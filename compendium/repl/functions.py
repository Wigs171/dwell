"""
Built-in REPL functions available to agents during code execution.

These are factory functions that create closures with the necessary
clients and trackers baked in. The returned callables are registered
in the REPL namespace.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import anthropic
import httpx

from compendium.guardrails.cost_tracker import CostTracker
from compendium.models import GuardrailConfig


# Max depth for nested rlm_query chains. Per Zhang 2025, recursion depth
# is bounded only by token budget in principle; in practice the tail-cost
# asymmetry called out in hierarchical-agent-workflow.md means we cap
# hard. Depth 0 = root; depth 3 allows root -> grandchild -> great-grandchild.
MAX_RLM_DEPTH = 3

# Default max parallelism for llm_query_many. Keeps API rate usage sane;
# the paper's parallel fan-out examples are 2-5 way.
DEFAULT_FANOUT = 5


def make_llm_query_fn(
    client: anthropic.Anthropic,
    model: str,
    cost_tracker: CostTracker,
    max_chars: int = 500_000,
) -> Callable[[str, int], str]:
    """
    Create the llm_query() function for the REPL.
    This is the sub-LLM call from the RLM paper.
    """

    def llm_query(prompt: str, max_tokens: int = 4096) -> str:
        """Query a sub-LLM. The prompt should be a complete, self-contained request."""
        # Truncate prompt if it exceeds budget
        if len(prompt) > max_chars:
            prompt = prompt[:max_chars] + "\n\n[TRUNCATED]"

        cost_tracker.check_budget()

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        cost_tracker.record_call(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            is_sub_call=True,
        )

        return response.content[0].text

    return llm_query


def make_remaining_budget_fn(
    cost_tracker: CostTracker,
) -> Callable[[], dict[str, Any]]:
    """
    Create remaining_budget() for the REPL.

    Per the RLM paper, the root model should reason about remaining
    token budget when deciding how wide to fan out sub-calls. Today the
    agent only *hits* the budget (BudgetExceeded), it can't query it.
    This function returns a snapshot the model can use in code:

        b = remaining_budget()
        if b['remaining_usd'] > 0.50:
            # fan out wide
            results = llm_query_many([...])
        else:
            # one focused call
            result = llm_query(...)
    """

    def remaining_budget() -> dict[str, Any]:
        summary = cost_tracker.get_summary()
        gr = cost_tracker.guardrails
        spent = summary["estimated_cost_usd"]
        budget = gr.max_cost_dollars
        sub_used = summary["total_sub_calls"]
        sub_cap = gr.max_total_sub_calls
        return {
            "spent_usd": round(spent, 4),
            "budget_usd": round(budget, 4),
            "remaining_usd": round(max(0.0, budget - spent), 4),
            "percent_used": round(100 * spent / budget, 1) if budget else 0.0,
            "sub_calls_used": sub_used,
            "sub_calls_remaining": max(0, sub_cap - sub_used),
        }

    return remaining_budget


def make_llm_query_many_fn(
    client: anthropic.Anthropic,
    model: str,
    cost_tracker: CostTracker,
    max_chars: int = 500_000,
    max_workers: int = DEFAULT_FANOUT,
) -> Callable[[list[str], int], list[str]]:
    """
    Create llm_query_many() for parallel fan-out.

    Paper reference (sub-lm-invocation.md): "Parallel fan-out — issue
    multiple independent child calls whose results are combined by the
    root." The canonical book-QA pattern splits a novel into chapters
    and fires independent llm_query() calls, each returning a factual
    summary string.

    This is the sync version using a ThreadPoolExecutor (the Anthropic
    sync client is thread-safe). Results come back in the same order as
    the prompts so callers can zip(prompts, results) reliably.

    Usage inside the REPL:

        chapter_qs = [f"In chapter {i}, find ..." for i in range(10)]
        answers = llm_query_many(chapter_qs)
        final = llm_query(f"Given these findings: {answers}, ...")
    """

    def _one(prompt: str, max_tokens: int) -> str:
        if len(prompt) > max_chars:
            prompt = prompt[:max_chars] + "\n\n[TRUNCATED]"
        cost_tracker.check_budget()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        cost_tracker.record_call(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            is_sub_call=True,
        )
        return response.content[0].text

    def llm_query_many(
        prompts: list[str], max_tokens: int = 4096
    ) -> list[str]:
        """Dispatch many sub-LLM queries in parallel. Order-preserving.

        If a single call fails, its slot contains an `[ERROR ...]` string
        rather than raising — the caller can detect and retry selectively.
        """
        if not prompts:
            return []
        # Pre-flight budget check so we fail fast before spawning workers.
        cost_tracker.check_budget()
        results: list[str] = [""] * len(prompts)
        with ThreadPoolExecutor(max_workers=min(max_workers, len(prompts))) as pool:
            futures = {
                pool.submit(_one, p, max_tokens): i
                for i, p in enumerate(prompts)
            }
            for fut in futures:
                i = futures[fut]
                try:
                    results[i] = fut.result()
                except Exception as exc:
                    results[i] = f"[ERROR] llm_query_many[{i}] failed: {exc}"
        return results

    return llm_query_many


def make_partition_fn() -> Callable[[str, int, int], list[str]]:
    """
    Create partition() — map-reduce primitive for splitting long inputs.

    Paper reference (map-reduce-over-context.md): RLMs "spontaneously
    discover" map-reduce when given primitives like
    `partition(text, chunk_size)`. Exposing this as a named function
    prompts the agent toward chunk-delegation on dense sources instead
    of tripping the hidden 500k-char llm_query truncation or over-
    stuffing a single child context.

    Chunks are whitespace-aligned where possible (falls back to hard
    split if no whitespace in window) and may optionally overlap so
    information spanning a boundary isn't lost.

        chunks = partition(source_content, chunk_size=40000, overlap=500)
        per_chunk = llm_query_many([f"Extract entities from: {c}" for c in chunks])
    """

    def partition(
        text: str, chunk_size: int = 40_000, overlap: int = 0
    ) -> list[str]:
        """Split `text` into chunks of at most `chunk_size` chars.

        `overlap` (chars) duplicates trailing content at each boundary
        to mitigate boundary-artefact loss for cross-chunk references.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be in [0, chunk_size)")
        if not text:
            return []
        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        i = 0
        n = len(text)
        step = chunk_size - overlap
        while i < n:
            end = min(i + chunk_size, n)
            # Align to whitespace when we're not at the tail, so we
            # don't chop words in half.
            if end < n:
                # Search backwards for a break point in the last 10%.
                search_from = max(i + int(chunk_size * 0.9), i + 1)
                break_at = text.rfind("\n", search_from, end)
                if break_at < 0:
                    break_at = text.rfind(" ", search_from, end)
                if break_at > 0:
                    end = break_at
            chunks.append(text[i:end])
            if end >= n:
                break
            i = end - overlap if overlap else end
            if i <= 0:
                i = end
        return chunks

    return partition


