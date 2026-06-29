"""Vision backend abstraction for figure transcription + OCR.

`describe_pdf_figures` and `make_view_image_fn` both boil down to the
same operation: given `(image_bytes, media_type, prompt)`, return a
dense technical description. Until now that operation always went to
Claude Vision. This module introduces a `VisionProvider` seam so the
same call sites can route to a local Gemma 4 model via Ollama at $0
marginal cost.

Rationale is recorded in `memory/`: code-heavy figures and printed-text
OCR don't need Claude's capacity — Gemma 4 E4B (4.5B effective params,
native vision, Apache 2.0) handles them fine and unblocks long-book
OCR where Claude Vision was cost-prohibitive.

Providers:

- **`AnthropicVisionProvider`**: the pre-existing behavior — single
  `messages.create` calls in `describe`, Message Batches API in
  `describe_many` (50% off, stacks with prompt cache).
- **`OllamaVisionProvider`**: POSTs to a local Ollama `/api/chat`
  endpoint with the image as base64. `describe_many` is a ThreadPool
  over individual `describe` calls — the local GPU batches at the
  kernel level, so there's no separate batch API to exploit.

Both return `str` descriptions. Error strings are prefixed `[...]` so
the existing REPL/caller conventions keep working without changes.
"""

from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import httpx

from compendium.guardrails.cost_tracker import CostTracker


log = logging.getLogger(__name__)


# Claude Vision hard cap; we also clamp local-provider inputs to the
# same limit so a swap between providers never surprises the caller
# with differently-sized image failures.
IMAGE_LIMIT_BYTES = 5 * 1024 * 1024

IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass
class VisionRequest:
    """One image + prompt pair."""

    image_bytes: bytes
    media_type: str
    prompt: str
    custom_id: str = ""


