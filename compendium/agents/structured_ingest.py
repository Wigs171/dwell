"""Structured (non-REPL) replacements for the RLM-REPL ingest agents.

The default ingest pipeline drives Router / PageWriter / Explorer as
recursive language models (a persistent REPL the model writes Python
into). That's powerful but expensive: every page is a multi-turn REPL
session. For the web "Learn" builds we want a cheap, deterministic path
that performs ONE `client.messages.create` per decision and parses JSON
out of the text — exactly the shape the Reviewer already uses.

This module provides drop-in structured replacements with the SAME
public method signatures as the REPL agents:

- `StructuredRouter.route(...)`     -> IngestPlan   (cf. IngestRouter)
- `StructuredPageWriter.write(...)` -> Page          (cf. PageWriter)
- `StructuredExplorer.explore()`    -> ExpansionReport (cf. Explorer)
- `structured_gather(...)`          -> list[Path]    (cf. ResearchAgent
                                       gather, but single-call + cheap)

Byte-compatibility: these reuse the REPL agents' parsers
(`IngestRouter._parse_plan` / `_ensure_source_change`,
`PageWriter._parse_page` / `related_pages`, Explorer's
`_parse_proposals_dict` / `_write_expansion_files`) and the same
`write_page` contract, so the on-disk output is identical in shape and
field order.

Multi-provider safe: only `client.messages.create(...)` is used, and
JSON is sliced out of the response text with the Reviewer's robust
approach (strip ```json fences, take the first `{` / last `}`). No
native tool-use, no FINAL_VAR.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from compendium.agents.explorer import (
    EXPLORER_PROMPT,
    _broken_for_repl,
    _parse_proposals_dict,
    _read_topic as _explorer_read_topic,
    _source_summaries_for_repl,
    _thin_for_repl,
    _write_empty_report,
    _write_expansion_files,
)
from compendium.agents.ingest_router import (
    INGEST_ROUTER_PROMPT,
    _estimate_tokens,
    _parse_plan,
    _read_vault_topic,
    _size_tier,
)
from compendium.agents.page_writer import (
    PAGE_WRITER_PROMPT,
    _parse_page,
    _read_page_for_repl,
    _related_pages_for_repl,
)
from compendium.config import CompendiumConfig
from compendium.guardrails.cost_tracker import CostTracker
from compendium.models import (
    ExpansionReport,
    IngestPlan,
    ModelTier,
    Page,
    PageChange,
    PageType,
)
from compendium.repl.functions import make_fetch_url_fn, make_web_search_fn
from compendium.vault import (
    VaultPaths,
    find_orphans,
    list_pages,
    read_page,
    read_recent,
    timestamp_iso,
)
from compendium.vault.pages import slugify
from compendium.vault.registry import IngestRegistry, RegistryEntry, now_iso


# Sources longer than this are deterministically chunked before the
# single structured call (Router extract-merge; Writer relevant-slice).
# Chosen so even chunked prompts stay well under provider context limits.
_LONG_SOURCE_CHARS = 45_000
_CHUNK_CHARS = 40_000
_CHUNK_OVERLAP = 400


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict:
    """Slice a JSON object out of an LLM response (Reviewer's approach).

    Strips ```json fences, then takes the substring from the first `{`
    to the last `}` and json.loads it. Returns {} on any failure so the
    caller's parser degrades gracefully rather than crashing the write.
    """
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        data = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _chunk(text: str, chunk_size: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Whitespace-aligned chunking (mirrors repl.functions.partition)."""
    if not text or len(text) <= chunk_size:
        return [text] if text else []
    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + chunk_size, n)
        if end < n:
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


def _one_call(
    client,
    cost_tracker: CostTracker,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    is_sub_call: bool = False,
) -> str:
    """ONE messages.create call. Records cost. Returns response text ("" on empty)."""
    cost_tracker.check_budget()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    try:
        cost_tracker.record_call(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            is_sub_call=is_sub_call,
        )
    except Exception:
        pass
    if not response.content:
        return ""
    return (response.content[0].text or "").strip()


def _pmap(fn: Callable, items, max_workers: int = 4) -> list:
    """Run `fn` over `items` concurrently (threads), preserving input order.

    Sequential when there are 0-1 items. The cost tracker is thread-safe, so
    parallel LLM sub-calls are budget-accounted correctly.
    """
    items = list(items)
    if len(items) <= 1:
        return [fn(x) for x in items]
    from concurrent.futures import ThreadPoolExecutor

    out: list = [None] * len(items)
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as ex:
        futs = {ex.submit(fn, x): i for i, x in enumerate(items)}
        for f in futs:
            out[futs[f]] = f.result()
    return out


# ---------------------------------------------------------------------------
# Router — one structured call (or chunked extract → merge for long sources)
# ---------------------------------------------------------------------------


_ROUTER_STRUCTURED_RULES = """\

## Response contract (STRUCTURED MODE)

You are NOT in a REPL. Do not write Python, do not call functions, do
not narrate. Respond with ONLY a single JSON object of EXACTLY this
shape — no prose before or after, no markdown fences:

{
  "source_id": "string",
  "source_title": "string",
  "source_summary": "string (1-2 sentences: what is the source?)",
  "changes": [
    {
      "op": "create" | "update",
      "page_id": "kebab-case-slug",
      "page_type": "entity" | "concept" | "source" | "synthesis",
      "title": "Human Readable Title",
      "reason": "1-3 sentences, fed to PageWriter"
    }
  ],
  "implied_wikilinks": ["slug-1", "slug-2"],
  "rationale": "one paragraph on the plan overall"
}

- Always include exactly one change with page_type "source" and
  page_id equal to the given source_id (op "create").
- Use the routing + scaling rules above to decide which entities and
  concepts become CREATE/UPDATE vs implied_wikilinks. Prefer UPDATE
  when an existing page (see the provided list of existing page ids)
  already covers a candidate.
- page_id values MUST be kebab-case slugs.
"""

_ROUTER_EXTRACT_PROMPT = """\
You are extracting candidate wiki pages from ONE excerpt of a longer
source on **{topic}**.

List every entity (person, work, organization, dataset, tool, model,
benchmark) and every concept (idea, algorithm, framework, primitive,
baseline) the author treats as load-bearing in THIS excerpt. Ignore
incidental mentions.

Respond with ONLY a JSON object of this shape (no prose, no fences):

{{
  "candidates": [
    {{"name": "Human Readable Name", "page_type": "entity" | "concept",
      "reason": "why it is load-bearing in this excerpt"}}
  ]
}}

EXCERPT:
{excerpt}
"""


class StructuredRouter:
    """Single-call IngestRouter replacement (no REPL).

    Same public `route(...)` signature as `IngestRouter.route`. Reuses
    `IngestRouter._parse_plan` + `_ensure_source_change` so the produced
    IngestPlan is identical in shape to the REPL router's output.
    """

    def __init__(
        self,
        client,
        config: CompendiumConfig,
        cost_tracker: CostTracker,
        *,
        vault: VaultPaths,
        tiered=None,
    ):
        self.client = client
        self.config = config
        self.cost_tracker = cost_tracker
        self._vault = vault
        tiered = tiered or config.tiered_models
        self.model = tiered.get_model(ModelTier.STRATEGIC)
        self.sub_model = tiered.get_model(ModelTier.SYNTHESIS)

    def _system_prompt(self) -> str:
        topic = _read_vault_topic(self._vault)
        base = INGEST_ROUTER_PROMPT.format(topic=topic or "(unspecified topic)")
        return base + _ROUTER_STRUCTURED_RULES

    def route(
        self,
        *,
        source_content: str,
        source_title: str,
        source_id: str,
        vault_topic: str,
        vault_index: str,
        recent_log: str,
    ) -> IngestPlan:
        token_count = _estimate_tokens(source_content)
        size_tier = _size_tier(token_count)

        # Deterministically prefetch existing page ids so the model can
        # decide CREATE vs UPDATE without REPL function calls.
        existing_ids = list_pages(self._vault)
        existing_block = (
            ", ".join(existing_ids) if existing_ids else "(none — empty vault)"
        )

        candidate_hint = ""
        if len(source_content) > _LONG_SOURCE_CHARS:
            candidate_hint = self._extract_candidates(source_content, vault_topic)

        # Bound the source content put in the prompt; the candidate-hint
        # carries the union of what the chunked pass found.
        content_for_prompt = source_content
        if len(content_for_prompt) > _LONG_SOURCE_CHARS:
            content_for_prompt = (
                source_content[: _LONG_SOURCE_CHARS]
                + "\n\n[...source truncated for prompt; see candidate list below...]"
            )

        user_parts = [
            f"source_id: {source_id}",
            f"source_title: {source_title}",
            f"source_size_tier: {size_tier} (~{token_count} tokens)",
            f"vault_topic: {vault_topic or '(unspecified)'}",
            "",
            "## existing page ids (prefer UPDATE over CREATE when one matches)",
            existing_block,
            "",
            "## vault index.md",
            (vault_index.strip() or "(empty)"),
        ]
        if recent_log.strip():
            user_parts += ["", "## recent log", recent_log.strip()]
        if candidate_hint:
            user_parts += [
                "",
                "## candidate pages (extracted from the full source by a "
                "chunked scan — use as your CREATE/UPDATE pool)",
                candidate_hint,
            ]
        user_parts += ["", "## source_content", content_for_prompt]
        user_parts += [
            "",
            "Produce the IngestPlan JSON object now. JSON only.",
        ]
        user_msg = "\n".join(user_parts)

        text = _one_call(
            self.client,
            self.cost_tracker,
            model=self.model,
            system=self._system_prompt(),
            user=user_msg,
            max_tokens=4096,
        )
        data = _extract_json(text)
        # Reuse the REPL router's validated parser + source-change guarantee.
        return _parse_plan(data, source_id=source_id, source_title=source_title)

    def _extract_candidates(self, source_content: str, vault_topic: str) -> str:
        """Chunked per-excerpt extraction → merged, deduped candidate list.

        For very long sources, run one cheap synthesis-tier extract call
        per chunk (sequentially, budget-gated), then format the union as
        a hint block for the single routing call.
        """
        topic = vault_topic or _read_vault_topic(self._vault) or "(unspecified)"
        chunks = _chunk(source_content)

        def _extract_chunk(c: str) -> dict:
            try:
                self.cost_tracker.check_budget()
            except Exception:
                return {}
            text = _one_call(
                self.client,
                self.cost_tracker,
                model=self.sub_model,
                system="You extract load-bearing entities and concepts as JSON.",
                user=_ROUTER_EXTRACT_PROMPT.format(topic=topic, excerpt=c),
                max_tokens=1500,
                is_sub_call=True,
            )
            return _extract_json(text)

        seen: dict[str, dict] = {}
        for data in _pmap(_extract_chunk, chunks):  # chunk calls run concurrently
            for raw in (data or {}).get("candidates", []) or []:
                if not isinstance(raw, dict):
                    continue
                name = (raw.get("name") or "").strip()
                if not name:
                    continue
                key = slugify(name)
                if key in seen:
                    continue
                seen[key] = {
                    "name": name,
                    "page_type": (raw.get("page_type") or "concept").strip(),
                    "reason": (raw.get("reason") or "").strip(),
                }
        if not seen:
            return ""
        lines = [
            f"- {v['name']} ({v['page_type']}): {v['reason']}".rstrip(": ")
            for v in seen.values()
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PageWriter — one structured call (long sources get a relevant-slice pass)
# ---------------------------------------------------------------------------


_WRITER_STRUCTURED_RULES = """\

## Response contract (STRUCTURED MODE)

You are NOT in a REPL. Do not write Python, do not call functions, do
not narrate. Respond with ONLY a single JSON object of EXACTLY this
shape — no prose before or after, no markdown fences:

{
  "id": "kebab-case-slug (= change.page_id)",
  "title": "Human Readable Title (= change.title)",
  "type": "entity" | "concept" | "source" | "synthesis",
  "summary": "ONE line for index.md",
  "tags": ["tag-1"],
  "aliases": ["Alternate Name"],
  "sources": ["source-id"],
  "source_tier": "primary" | "secondary" | "tertiary",
  "confidence": "high" | "medium" | "low",
  "body": "full markdown body, starting with the # heading"
}

- Apply ALL the writing rules above (reference-grade prose, generous
  [[slug|Display]] wikilinks, verbatim fenced code blocks for any
  formal content the page is about, the source-page structure for
  page_type "source", and the source_tier / confidence judgments).
- ALWAYS include the triggering source id in `sources`.
- The `body` value is a JSON string: escape newlines as \\n and
  double-quotes as \\". Output JSON only.
"""

_WRITER_SLICE_PROMPT = """\
From this excerpt, extract every fact, claim, quote, definition, or code
block directly about '{title}'. Preserve fenced code blocks VERBATIM.
Ignore anything unrelated. If nothing relevant, reply with exactly: N/A

EXCERPT:
{excerpt}
"""


class StructuredPageWriter:
    """Single-call PageWriter replacement (no REPL).

    Same public `write(...)` signature as `PageWriter.write`. Reuses
    `PageWriter.related_pages` (sibling-awareness) and
    `PageWriter._parse_page` so the produced Page is byte-compatible.
    """

    def __init__(
        self,
        client,
        config: CompendiumConfig,
        cost_tracker: CostTracker,
        *,
        vault: VaultPaths,
        tiered=None,
    ):
        self.client = client
        self.config = config
        self.cost_tracker = cost_tracker
        self.guardrails = config.get_guardrails()
        self._vault = vault
        tiered = tiered or config.tiered_models
        self.model = tiered.get_model(ModelTier.SYNTHESIS)
        self.sub_model = tiered.get_model(ModelTier.SYNTHESIS)

    def _system_prompt(self) -> str:
        topic = _read_vault_topic(self._vault)
        max_tokens = self.guardrails.max_tokens_per_page
        word_ceiling = int(max_tokens * 0.75)
        base = PAGE_WRITER_PROMPT.format(
            topic=topic or "(unspecified topic)",
            max_tokens=max_tokens,
            word_ceiling=word_ceiling,
        )
        return base + _WRITER_STRUCTURED_RULES

    def write(
        self,
        *,
        change: PageChange,
        source_id: str,
        source_title: str,
        source_content: str,
        sibling_index: str,
    ) -> Page:
        existing = read_page(self._vault, change.page_id)
        existing_page = (
            _read_page_for_repl(self._vault, change.page_id)
            if existing is not None
            else None
        )

        # Sibling-awareness: reuse PageWriter's pure-Python related_pages
        # scorer. It reads the `change` dict out of a REPL namespace, so
        # we feed it a tiny shim that exposes the same `get(name)` API.
        siblings = self._related_pages(change, limit=5)

        # For very long sources, distill the relevant slice first.
        content_for_prompt = source_content
        if len(source_content) > _LONG_SOURCE_CHARS:
            distilled = self._distill_relevant(change, source_content)
            if distilled:
                content_for_prompt = distilled
            else:
                content_for_prompt = source_content[: _LONG_SOURCE_CHARS]

        user_parts = [
            "## change",
            json.dumps(
                {
                    "op": change.op.value,
                    "page_id": change.page_id,
                    "page_type": change.page_type.value,
                    "title": change.title,
                    "reason": change.reason,
                },
                ensure_ascii=False,
            ),
            "",
            f"source_id: {source_id}",
            f"source_title: {source_title}",
        ]
        if siblings:
            sib_lines = []
            for s in siblings:
                sib_lines.append(
                    f"### sibling [{s['score']}] {s['id']} ({s['type']}): "
                    f"{s['summary']}\n{s['body']}"
                )
            user_parts += [
                "",
                "## related existing pages — reconcile your facts with these "
                "before drafting (do not contradict them; if you must, add an "
                "## Open questions section naming both page ids)",
                "\n\n".join(sib_lines),
            ]
        if existing_page is not None:
            user_parts += [
                "",
                "## existing page on disk (UPDATE — integrate, don't replace)",
                json.dumps(existing_page, ensure_ascii=False, default=str),
            ]
        if sibling_index.strip():
            user_parts += [
                "",
                "## index.md (wikilink targets)",
                sibling_index.strip()[:8000],
            ]
        user_parts += ["", "## source_content", content_for_prompt]
        user_parts += ["", "Produce the page JSON object now. JSON only."]
        user_msg = "\n".join(user_parts)

        # Give the body room: max_tokens_per_page is the body ceiling, plus
        # headroom for frontmatter fields and JSON escaping.
        max_tokens = max(2048, int(self.guardrails.max_tokens_per_page * 1.5))
        text = _one_call(
            self.client,
            self.cost_tracker,
            model=self.model,
            system=self._system_prompt(),
            user=user_msg,
            max_tokens=max_tokens,
        )
        data = _extract_json(text)
        if not data:
            raise ValueError(
                f"StructuredPageWriter got no parseable JSON for "
                f"{change.page_id!r}"
            )
        return _parse_page(data, change=change, source_id=source_id)

    def _related_pages(self, change: PageChange, *, limit: int = 5) -> list[dict]:
        """Run PageWriter._related_pages_for_repl with a minimal REPL shim.

        That function only needs `repl.get("change")`, so we provide a
        tiny object that satisfies it. Pure Python, no LLM call.
        """

        class _ChangeShim:
            def __init__(self, change: PageChange):
                self._change = {
                    "title": change.title,
                    "page_type": change.page_type.value,
                    "page_id": change.page_id,
                }

            def get(self, name: str):
                return self._change if name == "change" else None

        try:
            return _related_pages_for_repl(
                self._vault, _ChangeShim(change), limit=limit
            )
        except Exception:
            return []

    def _distill_relevant(self, change: PageChange, source_content: str) -> str:
        """Chunked map → concat of slices relevant to this page's topic.

        Per-chunk slice calls run concurrently; order is preserved.
        """
        chunks = _chunk(source_content)

        def _slice_chunk(c: str) -> str:
            try:
                self.cost_tracker.check_budget()
            except Exception:
                return ""
            text = _one_call(
                self.client,
                self.cost_tracker,
                model=self.sub_model,
                system="You extract source material relevant to one wiki page.",
                user=_WRITER_SLICE_PROMPT.format(title=change.title, excerpt=c),
                max_tokens=2000,
                is_sub_call=True,
            )
            return text if (text and "N/A" not in text[:8]) else ""

        slices = [s for s in _pmap(_slice_chunk, chunks) if s]
        return "\n\n---\n\n".join(slices)


# ---------------------------------------------------------------------------
# Explorer — one structured call over deterministically-gathered signals
# ---------------------------------------------------------------------------


_EXPLORER_STRUCTURED_RULES = """\

## Response contract (STRUCTURED MODE)

You are NOT in a REPL. The mechanical signals you would query are
already gathered for you below. Respond with ONLY a single JSON object
of EXACTLY this shape — no prose, no markdown fences:

{
  "proposals": [
    {
      "kind": "gap" | "open_question" | "missed_connection" | "source_suggestion" | "thesis_drift",
      "title": "one-line title (may use [[Page Title]] wikilinks)",
      "priority": 1,
      "signal": "one sentence naming the mechanical signal that triggered this",
      "rationale": "1-3 sentences on why this matters, citing specific pages",
      "related": ["page-id-1", "page-id-2"]
    }
  ]
}

Apply the categories + guidelines above. Aim for 5-15 proposals. JSON only.
"""


class StructuredExplorer:
    """Single-call Explorer replacement (no REPL).

    Same public `explore()` signature + same write to `_meta/expansion.md`
    via Explorer's `_write_expansion_files`. Mechanical signals are
    pre-gathered deterministically (no signal-query functions).
    """

    def __init__(
        self,
        client,
        config: CompendiumConfig,
        cost_tracker: CostTracker,
        *,
        vault: VaultPaths,
        tiered=None,
    ):
        self.client = client
        self.config = config
        self.cost_tracker = cost_tracker
        self._vault = vault
        tiered = tiered or config.tiered_models
        self.model = tiered.get_model(ModelTier.STRATEGIC)

    @property
    def vault(self) -> VaultPaths:
        return self._vault

    def _system_prompt(self, topic: str) -> str:
        return EXPLORER_PROMPT.format(topic=topic or "(unspecified topic)") + _EXPLORER_STRUCTURED_RULES

    def explore(self) -> ExpansionReport:
        topic = _explorer_read_topic(self._vault)
        page_count = len(list_pages(self._vault))
        report = ExpansionReport(
            timestamp=timestamp_iso(), topic=topic, proposals=[]
        )
        if page_count == 0:
            _write_empty_report(self._vault, report, topic)
            return report

        # Deterministically gather the mechanical signals the REPL
        # Explorer would otherwise query function-by-function.
        broken = _broken_for_repl(self._vault, 20)
        orphans = find_orphans(self._vault)[:20]
        thin = _thin_for_repl(self._vault, 120)[:20]
        sources = _source_summaries_for_repl(self._vault, limit=8)
        recent = read_recent(self._vault, n=5)
        mend = self._mend_escalations()

        parts = [
            f"vault_topic: {topic or '(unspecified)'}",
            f"page_count: {page_count}",
        ]
        if mend.strip():
            parts += ["", "## mend escalations (LLM-verified gaps / open questions)", mend]
        if broken:
            parts += ["", "## broken_wikilinks (target — N refs — referrers)"]
            for target, n, refs in broken:
                parts.append(f"- {target} ({n} ref{'s' if n != 1 else ''}) referrers: [{', '.join(refs)}]")
        if orphans:
            parts += ["", "## orphan_pages (no inbound wikilinks)"]
            parts += [f"- {pid}" for pid in orphans]
        if thin:
            parts += ["", "## thin_pages (< 120 words)"]
            parts += [f"- {pid} ({wc} words)" for pid, wc in thin]
        if sources:
            parts += ["", "## source_summaries"]
            for sid, title, body in sources:
                parts += [f"### source: {sid} — {title}", body]
        if recent.strip():
            parts += ["", "## recent_log", recent.strip()]
        parts += ["", "Produce the proposals JSON object now. JSON only."]
        user_msg = "\n".join(parts)

        try:
            text = _one_call(
                self.client,
                self.cost_tracker,
                model=self.model,
                system=self._system_prompt(topic),
                user=user_msg,
                max_tokens=4096,
            )
        except Exception as exc:
            raise RuntimeError(f"StructuredExplorer call failed: {exc}") from exc

        data = _extract_json(text)
        if data:
            report.proposals = list(_parse_proposals_dict(data))

        self._vault.meta.mkdir(parents=True, exist_ok=True)
        _write_expansion_files(self._vault, report)
        return report

    def _mend_escalations(self) -> str:
        path = self._vault.meta / "mend-report.md"
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")[:12_000]
        except OSError:
            return ""


# ---------------------------------------------------------------------------
# Gather — non-REPL research: search → fetch → save as raw/articles/*.md
# ---------------------------------------------------------------------------


# Mirror research_agent._provenance_header byte-for-byte so saved sources
# carry the same `<!-- source_type: ... -->` provenance the downstream
# pipeline (and the Writer's source_tier heuristic) expects.
def _provenance_header(*, topic: str, source_type: str, source_url: str | None = None) -> str:
    from compendium.vault.pages import today_iso

    lines = [
        f"<!-- research_topic: {topic} -->",
        f"<!-- source_type: {source_type} -->",
    ]
    if source_url:
        lines.append(f"<!-- source_url: {source_url} -->")
    lines.append(f"<!-- researched: {today_iso()} -->")
    return "\n".join(lines) + "\n\n"


def _unique_slug_in_dir(base: str, used: set[str], dir_: Path) -> str:
    """Mirror research_agent._unique_slug_in_dir for byte-compatible slugs."""
    candidate = base or "research-source"
    n = 2
    while candidate in used or (dir_ / f"{candidate}.md").exists() or (
        dir_ / f"{candidate}.pdf"
    ).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _query_variants(prompt: str) -> list[str]:
    """The query plus 1-2 sensible variants for broader coverage."""
    p = prompt.strip()
    variants = [p]
    if len(p.split()) <= 8:
        variants.append(f"{p} overview")
        variants.append(f"{p} explained")
    else:
        variants.append(f"{p} summary")
    # De-dup preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out[:3]


# ---------------------------------------------------------------------------
# Topic resolution — read the vault and turn a (possibly vague) prompt into
# concrete, vault-grounded search queries BEFORE dispatching research agents.
# ---------------------------------------------------------------------------

# Words that refer to the meta-task ("expand the knowledge base") rather than
# to any subject. A prompt whose only words are these is "generic": the topic
# must be inferred from the vault itself, not searched for literally.
_META_WORDS = {
    "find", "finds", "found", "new", "fresh", "recent", "material", "materials",
    "source", "sources", "expand", "expanding", "expansion", "grow", "growing",
    "extend", "extending", "add", "adding", "more", "the", "a", "an", "any",
    "knowledge", "base", "bases", "kb", "wiki", "wikis", "vault", "graph",
    "notes", "note", "page", "pages", "node", "nodes", "entry", "entries",
    "to", "this", "that", "these", "my", "our", "your", "content", "contents",
    "information", "info", "stuff", "things", "and", "or", "on", "about",
    "deepen", "deeper", "fill", "fills", "filling", "gap", "gaps", "build",
    "building", "out", "develop", "developing", "enrich", "enriching",
    "update", "updates", "updating", "with", "some", "please", "go", "into",
    "for", "of", "it", "let's", "lets", "can", "you", "i", "want", "need",
    "make", "better", "good", "great", "current", "existing", "topic", "topics",
}


def _looks_generic(text: str) -> bool:
    """True when the text names no subject of its own (e.g. 'expand the
    knowledge base') — only meta-words remain. The research topic must then
    be inferred from the vault."""
    toks = re.findall(r"[a-zA-Z][a-zA-Z'\-]*", (text or "").lower())
    return not any(t not in _META_WORDS for t in toks)


def _deslug(s: str) -> str:
    return re.sub(r"[-_]+", " ", (s or "").strip()).strip()


def _vault_research_digest(paths: VaultPaths) -> dict:
    """A compact snapshot of vault state for topic resolution: the subject,
    the index blurb, existing non-source pages, the clearest gaps (wanted-
    but-missing pages + thin stubs), and the recent activity log."""
    subject = _read_vault_topic(paths)
    index_head = ""
    try:
        if paths.index_md.exists():
            index_head = paths.index_md.read_text(encoding="utf-8").strip()[:800]
    except OSError:
        pass

    pages: list[str] = []
    titles: list[str] = []
    for pid in list_pages(paths):
        page = read_page(paths, pid)
        if page is None or page.type == PageType.SOURCE:
            continue
        if page.title:
            titles.append(page.title)
        summ = " ".join((page.summary or "").split())
        pages.append(
            f"- {page.title} [{page.type.value}]" + (f": {summ}" if summ else "")
        )
        if len(pages) >= 80:
            break

    try:
        broken = [(_deslug(t), c) for t, c, _refs in _broken_for_repl(paths, 18)]
    except Exception:
        broken = []
    try:
        thin = [(pid, wc) for pid, wc in _thin_for_repl(paths, 12)]
    except Exception:
        thin = []
    try:
        recent = read_recent(paths, 6)
    except Exception:
        recent = ""

    return {
        "subject": subject,
        "index_head": index_head,
        "pages": pages,
        "titles": titles,
        "broken": broken,
        "thin": thin,
        "recent": recent,
    }


def _fallback_queries(prompt: str, digest: dict) -> list[str]:
    """Heuristic queries when no LLM is available to resolve the topic.

    Generic prompt → derive from the gaps + subject. Specific prompt → the
    prompt plus variants (the original behavior)."""
    if not _looks_generic(prompt):
        return _query_variants(prompt)
    # Only use the subject as a query qualifier when it's a short, clean phrase
    # (vault schema lines can be a verbose sentence full of meta-words).
    subject = (digest.get("subject") or "").strip()
    subj_kw = subject if 0 < len(subject.split()) <= 4 else ""
    out: list[str] = []
    # Gaps first: wanted-but-missing pages, then thin stubs — clearest signal
    # of where a new/deepened node belongs.
    for target, _count in digest.get("broken", []):
        if target.strip():
            out.append(f"{target.strip()} {subj_kw}".strip())
    for pid, _wc in digest.get("thin", []):
        t = _deslug(pid)
        if t:
            out.append(f"{t} {subj_kw}".strip())
    # Complete vault with no gaps → seed from existing page titles (clean nouns)
    # to find adjacent/deeper material on the same subjects.
    if not out:
        for title in digest.get("titles", []):
            out.append(title.strip())
    if not out and subj_kw:
        out.append(subj_kw)
    seen: set[str] = set()
    uniq: list[str] = []
    for q in out:
        k = q.lower()
        if q and not _looks_generic(q) and k not in seen:
            seen.add(k)
            uniq.append(q)
    return uniq[:6] or _query_variants(prompt)


_RESOLVE_SYSTEM = """\
You plan web-search queries that EXPAND an existing knowledge base (a wiki of
interlinked pages). You are given the vault's SUBJECT, an inventory of its
pages, its current gaps, the recent activity log, and the user's request.

Turn the request into concrete web-search queries that find NEW source
material ON THE VAULT'S SUBJECT.

Rules — read carefully:
- Queries MUST be about the vault's subject matter, never about the meta-task.
  A request like "find new material and expand the knowledge base" is a request
  about THIS vault's subject. Treat the words knowledge base / wiki / vault /
  graph / notes / page / node / expand / grow / deepen as referring to THIS
  vault — NEVER search for those words literally.
- Prioritize the gaps. Wanted-but-missing pages (broken wikilinks) are the
  clearest signal of where a new node belongs; thin stubs want deepening.
- If the request names a specific topic within the subject, focus there. If it
  is generic, infer the highest-value directions from the gaps and coverage.
- Prefer specific, source-findable queries (named subtopics, mechanisms,
  people, works) over vague ones. Return 3 to 6 queries.

Respond with ONLY a JSON object (no prose, no markdown fences):
{
  "subject": "short phrase naming the vault's subject as you understand it",
  "is_generic_request": true | false,
  "queries": ["query 1", "query 2", "..."],
  "rationale": "one sentence on why these queries"
}
"""


def resolve_research_queries(
    config: CompendiumConfig,
    paths: VaultPaths,
    prompt: str,
    *,
    cost_tracker: CostTracker | None = None,
) -> dict:
    """Resolve a (possibly vague) research prompt into concrete, vault-
    grounded search queries.

    Reads vault state (subject, page inventory, gaps, recent log) so a request
    like "find new material and expand the knowledge base" researches the
    vault's *actual subject* rather than the literal words. Falls back to
    heuristics when no LLM auth is available or the call fails.

    Returns {"subject": str, "queries": list[str], "generic": bool,
             "rationale": str}.
    """
    digest = _vault_research_digest(paths)
    generic = _looks_generic(prompt)

    if not config.has_auth:
        return {
            "subject": digest.get("subject", ""),
            "queries": _fallback_queries(prompt, digest),
            "generic": generic,
            "rationale": "no LLM auth — heuristic queries from vault gaps",
        }

    user_parts = [
        f"## user request\n{prompt.strip() or '(empty)'}",
        "",
        "## vault subject\n"
        + (digest["subject"] or "(unstated — infer it from the pages below)"),
    ]
    if digest["index_head"]:
        user_parts += ["", "## vault index (intro)", digest["index_head"]]
    if digest["pages"]:
        user_parts += [
            "",
            f"## existing pages ({len(digest['pages'])} shown)",
            "\n".join(digest["pages"]),
        ]
    if digest["broken"]:
        gaps = "\n".join(
            f"- {t} (wanted by {c} page{'s' if c != 1 else ''})"
            for t, c in digest["broken"]
        )
        user_parts += [
            "",
            "## wanted-but-missing pages (top gaps — new nodes belong here)",
            gaps,
        ]
    if digest["thin"]:
        stubs = "\n".join(f"- {_deslug(pid)} ({wc} words)" for pid, wc in digest["thin"])
        user_parts += ["", "## thin stubs (want deepening)", stubs]
    if digest["recent"].strip():
        user_parts += ["", "## recent activity log", digest["recent"].strip()[:1500]]

    data: dict = {}
    try:
        client = config.create_anthropic_client()
        ct = cost_tracker or CostTracker(config.get_guardrails())
        model = config.tiered_models.get_model(ModelTier.STRATEGIC)
        text = _one_call(
            client, ct,
            model=model,
            system=_RESOLVE_SYSTEM,
            user="\n".join(user_parts),
            max_tokens=700,
        )
        data = _extract_json(text)
    except Exception:
        data = {}

    raw = data.get("queries") if isinstance(data, dict) else None
    queries = [q.strip() for q in (raw or []) if isinstance(q, str) and q.strip()]
    # Guard: drop any query that is itself only meta-words (model slipped and
    # echoed "expand the knowledge base" instead of a real topic).
    queries = [q for q in queries if not _looks_generic(q)][:6]
    if not queries:
        queries = _fallback_queries(prompt, digest)

    subject = (data.get("subject") if isinstance(data, dict) else None) or digest.get(
        "subject", ""
    )
    return {
        "subject": subject,
        "queries": queries,
        "generic": bool(data.get("is_generic_request", generic))
        if isinstance(data, dict)
        else generic,
        "rationale": (data.get("rationale") if isinstance(data, dict) else "") or "",
    }


def structured_gather(
    config: CompendiumConfig,
    paths: VaultPaths,
    prompt: str,
    registry: IngestRegistry,
    progress: Callable[[str, dict], None] | None = None,
    *,
    cost_tracker: CostTracker | None = None,
    resolve: bool = True,
) -> list[Path]:
    """Non-REPL research gather. Returns the saved raw/articles/*.md Paths.

    First RESOLVES the prompt against the vault (subject, page inventory,
    gaps, recent log) into concrete search queries — so a vague request like
    "expand the knowledge base" researches the vault's *actual subject*, not
    the literal words (set `resolve=False` to search the prompt verbatim).
    Then builds `web_search` + `fetch_url` from `compendium.repl.functions`,
    runs the queries, takes the top unique result URLs, fetches each, and
    saves each as `raw/articles/<unique-slug>.md` using the SAME provenance-
    header format as research_agent. Applies the IngestRegistry dedup gate
    (tombstone → find_by_url → skip, else record). Emits progress events if
    a callback is given.
    """

    def _emit(phase: str, **payload) -> None:
        if progress is None:
            return
        try:
            progress(phase, payload)
        except Exception:
            pass

    web_search = make_web_search_fn(
        provider=config.search_provider,
        api_key=config.search_api_key,
        jina_api_key=getattr(config, "jina_api_key", ""),
    )
    fetch_url = make_fetch_url_fn(jina_api_key=getattr(config, "jina_api_key", ""))

    # Resolve the prompt → vault-grounded queries before any search. This is
    # the step that keeps "expand the knowledge base" on the vault's subject.
    if resolve:
        plan = resolve_research_queries(
            config, paths, prompt, cost_tracker=cost_tracker
        )
        queries = plan["queries"]
        topic_label = (plan["subject"] or prompt).strip()
        _emit(
            "resolved",
            subject=plan["subject"],
            queries=queries,
            generic=plan["generic"],
            rationale=plan["rationale"],
        )
    else:
        queries = _query_variants(prompt)
        topic_label = prompt.strip()

    if not queries:
        queries = _query_variants(prompt)

    _emit(
        "search",
        msg=f"Searching the web ({len(queries)} quer"
        + ("ies" if len(queries) != 1 else "y")
        + (f" on {topic_label}" if topic_label else "")
        + ")",
        query=queries[0] if queries else prompt,
        queries=queries,
    )

    # Collect unique result URLs across the resolved queries.
    seen_urls: set[str] = set()
    ranked: list[dict] = []
    for q in queries:
        try:
            results = web_search(q, num_results=6)
        except Exception:
            results = []
        for r in results or []:
            if not isinstance(r, dict) or r.get("error"):
                continue
            url = (r.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            ranked.append(r)
        if len(ranked) >= 6:
            break

    # Top ~4-6 unique results.
    ranked = ranked[:6]
    _emit("searched", count=len(ranked), urls=[r.get("url", "") for r in ranked])

    paths.raw_articles.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    used_slugs: set[str] = set()

    # Dedup gate (sequential, registry reads): tombstone first, then URL.
    to_fetch: list[tuple[str, str]] = []
    for r in ranked:
        url = (r.get("url") or "").strip()
        title = (r.get("title") or url or "Untitled").strip()
        if not url:
            continue
        try:
            if registry.is_tombstoned(url=url) is not None:
                _emit("skipped", url=url, reason="tombstoned")
                continue
            hit = registry.find_by_url(url)
            if hit is not None:
                _emit("skipped", url=url, reason=f"already ingested as {hit.source_id}")
                continue
        except Exception:
            pass
        to_fetch.append((url, title))

    # Fetch pages CONCURRENTLY (I/O-bound). Saves stay sequential so the
    # registry + file writes never race.
    _emit("fetch", count=len(to_fetch))

    def _fetch(item: tuple[str, str]):
        url, title = item
        try:
            body = fetch_url(url)
        except Exception as exc:
            return (url, title, None, str(exc)[:160])
        if not body or body.strip().startswith("[FETCH ERROR]"):
            return (url, title, None, (body or "")[:160])
        return (url, title, body, None)

    for url, title, body, err in _pmap(_fetch, to_fetch):
        if body is None:
            _emit("fetch_failed", url=url, error=err or "")
            continue

        slug = _unique_slug_in_dir(slugify(title), used_slugs, paths.raw_articles)
        used_slugs.add(slug)

        # Build the markdown source: provenance header + # heading + the
        # fetched body + a ## Sources block (load-bearing for the linter).
        content = body.strip()
        if not content.startswith("# "):
            content = f"# {title}\n\n{content}"
        if "## Sources" not in content:
            content = content.rstrip() + f"\n\n## Sources\n- {title} — {url}\n"
        header = _provenance_header(
            topic=topic_label or prompt, source_type="article", source_url=url
        )
        target = paths.raw_articles / f"{slug}.md"
        target.write_text(header + content + "\n", encoding="utf-8", newline="\n")

        # Record in the registry so a re-run skips this URL.
        try:
            registry.record(
                RegistryEntry(
                    source_id=slug,
                    raw_path=target.relative_to(paths.root).as_posix(),
                    ingested=now_iso(),
                    url=url,
                    origin=url,
                )
            )
        except Exception:
            pass

        saved.append(target)
        _emit("gathered", url=url, slug=slug, path=str(target))

    _emit("gather_done", saved=len(saved))
    return saved