def make_rlm_query_fn(
    client: anthropic.Anthropic,
    model: str,
    cost_tracker: CostTracker,
    guardrails: GuardrailConfig,
    parent_depth: int = 0,
) -> Callable[..., str]:
    """
    Create rlm_query() — recursive child RLM (paper's nested sub-LM call).

    Paper reference (sub-lm-invocation.md §Recursive Nesting): "Child
    calls may themselves issue further sub-LM invocations, creating a
    call tree bounded only by token budget and depth limits."

    Today's `llm_query` is a flat one-shot call. `rlm_query` spawns a
    child REPL agent: the child receives the prompt, gets its own fresh
    REPL with llm_query / llm_query_many / partition / rlm_query
    (depth-capped) / remaining_budget registered, runs its own loop,
    and returns the string it FINAL_VAR'd.

    This is the mechanism the vault's `recursive-language-model.md`
    describes as core to RLMs — without it, there is no recursion, only
    one layer of delegation.

    Usage:

        # Root agent:
        summary = rlm_query(
            "Summarize this chapter's treatment of the ring motif.",
            context={"chapter_text": chapters[2]},
            max_iterations=8,
        )
    """

    def rlm_query(
        prompt: str,
        *,
        context: dict[str, Any] | None = None,
        max_iterations: int = 10,
        system: str | None = None,
    ) -> str:
        """Spawn a bounded child RLM and return its FINAL result."""
        # Late import to avoid a circular dep (base.py imports this module).
        from compendium.repl.rlm_child import RLMChild

        if parent_depth + 1 > MAX_RLM_DEPTH:
            return (
                f"[RLM DEPTH CAP] max depth {MAX_RLM_DEPTH} reached "
                f"(parent at depth {parent_depth}); falling back to flat "
                "llm_query would be appropriate here. Caller should "
                "synthesize from the data it already has."
            )
        cost_tracker.check_budget()

        child = RLMChild(
            client=client,
            model=model,
            cost_tracker=cost_tracker,
            guardrails=guardrails,
            depth=parent_depth + 1,
            max_iterations=max_iterations,
            system_prompt=system,
        )
        try:
            result = child.run(prompt=prompt, context=context or {})
        except Exception as exc:
            return f"[RLM ERROR] child at depth {parent_depth + 1} failed: {exc}"
        # Coerce to string — the parent REPL just wants a text return.
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, default=str)
        except Exception:
            return str(result)

    return rlm_query