class VisionProvider(Protocol):
    """Describe images with a prompt. All providers follow this contract."""

    name: str
    model: str

    def describe(
        self,
        image_bytes: bytes,
        media_type: str,
        prompt: str,
        *,
        max_tokens: int = 1500,
    ) -> str:
        """Describe one image. Return description text or `[...]` error."""
        ...

    def describe_many(
        self,
        requests: list[VisionRequest],
        *,
        max_tokens: int = 1500,
        max_workers: int = 8,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> list[str]:
        """Describe N images in order. Returns a list aligned 1:1 with `requests`."""
        ...


# ---------------------------------------------------------------------------
# Anthropic (Claude Vision) provider
# ---------------------------------------------------------------------------


class AnthropicVisionProvider:
    """Claude Vision backend. Batches via Message Batches API for 50% off."""

    name = "anthropic"

    def __init__(
        self,
        client,
        model: str,
        cost_tracker: CostTracker,
        *,
        use_batch: bool = True,
        batch_min_size: int = 3,
        batch_max_wait_seconds: int = 1800,
    ):
        self.client = client
        self.model = model
        self.cost_tracker = cost_tracker
        self.use_batch = use_batch
        self.batch_min_size = batch_min_size
        self.batch_max_wait_seconds = batch_max_wait_seconds

    def describe(
        self,
        image_bytes: bytes,
        media_type: str,
        prompt: str,
        *,
        max_tokens: int = 1500,
    ) -> str:
        if len(image_bytes) > IMAGE_LIMIT_BYTES:
            return (
                f"[IMAGE TOO LARGE] {len(image_bytes):,} bytes > "
                f"{IMAGE_LIMIT_BYTES:,} (Anthropic cap)."
            )
        encoded = base64.standard_b64encode(image_bytes).decode("ascii")
        try:
            self.cost_tracker.check_budget()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": encoded,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            self.cost_tracker.record_call(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=self.model,
                is_sub_call=True,
            )
        except Exception as exc:
            return f"[IMAGE API ERROR] {exc}"

        if not response.content:
            return "[IMAGE API ERROR] empty response"
        content0 = response.content[0]
        return content0.text if hasattr(content0, "text") else ""

    def describe_many(
        self,
        requests: list[VisionRequest],
        *,
        max_tokens: int = 1500,
        max_workers: int = 8,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> list[str]:
        if not requests:
            return []

        if self.use_batch and len(requests) >= self.batch_min_size:
            try:
                return self._describe_many_batch(
                    requests, max_tokens=max_tokens, progress_cb=progress_cb,
                )
            except Exception as exc:
                log.warning(
                    "batch vision path failed (%s); falling back to "
                    "parallel messages.create", exc,
                )

        return self._describe_many_threadpool(
            requests, max_tokens=max_tokens, max_workers=max_workers,
        )

    def _describe_many_threadpool(
        self,
        requests: list[VisionRequest],
        *,
        max_tokens: int,
        max_workers: int,
    ) -> list[str]:
        results: list[str] = [""] * len(requests)
        workers = min(max_workers, len(requests))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(
                    self.describe,
                    r.image_bytes,
                    r.media_type,
                    r.prompt,
                    max_tokens=max_tokens,
                ): i
                for i, r in enumerate(requests)
            }
            for fut in as_completed(future_to_idx):
                i = future_to_idx[fut]
                try:
                    results[i] = fut.result()
                except Exception as exc:
                    results[i] = f"[IMAGE API ERROR] {exc}"
        return results

    def _describe_many_batch(
        self,
        requests: list[VisionRequest],
        *,
        max_tokens: int,
        progress_cb: Callable[[int, int], None] | None,
    ) -> list[str]:
        from compendium.guardrails.batch import submit_batch

        batch_requests: list[dict] = []
        cid_to_idx: dict[str, int] = {}
        for i, r in enumerate(requests):
            cid = f"vis-{i:04d}"
            cid_to_idx[cid] = i
            if len(r.image_bytes) > IMAGE_LIMIT_BYTES:
                continue
            encoded = base64.standard_b64encode(r.image_bytes).decode("ascii")
            batch_requests.append({
                "custom_id": cid,
                "params": {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": r.media_type,
                                        "data": encoded,
                                    },
                                },
                                {"type": "text", "text": r.prompt},
                            ],
                        }
                    ],
                },
            })

        results: list[str] = [""] * len(requests)
        # Mark oversized images before the batch runs so they don't come
        # back blank-looking to the caller.
        for i, r in enumerate(requests):
            if len(r.image_bytes) > IMAGE_LIMIT_BYTES:
                results[i] = (
                    f"[IMAGE TOO LARGE] {len(r.image_bytes):,} bytes > "
                    f"{IMAGE_LIMIT_BYTES:,}"
                )

        if not batch_requests:
            return results

        batch_results = submit_batch(
            client=self.client,
            cost_tracker=self.cost_tracker,
            model=self.model,
            requests=batch_requests,
            max_wait_seconds=self.batch_max_wait_seconds,
            is_sub_call=True,
            progress_cb=progress_cb,
        )

        for cid, res in batch_results.items():
            i = cid_to_idx.get(cid)
            if i is None:
                continue
            if res["type"] == "succeeded":
                results[i] = (res.get("text") or "")[:6000]
            else:
                results[i] = (
                    f"[IMAGE API ERROR] {res['type']}: {res.get('error', '')}"
                )
        return results


# ---------------------------------------------------------------------------
# Ollama (local Gemma 4) provider
# ---------------------------------------------------------------------------


