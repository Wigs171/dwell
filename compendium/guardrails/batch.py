"""Anthropic Message Batches API wrapper.

Batch requests get a flat 50% discount on input + output tokens, in
exchange for an async SLA (up to 24h in the docs; minutes in practice).
Stacks with prompt caching: a cached-read inside a batched request
costs `0.1 * 0.5 = 0.05x` the base input rate — so a well-structured
batch over a cached prefix approaches 95% off.

Where this fits in Compendium:

- **Eager figure transcription** (`pdf_image_extractor.describe_pdf_figures`):
  25+ independent Vision calls per dense paper. Already parallel via
  ThreadPoolExecutor — batch halves the cost, trades ~1-2 min of
  extra wall-clock at worst.

Where this does NOT fit:

- Anything inside an agent's REPL loop (Router, PageWriter, Explorer,
  Research) — each turn depends on the previous turn's output.
- Interactive queries the user is watching live.
- The Reviewer, since it already packs all pages into one messages.create
  call (batching one request is pointless).

Cost is recorded per successful response via the shared `CostTracker`
with `is_batch=True`, so the 0.5x multiplier is applied on the billing
side and shows up correctly in `.cost-history`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import anthropic

from compendium.guardrails.cost_tracker import CostTracker


log = logging.getLogger(__name__)


# Polling schedule defaults. Anthropic batches for this workload typically
# finish in 30-120s, so start fast and back off — fewer HTTP polls, no
# meaningful added latency when a batch runs long.
_INITIAL_POLL_INTERVAL = 5.0
_MAX_POLL_INTERVAL = 60.0
_DEFAULT_MAX_WAIT_SECONDS = 30 * 60  # 30 min; figure batches never need this long


class BatchTimeout(Exception):
    """Raised when a batch exceeds its max-wait deadline."""


def submit_batch(
    client: anthropic.Anthropic,
    cost_tracker: CostTracker,
    model: str,
    requests: list[dict[str, Any]],
    *,
    poll_interval: float = _INITIAL_POLL_INTERVAL,
    max_wait_seconds: int = _DEFAULT_MAX_WAIT_SECONDS,
    is_sub_call: bool = True,
    progress_cb: "callable | None" = None,
) -> dict[str, dict[str, Any]]:
    """Submit a batch of message requests and block until all complete.

    `requests` is a list of `{"custom_id": str, "params": {...}}` dicts
    where `params` matches the kwargs of `client.messages.create` — i.e.
    `{"model": ..., "max_tokens": ..., "messages": [...]}`. Caller is
    responsible for making `custom_id` unique within the batch.

    Returns a dict keyed by `custom_id` with one of:
      `{"type": "succeeded", "text": str, "message": Message}`
      `{"type": "errored", "error": str}`
      `{"type": "expired" | "canceled"}`

    Cost for each succeeded response is recorded against `cost_tracker`
    with `is_batch=True`. Failed or expired results are not billed.
    `progress_cb(n_done, n_total)` is called after each poll when the
    status changes, for CLI progress reporting.
    """
    if not requests:
        return {}

    # OpenAI-compatible (non-Anthropic) clients have no Batches API. Run the
    # requests sequentially via messages.create — same return shape, no batch
    # discount. Keeps multi-provider ingest working (figure transcription etc.).
    if getattr(client, "is_compat", False) or not hasattr(client.messages, "batches"):
        out: dict[str, dict[str, Any]] = {}
        for req in requests:
            cid = req.get("custom_id", "")
            try:
                msg = client.messages.create(**req.get("params", {}))
                text = msg.content[0].text if (msg.content and hasattr(msg.content[0], "text")) else ""
                usage = msg.usage
                cost_tracker.record_call(
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    model=model, is_sub_call=is_sub_call,
                    cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                )
                out[cid] = {"type": "succeeded", "text": text, "message": msg}
            except Exception as exc:                 # noqa: BLE001
                out[cid] = {"type": "errored", "error": str(exc)}
            if progress_cb is not None:
                progress_cb(len(out), len(requests))
        return out

    batch = client.messages.batches.create(requests=requests)
    batch_id = batch.id
    log.debug("submitted batch %s with %d requests", batch_id, len(requests))

    deadline = time.monotonic() + max_wait_seconds
    interval = poll_interval
    last_done = -1
    while True:
        status = client.messages.batches.retrieve(batch_id)
        counts = getattr(status, "request_counts", None)
        if counts is not None and progress_cb is not None:
            # Expose processing progress when caller wants it. Succeeded
            # + errored + expired + canceled all count as "done" for UX.
            done = sum(
                getattr(counts, k, 0) or 0
                for k in ("succeeded", "errored", "expired", "canceled")
            )
            if done != last_done:
                progress_cb(done, len(requests))
                last_done = done
        if status.processing_status == "ended":
            break
        if time.monotonic() > deadline:
            try:
                client.messages.batches.cancel(batch_id)
            except Exception as exc:  # pragma: no cover — defensive
                log.debug("batch cancel failed for %s: %s", batch_id, exc)
            raise BatchTimeout(
                f"batch {batch_id} not complete after {max_wait_seconds}s "
                f"(status={status.processing_status})"
            )
        time.sleep(interval)
        interval = min(interval * 1.5, _MAX_POLL_INTERVAL)

    out: dict[str, dict[str, Any]] = {}
    for result in client.messages.batches.results(batch_id):
        cid = result.custom_id
        rtype = result.result.type
        if rtype == "succeeded":
            msg = result.result.message
            text = ""
            if msg.content and hasattr(msg.content[0], "text"):
                text = msg.content[0].text
            usage = msg.usage
            cost_tracker.record_call(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                model=model,
                is_sub_call=is_sub_call,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                is_batch=True,
            )
            out[cid] = {"type": "succeeded", "text": text, "message": msg}
        elif rtype == "errored":
            err = result.result.error
            out[cid] = {"type": "errored", "error": str(err)}
        else:
            # "expired" or "canceled" — neither bills tokens
            out[cid] = {"type": rtype}
    return out