def make_async_llm_query_fn(
    client: anthropic.AsyncAnthropic,
    model: str,
    cost_tracker: CostTracker,
    max_chars: int = 500_000,
) -> Callable:
    """Async variant for parallel sub-calls within the REPL."""

    async def llm_query_async(prompt: str, max_tokens: int = 4096) -> str:
        if len(prompt) > max_chars:
            prompt = prompt[:max_chars] + "\n\n[TRUNCATED]"

        cost_tracker.check_budget()

        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        await cost_tracker.record_call_async(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            is_sub_call=True,
        )

        return response.content[0].text

    return llm_query_async


def make_web_search_fn(
    provider: str,
    api_key: str,
    jina_api_key: str = "",
) -> Callable[[str, int], list[dict[str, str]]]:
    """
    Create web_search() function for the REPL.
    Returns structured search results [{title, snippet, url}, ...].

    If `provider` is "none"/empty but `jina_api_key` is set, falls
    through to Jina Search — a free-tier-friendly backend. This lets
    citation verification run at lint time without explicit provider
    config, using the same key already set for `fetch_url` (r.jina.ai).
    """

    def web_search_tavily(query: str, num_results: int = 5) -> list[dict[str, str]]:
        """Search the web using Tavily API."""
        try:
            from tavily import TavilyClient

            tavily = TavilyClient(api_key=api_key)
            response = tavily.search(query=query, max_results=num_results)
            return [
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("content", "")[:1500],
                    "url": r.get("url", ""),
                }
                for r in response.get("results", [])
            ]
        except ImportError:
            return [{"error": "tavily-python not installed. pip install tavily-python"}]
        except Exception as e:
            return [{"error": f"Search failed: {e}"}]

    def web_search_brave(query: str, num_results: int = 5) -> list[dict[str, str]]:
        """Search the web using Brave Search API."""
        try:
            resp = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": num_results},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for r in data.get("web", {}).get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("description", "")[:1500],
                    "url": r.get("url", ""),
                })
            return results or [{"title": "No results", "snippet": "", "url": ""}]
        except Exception as e:
            return [{"error": f"Brave search failed: {e}"}]

    def web_search_jina(query: str, num_results: int = 5) -> list[dict[str, str]]:
        """Search via Jina Search (s.jina.ai). Uses `jina_api_key` or
        the `api_key` parameter for auth.
        """
        key = jina_api_key or api_key
        try:
            headers = {"Accept": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            resp = httpx.post(
                "https://s.jina.ai/",
                json={"q": query},
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data") or data.get("results") or []
            if not isinstance(items, list):
                items = []
            out: list[dict[str, str]] = []
            for r in items[:num_results]:
                if not isinstance(r, dict):
                    continue
                out.append({
                    "title": r.get("title", ""),
                    "snippet": (r.get("description") or r.get("content", ""))[:1500],
                    "url": r.get("url", ""),
                })
            return out or [{"title": "No Jina results", "snippet": "", "url": ""}]
        except Exception as e:
            return [{"error": f"Jina search failed: {e}"}]

    def web_search_none(query: str, num_results: int = 5) -> list[dict[str, str]]:
        """Placeholder when no search provider is configured."""
        return [
            {
                "title": "No search provider configured",
                "snippet": (
                    "Set COMPENDIUM_SEARCH_PROVIDER and COMPENDIUM_SEARCH_API_KEY "
                    "to enable web search. Supported: 'tavily', 'brave', 'jina'."
                ),
                "url": "",
            }
        ]

    providers = {
        "tavily": web_search_tavily,
        "brave": web_search_brave,
        "jina": web_search_jina,
        "none": web_search_none,
    }

    # Fallback precedence when no explicit provider:
    # 1. jina_api_key set → use Jina (free-tier friendly, same key as fetch_url)
    # 2. otherwise → "none" placeholder
    if provider in (None, "", "none"):
        if jina_api_key:
            return web_search_jina
        return web_search_none
    return providers.get(provider, web_search_none)


def _html_to_markdown(html: str, max_chars: int = 50_000) -> str:
    """Convert HTML to structured markdown preserving headings, paragraphs, lists, and quotes."""
    import re

    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # Convert headings
    for i in range(1, 7):
        text = re.sub(
            rf"<h{i}[^>]*>(.*?)</h{i}>",
            lambda m, lvl=i: f"\n{'#' * lvl} {m.group(1).strip()}\n",
            text, flags=re.DOTALL | re.IGNORECASE,
        )

    # Convert blockquotes
    text = re.sub(
        r"<blockquote[^>]*>(.*?)</blockquote>",
        lambda m: "\n> " + m.group(1).strip().replace("\n", "\n> ") + "\n",
        text, flags=re.DOTALL | re.IGNORECASE,
    )

    # Convert list items
    text = re.sub(
        r"<li[^>]*>(.*?)</li>",
        lambda m: f"- {m.group(1).strip()}",
        text, flags=re.DOTALL | re.IGNORECASE,
    )

    # Paragraphs and breaks
    text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Bold and italic
    text = re.sub(r"<(strong|b)[^>]*>(.*?)</\1>", r"**\2**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(em|i)[^>]*>(.*?)</\1>", r"*\2*", text, flags=re.DOTALL | re.IGNORECASE)

    # Strip remaining tags and clean whitespace
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r" +", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


# Hoisted to module scope so the Batch-API figure-transcription path
# (pdf_image_extractor._describe_via_batch) produces descriptions that
# match what the REPL view_image() would produce for the same figure.
#
# Prompt design — three jobs in one call:
#  1. Triage — classify as DECORATIVE (photo / banner / sample image /
#     ornamental gradient) or SUBSTANTIVE (chart / diagram / equation /
#     code). One call instead of two; cost stays flat.
#  2. Cap length on DECORATIVE — single-line `[DECORATIVE] ...` reply
#     so a banner doesn't get a 400-word "no axes, no legend" essay.
#  3. Ban absence-listing — small vision models (Gemma 4 in particular)
#     default to enumerating what's missing when a "describe this chart"
#     prompt meets a non-chart. Explicit prohibition of absence phrases
#     fixes this.
_DEFAULT_VIEW_IMAGE_PROMPT = (
    "You are transcribing one figure from a research PDF for a "
    "technical wiki.\n"
    "\n"
    "STEP 1 — classify the image as one of:\n"
    "- DECORATIVE: photographs, dataset sample images, decorative "
    "gradients or color fields, page banners, ornamental flourishes, "
    "scenic/biological/object photos. Anything that conveys NO "
    "technical, quantitative, or symbolic content.\n"
    "- SUBSTANTIVE: charts, plots, diagrams, schematics, equations, "
    "code, pseudocode, algorithms, tables, attention maps with labels, "
    "architectural figures. Anything that conveys a technical claim.\n"
    "\n"
    "STEP 2A — if DECORATIVE, output EXACTLY one line:\n"
    "[DECORATIVE] <≤12-word phrase naming the subject>\n"
    "Then stop. Examples:\n"
    "[DECORATIVE] Two golden retrievers in a field — dataset sample.\n"
    "[DECORATIVE] Magenta-to-cyan gradient banner.\n"
    "[DECORATIVE] Macaw perched on a branch — ImageNet sample.\n"
    "Do NOT identify species in Latin, estimate RGB values, list what "
    "is missing, or speculate about provenance.\n"
    "\n"
    "STEP 2B — if SUBSTANTIVE, transcribe densely. Cover only what is "
    "visibly present:\n"
    "- axis labels, units, numeric values, ratios, ranges\n"
    "- structural elements that ARE present (boxes, arrows, legends, "
    "groupings)\n"
    "- text inside the figure (titles, annotations, captions)\n"
    "- the relationship or claim being illustrated\n"
    "- code, pseudocode, equations, and command snippets transcribed "
    "VERBATIM in fenced code blocks (```python, ```bash, ```math, or "
    "plain ``` as appropriate). Preserve variable names, literals, "
    "operators, and whitespace exactly. If multiple cells are shown "
    "(In[1], Out[2], etc.), transcribe each in order, labeled.\n"
    "\n"
    "RULES (apply to both branches):\n"
    "- Describe only what is visibly present. NEVER enumerate absent "
    "elements. Banned phrases include: \"no axes\", \"no legend\", "
    "\"no annotations\", \"no equations\", \"no code\", \"no formal "
    "notation\", \"absence of\", \"lacks\", \"does not contain\".\n"
    "- No preamble (\"Based on the image…\", \"This figure shows…\"). "
    "Start with content.\n"
    "- No meta commentary about your analysis or confidence.\n"
    "- No speculation beyond what is visible."
)


def make_view_image_fn(
    provider=None,
    *,
    client: "anthropic.Anthropic | None" = None,
    model: str | None = None,
    cost_tracker: "CostTracker | None" = None,
    max_description_chars: int = 6_000,
) -> Callable[[str, str], str]:
    """Create view_image(path, prompt=...) for the REPL.

    Reads a PNG/JPEG/WebP/GIF from disk, sends it to the configured
    `VisionProvider` (Claude Vision by default; local Gemma 4 via Ollama
    when `vision_provider='ollama'`), and returns a dense technical
    description the REPL agent can incorporate into page writes or
    routing decisions.

    Two call styles are supported:

    - Pass a `provider` directly (new style).
    - Pass `client + model + cost_tracker` (legacy style) — the function
      constructs an `AnthropicVisionProvider` from them. This keeps
      older code paths working while the migration is in flight.

    Every call is counted against the cost tracker as a sub-call, so
    the global budget still governs local-model calls too (though at
    $0 rates, so only sub-call count caps apply).

    Error cases are returned as `[IMAGE ...]` strings so the REPL
    doesn't crash — the agent can decide to skip or try another image.
    """
    from compendium.sources.vision_provider import (
        AnthropicVisionProvider,
        load_image,
    )

    if provider is None:
        if client is None or model is None or cost_tracker is None:
            raise TypeError(
                "make_view_image_fn needs either a `provider` or "
                "`(client, model, cost_tracker)` for the legacy path"
            )
        provider = AnthropicVisionProvider(
            client=client, model=model, cost_tracker=cost_tracker,
        )

    def view_image(path: str, prompt: str | None = None) -> str:
        """View an image and return a dense technical description."""
        loaded = load_image(path)
        if isinstance(loaded, str):
            return loaded
        image_bytes, media_type = loaded
        prompt_text = (prompt or _DEFAULT_VIEW_IMAGE_PROMPT).strip()
        text = provider.describe(image_bytes, media_type, prompt_text)
        return text[:max_description_chars]

    return view_image


def make_fetch_url_fn(
    max_chars: int = 50_000,
    jina_api_key: str = "",
) -> Callable[[str, int], str]:
    """Create fetch_url() to retrieve a page's content as markdown.

    Resolution order:
      1. Jina Reader (https://r.jina.ai/<url>) — browser-rendered, clean
         markdown. Uses `jina_api_key` if provided (higher rate limits).
      2. Plain httpx + regex-based HTML-to-markdown — fallback when Jina
         is unreachable or returns an error.

    Jina renders JavaScript-heavy pages that our regex fetcher can't.
    """

    def _fetch_via_jina(url: str, limit: int) -> str | None:
        headers = {
            "User-Agent": "CompendiumBuilder/0.1 (research agent)",
            "Accept": "text/plain",
            "X-With-Links-Summary": "true",
        }
        if jina_api_key:
            headers["Authorization"] = f"Bearer {jina_api_key}"
        try:
            with httpx.Client(timeout=60, follow_redirects=True) as http:
                resp = http.get(
                    f"https://r.jina.ai/{url}",
                    headers=headers,
                )
                resp.raise_for_status()
                text = resp.text
                if not text or text.strip().startswith("<"):
                    return None
                return text[:limit]
        except Exception:
            return None

    def _fetch_via_regex(url: str, limit: int) -> str:
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as http:
                resp = http.get(
                    url,
                    headers={
                        "User-Agent": "CompendiumBuilder/0.1 (research agent)"
                    },
                )
                resp.raise_for_status()
                return _html_to_markdown(resp.text, max_chars=limit)
        except Exception as e:
            return f"[FETCH ERROR] {e}"

    def fetch_url(url: str, char_limit: int | None = None) -> str:
        """Fetch a URL and return its content as structured markdown."""
        limit = char_limit or max_chars
        jina = _fetch_via_jina(url, limit)
        if jina is not None and not jina.strip().startswith("[FETCH ERROR]"):
            return jina
        return _fetch_via_regex(url, limit)

    return fetch_url


def make_deep_search_fn(
    search_fn: Callable,
    fetch_fn: Callable,
) -> Callable[[str, int], list[dict[str, Any]]]:
    """
    Create deep_search() that chains web_search + fetch_url for richer results.
    Returns [{title, url, snippet, body_excerpt, headings}] with actual page content.
    """

    def deep_search(
        query: str, num_results: int = 3
    ) -> list[dict[str, Any]]:
        """Search the web and auto-fetch the top results for deeper content."""
        import re

        results = search_fn(query, num_results=num_results)
        enriched = []

        for r in results:
            if "error" in r or not r.get("url"):
                enriched.append(r)
                continue

            url = r["url"]
            try:
                body = fetch_fn(url, char_limit=3000)
                headings = re.findall(r"^#{1,4} (.+)$", body, re.MULTILINE)
                enriched.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": r.get("snippet", ""),
                    "body_excerpt": body[:2000],
                    "headings": headings[:10],
                })
            except Exception:
                enriched.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": r.get("snippet", ""),
                    "body_excerpt": "",
                    "headings": [],
                })

        return enriched

    return deep_search