class OllamaVisionProvider:
    """Local Gemma 4 via Ollama's `/api/chat` endpoint. Zero marginal cost.

    Ollama serves vision-capable Gemma at a standard chat endpoint where
    images ride on the message as a list of base64 strings. We POST one
    request per image and parallelize via a threadpool — the local GPU
    batches at the kernel level, so there's no separate batch API to
    exploit the way Anthropic has one.

    The cost_tracker still receives a `record_call` — at $0/$0 rates
    (see MODEL_COSTS in cost_tracker) — so the telemetry pane in
    `get_summary()` shows how many local-model calls ran, which is
    useful when debugging "why was this ingest fast/cheap".
    """

    name = "ollama"

    def __init__(
        self,
        model: str,
        cost_tracker: CostTracker,
        *,
        endpoint: str = "http://localhost:11434",
        request_timeout: float = 300.0,
    ):
        self.model = model
        self.cost_tracker = cost_tracker
        self.endpoint = endpoint.rstrip("/")
        self.request_timeout = request_timeout

    # Ollama has no fixed image-size limit; this is just a sanity cap so
    # we don't pump a 100 MB scan render through the local server. Set
    # well above the Anthropic 5 MB cap so embedded high-res figures
    # (e.g. attention-map composites in vision papers) don't get
    # silently dropped just because we're behaviorally mirroring the
    # Anthropic provider.
    OLLAMA_IMAGE_LIMIT_BYTES = 50 * 1024 * 1024

    # Empirically, Gemma 4 E4B Q4_K_M on a VRAM-constrained machine
    # silently emits 0 chars when the input image is large enough to
    # crowd its vision token budget — anything over ~2 MB raw PNG is
    # the danger zone. We downscale above this threshold to
    # `MAX_DOWNSCALE_DIM` on the longest side, preserving aspect.
    # Below threshold we ship the original bytes untouched, since
    # diagrams with small text are sensitive to resampling artifacts.
    DOWNSCALE_THRESHOLD_BYTES = 2 * 1024 * 1024
    MAX_DOWNSCALE_DIM = 1600

    def _maybe_downscale(self, image_bytes: bytes) -> bytes:
        """Downscale a too-large image so Gemma's vision context can hold it.

        Returns the original bytes unchanged when below the threshold.
        Falls back to the original on any Pillow error so a single bad
        image can't break the whole batch.
        """
        if len(image_bytes) <= self.DOWNSCALE_THRESHOLD_BYTES:
            return image_bytes
        try:
            from io import BytesIO
            from PIL import Image
        except ImportError:
            return image_bytes
        try:
            img = Image.open(BytesIO(image_bytes))
            img.load()
            w, h = img.size
            longest = max(w, h)
            if longest <= self.MAX_DOWNSCALE_DIM:
                # Already within dimensions — bytes are big because of
                # bit depth / alpha rather than resolution. Re-encoding
                # as PNG with default optimize settings often shrinks
                # 30-50% with no visual loss.
                buf = BytesIO()
                img.save(buf, format="PNG", optimize=True)
                out = buf.getvalue()
            else:
                img.thumbnail(
                    (self.MAX_DOWNSCALE_DIM, self.MAX_DOWNSCALE_DIM),
                    Image.LANCZOS,
                )
                buf = BytesIO()
                img.save(buf, format="PNG", optimize=True)
                out = buf.getvalue()
            log.info(
                "vision: downscaled %d×%d / %.1f MB → %d×%d / %.1f MB",
                w, h, len(image_bytes) / 1024 / 1024,
                img.size[0], img.size[1], len(out) / 1024 / 1024,
            )
            return out
        except Exception as exc:
            log.debug("downscale failed (%s); sending original", exc)
            return image_bytes

    def describe(
        self,
        image_bytes: bytes,
        media_type: str,  # noqa: ARG002 — Ollama doesn't need media_type
        prompt: str,
        *,
        max_tokens: int = 1500,
    ) -> str:
        """One-call wrapper around `_describe_once` with a single retry
        when Gemma returns 0 chars.

        Empirically E4B is run-to-run variable on dense scientific
        figures — the same image can return 542 chars one call and 0
        chars the next. Retrying once costs ~22s and converts most
        of those flakes into successes.
        """
        if len(image_bytes) > self.OLLAMA_IMAGE_LIMIT_BYTES:
            return (
                f"[IMAGE TOO LARGE] {len(image_bytes):,} bytes > "
                f"{self.OLLAMA_IMAGE_LIMIT_BYTES:,} (ollama sanity cap)"
            )
        image_bytes = self._maybe_downscale(image_bytes)
        out = self._describe_once(image_bytes, prompt, max_tokens=max_tokens)
        if out or out is None:
            # Non-empty success or hard error string returned.
            return out or ""
        # Empty string — give it one more shot before giving up.
        log.debug("ollama returned 0 chars; retrying once")
        retry = self._describe_once(image_bytes, prompt, max_tokens=max_tokens)
        return retry or ""

    def _describe_once(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        max_tokens: int,
    ) -> str:
        encoded = base64.standard_b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [encoded],
                }
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        try:
            self.cost_tracker.check_budget()
            resp = httpx.post(
                f"{self.endpoint}/api/chat",
                json=payload,
                timeout=self.request_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            return f"[IMAGE API ERROR] ollama: {exc}"
        except Exception as exc:
            return f"[IMAGE API ERROR] {exc}"

        # Ollama usage counters are prompt_eval_count / eval_count.
        # Recorded at $0/$0 rates but tracked for telemetry.
        try:
            self.cost_tracker.record_call(
                input_tokens=int(data.get("prompt_eval_count", 0)),
                output_tokens=int(data.get("eval_count", 0)),
                model=self.model,
                is_sub_call=True,
            )
        except Exception:
            pass

        message = data.get("message") or {}
        text = message.get("content") or data.get("response") or ""
        return text

    def describe_many(
        self,
        requests: list[VisionRequest],
        *,
        max_tokens: int = 1500,
        max_workers: int = 4,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> list[str]:
        if not requests:
            return []
        results: list[str] = [""] * len(requests)
        # Lower default parallelism than Anthropic: a single consumer GPU
        # saturates at 2-4 concurrent Gemma generations; going higher just
        # queues them and hurts tail latency.
        workers = min(max_workers, len(requests))
        completed = 0
        total = len(requests)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(
                    self.describe,
                    r.image_bytes,
                    r.media_type,
                    r.prompt,
                    max_tokens=max_tokens,
                ): i
                for i, r in enumerate(requests)
            }
            for fut in as_completed(future_to_idx):
                i = future_to_idx[fut]
                try:
                    results[i] = fut.result()
                except Exception as exc:
                    results[i] = f"[IMAGE API ERROR] {exc}"
                completed += 1
                if progress_cb:
                    try:
                        progress_cb(completed, total)
                    except Exception:
                        pass
        return results


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_vision_provider(
    config,
    client,
    cost_tracker: CostTracker,
    *,
    model_override: str | None = None,
) -> VisionProvider:
    """Build the vision provider indicated by `config.vision_provider`.

    `model_override` lets callers force a specific vision model
    (e.g. cli.py uses the synthesis tier for OCR mode and mechanical
    tier for figure description — those decisions stay at the call
    site, we just receive the resolved model string).

    When the override is unset, we fall back to the config's
    `vision_model`, then to a sensible per-provider default.
    """
    provider_name = (getattr(config, "vision_provider", "anthropic") or "anthropic").lower()

    if provider_name == "ollama":
        model = (
            model_override
            or getattr(config, "vision_model", None)
            or "gemma4:e4b"
        )
        endpoint = getattr(config, "vision_endpoint", "") or "http://localhost:11434"
        return OllamaVisionProvider(
            model=model,
            cost_tracker=cost_tracker,
            endpoint=endpoint,
        )

    # Default: Anthropic. `model_override` wins, then vision_model, then
    # fall through to the caller's existing behavior where they pass an
    # explicit model string (which will be re-supplied via model_override).
    model = (
        model_override
        or getattr(config, "vision_model", None)
        or config.tiered_models.mechanical
    )
    return AnthropicVisionProvider(
        client=client,
        model=model,
        cost_tracker=cost_tracker,
    )


def load_image(path: str | Path) -> tuple[bytes, str] | str:
    """Read an image from disk into `(bytes, media_type)`.

    Returns an `[...]` error string on failure so callers can propagate
    it as-is without try/except. Shared between the REPL `view_image`
    path and the batch path so validation behavior matches.
    """
    p = Path(path)
    if not p.is_file():
        return f"[IMAGE NOT FOUND] {path}"
    media_type = IMAGE_MEDIA_TYPES.get(p.suffix.lower())
    if media_type is None:
        return f"[UNSUPPORTED IMAGE FORMAT] {p.suffix}"
    try:
        data = p.read_bytes()
    except OSError as exc:
        return f"[IMAGE READ ERROR] {exc}"
    return data, media_type
