"""Explorer — proposes where the vault should grow next.

Explorer is the gap-finder. After every ingest (and on demand via
`cli.py explore`) it surveys the current vault, collects mechanical
signals (broken wikilinks, orphans, thin pages, recent Reviewer flags),
and asks a strategic-tier model to synthesize a ranked list of
expansion proposals filed to `wiki/_meta/expansion.md`.

Categories:
- `gap`                  — a page that should exist but doesn't
- `open_question`        — an unresolved tension or contradiction
- `missed_connection`    — two existing pages that should cross-link
- `source_suggestion`    — an external source worth fetching
- `thesis_drift`         — the direction the corpus is pulling

Explorer is best-effort: if it fails, the caller (IngestOrchestrator
or the CLI) logs the error and continues.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import anthropic

from compendium.config import CompendiumConfig
from compendium.guardrails.cost_tracker import CostTracker
from compendium.models import (
    ExpansionKind,
    ExpansionProposal,
    ExpansionReport,
    ModelTier,
    PageType,
)
from compendium.vault import (
    VaultPaths,
    find_broken_wikilinks,
    find_orphans,
    list_pages,
    read_page,
    read_recent,
    timestamp_iso,
    today_iso,
)


# Body-length thresholds
_THIN_WORD_COUNT = 120

# Signal payload caps (truncate so the prompt stays tight)
_MAX_BROKEN = 30
_MAX_ORPHANS = 20
_MAX_THIN = 20
_MAX_SOURCE_SUMMARIES = 12


@dataclass
class _Signals:
    """Mechanical signals gathered from the vault before LLM synthesis."""

    topic: str
    page_count: int
    index_text: str
    recent_log: str
    broken_counts: list[tuple[str, int, list[str]]]   # (target_slug, count, example referrers)
    orphan_pages: list[str]
    thin_pages: list[tuple[str, int]]                  # (page_id, word_count)
    source_summaries: list[tuple[str, str, str]]       # (source_id, title, body-excerpt)

    def is_empty(self) -> bool:
        return (
            self.page_count == 0
            and not self.broken_counts
            and not self.orphan_pages
            and not self.thin_pages
        )


EXPLORER_PROMPT = """\
You are the Explorer for a vault on **{topic}**.

Your job: propose where the vault should grow next. You are NOT writing
pages. You produce a ranked, categorized list of expansion opportunities
the user can pursue — which gaps to fill, which questions to resolve,
which sources to fetch, which connections to draw.

## Your environment — REPL with signal-query functions

You operate in a persistent Python REPL. Do NOT expect all signals to
arrive in your prompt. Instead, query them as you reason, so your
context stays bounded even for vaults of thousands of pages.

Context variables:
- `vault_topic` (str) — the vault's overall topic
- `page_count` (int) — current number of wiki pages

Signal functions (pull on demand; each one is cheap):
- `get_broken_wikilinks(top_n=30)` -> list[(target_slug, ref_count, referrer_ids)]
    Missing pages ranked by how many pages want them. Highest-value gap source.
- `get_orphans()` -> list[page_id]
    Pages with zero inbound wikilinks (excluding source pages).
- `get_thin_pages(threshold=120)` -> list[(page_id, word_count)]
    Pages with body under `threshold` words — candidates to flesh out
    or fold into neighbors.
- `get_source_summaries(ids=None, limit=10)` -> list[(source_id, title, body_excerpt)]
    Samples source pages. Pass `ids=[...]` for specific, or `limit=N`
    to cap. Each body is truncated to ~2k chars.
- `get_vault_index()` -> str
    Full index.md content. For large vaults this can be big — prefer
    `list_pages()` + targeted `read_page()` unless you specifically need
    the flat summary list.
- `get_recent_log(n=5)` -> str
    Last N log entries. Best source of Reviewer flags from recent ingests.
- `get_mend_escalations()` -> str
    Contents of `_meta/mend-report.md`. Read this BEFORE `get_broken_wikilinks`
    when it's available — Mender's KEEP actions are confirmed gap signals
    (LLM-verified missing pages, not typos), and ESCALATED contradictions are
    ready-made `open_question` proposals. Each escalation should generally
    map 1:1 to a proposal in your report.
- `read_page(page_id)` -> dict or None
- `list_pages(page_type=None)` -> list[page_id]

Helper functions:
- `llm_query(prompt)` -> str (cheap sub-call)
- `slugify(text)` -> kebab-case
- `web_search(query)`, `fetch_url(url)` — sparingly
- `FINAL_VAR('report')` to finish.

## Categories

- `gap`: a page that should exist but doesn't. `get_broken_wikilinks`
  is the primary source. Priority tracks reference count and centrality.
- `open_question`: an unresolved tension. Reviewer contradictions
  (read them from `get_recent_log`), unsupported claims, explicit
  `## Open Questions` sections within pages.
- `missed_connection`: two existing pages that plausibly should
  cross-link but don't. Look at `get_source_summaries` or read related
  pages for entities/concepts that co-occur.
- `source_suggestion`: a specific external source (named book, paper,
  author, archive, dataset) that would fill a gap. Be specific —
  vague "more about X" is useless.
- `thesis_drift`: the direction the corpus is pulling, or a synthesis
  tension across multiple sources. Only emit when genuine drift is
  visible. Skip if the vault has one source or is empty.

## Method

1. Start with `print(vault_topic, page_count)`.
2. `mend = get_mend_escalations(); print(mend[:2000])` — read the Mender's
   curated escalations FIRST. KEEP actions are LLM-verified gaps.
   ESCALATED contradictions are ready `open_question` proposals.
3. `broken = get_broken_wikilinks(20); print(broken)` — mechanical gap source;
   use to supplement (not duplicate) the mend-sourced gaps.
4. For vaults with >5 sources, sample 5-8 summaries with
   `get_source_summaries(limit=8)` rather than all of them.
5. Check `get_recent_log(5)` for Reviewer contradiction flags.
6. For large vaults ( page_count > 50 ), DO NOT call `get_vault_index()`
   — it's redundant with the mechanical signals. Use it only when the
   signals are ambiguous.
7. Assemble the `report` dict. Call `FINAL_VAR('report')`.

## Output format

Build a dict named `report` containing a `proposals` list, then
`FINAL_VAR('report')`:

```python
report = {{
    "proposals": [
        {{
            "kind": "gap" | "open_question" | "missed_connection" | "source_suggestion" | "thesis_drift",
            "title": "one-line title (use [[Page Title]] wikilinks where apt)",
            "priority": 1,  # 1 highest, 5 lowest
            "signal": "one sentence naming what mechanical signal triggered this",
            "rationale": "1-3 sentences on why this matters, citing specific pages",
            "related": ["page-id-1", "page-id-2"]
        }}
    ]
}}
```

## Guidelines

- Aim for 5–15 proposals overall, spread across categories.
- Proposals touching the most pages or resolving Reviewer-flagged
  errors belong at priority 1–2.
- Don't just restate signals. Synthesize and explain the "why".
- No duplicates. One proposal per distinct gap/question/connection.
- Source suggestions must name specific works or authors.
- Skip any category with no worthy candidates — empty is fine.
"""


# ---- signal functions exposed to REPL --------------------------------------


def _broken_for_repl(vault: VaultPaths, top_n: int):
    raw = find_broken_wikilinks(vault)
    by_target: dict[str, list[str]] = {}
    for referrer, target in raw:
        by_target.setdefault(target, []).append(referrer)
    ordered = sorted(
        (
            (target, len(refs), sorted(set(refs))[:6])
            for target, refs in by_target.items()
        ),
        key=lambda t: (-t[1], t[0]),
    )
    return ordered[:top_n]


def _thin_for_repl(vault: VaultPaths, threshold: int):
    out: list[tuple[str, int]] = []
    for page_id in list_pages(vault):
        page = read_page(vault, page_id)
        if page is None or page.type == PageType.SOURCE:
            continue
        wc = len(page.body.split())
        if wc < threshold:
            out.append((page_id, wc))
    out.sort(key=lambda t: t[1])
    return out


def _source_summaries_for_repl(
    vault: VaultPaths,
    ids: list[str] | None = None,
    limit: int = 10,
):
    source_ids = ids or list_pages(vault, PageType.SOURCE)
    out = []
    for sid in source_ids[:limit]:
        page = read_page(vault, sid)
        if page is None:
            continue
        body = page.body.strip()
        if len(body) > 2000:
            body = body[:2000] + "\n…[truncated]"
        out.append((sid, page.title, body))
    return out


def _read_index(vault: VaultPaths) -> str:
    return (
        vault.index_md.read_text(encoding="utf-8")
        if vault.index_md.exists()
        else ""
    )


def _mend_escalations_for_repl(vault: VaultPaths) -> str:
    """Return the latest mend-report.md content, or a dash if absent.

    The Explorer's REPL reads this to incorporate the Mender's KEEP +
    ESCALATED decisions into its proposals. A KEEP on a broken link is
    a stronger gap signal than a raw `get_broken_wikilinks()` count,
    because the LLM confirmed it's an intentional gap rather than a
    typo that should've been redirected.
    """
    path = vault.meta / "mend-report.md"
    if not path.is_file():
        return "(no mend-report.md — mend has never run on this vault)"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "(mend-report.md unreadable)"
    # Cap the returned length — the report can be long and the REPL
    # doesn't need every action, just the signals.
    return text[:12_000]


def _read_page_for_repl(vault: VaultPaths, page_id: str):
    page = read_page(vault, page_id)
    if page is None:
        return None
    return {
        "id": page.id,
        "title": page.title,
        "type": page.type.value,
        "summary": page.summary,
        "tags": list(page.tags),
        "aliases": list(page.aliases),
        "sources": list(page.sources),
        "updated": page.updated,
        "body": page.body,
    }


def _list_pages_for_repl(vault: VaultPaths, page_type: str | None):
    pt = PageType(page_type) if page_type else None
    return list_pages(vault, pt)


# ---- Explorer (REPL-based) -------------------------------------------------


from compendium.agents.base import BaseAgent
from compendium.repl.environment import REPLEnvironment
from compendium.vault.pages import slugify


class Explorer(BaseAgent):
    """REPL-based Explorer — queries vault signals on demand.

    Scales to large vaults because signals are functions, not
    pre-fetched context. The agent decides what to sample based on
    its current reasoning, mirroring the RLM pattern the project is
    built on.
    """

    def __init__(self, *args, **kwargs):
        """Support the pre-REPL positional signature plus keyword style.

        Valid calls:
            Explorer(client, config, cost_tracker, vault)           # legacy
            Explorer(client, config, cost_tracker, vault=vault)     # hybrid
            Explorer(client=..., config=..., cost_tracker=..., vault=..., tiered=...)
        """
        tiered = kwargs.pop("tiered", None)
        vault = kwargs.pop("vault", None)
        args_list = list(args)
        if vault is None and len(args_list) == 4:
            vault = args_list.pop(3)
        if vault is None:
            raise TypeError("Explorer requires `vault` (positional or keyword)")
        args = tuple(args_list)

        if tiered is not None:
            kwargs.setdefault("model_override", tiered.get_model(ModelTier.STRATEGIC))
            kwargs.setdefault(
                "sub_call_model_override",
                tiered.get_model(ModelTier.SYNTHESIS),
            )

        super().__init__(*args, **kwargs)
        self._vault = vault
        # Default to strategic tier when no explicit override was set —
        # keeps behavior identical to the pre-REPL Explorer.
        if self._model_override is None:
            self._model_override = self.config.tiered_models.get_model(
                ModelTier.STRATEGIC
            )
        if self._sub_call_model_override is None:
            self._sub_call_model_override = self.config.tiered_models.get_model(
                ModelTier.SYNTHESIS
            )

    # Back-compat alias — many callers still reference `.vault`.
    @property
    def vault(self) -> VaultPaths:
        return self._vault

    def get_system_prompt(self) -> str:
        topic = _read_topic(self._vault) or "(unspecified topic)"
        return EXPLORER_PROMPT.format(topic=topic)

    def _register_standard_functions(self, repl: REPLEnvironment) -> None:
        super()._register_standard_functions(repl)
        vault = self._vault
        repl.register_function(
            "get_broken_wikilinks",
            lambda top_n=30: _broken_for_repl(vault, top_n),
        )
        repl.register_function("get_orphans", lambda: find_orphans(vault))
        repl.register_function(
            "get_thin_pages",
            lambda threshold=_THIN_WORD_COUNT: _thin_for_repl(vault, threshold),
        )
        repl.register_function(
            "get_source_summaries",
            lambda ids=None, limit=10: _source_summaries_for_repl(vault, ids, limit),
        )
        repl.register_function("get_vault_index", lambda: _read_index(vault))
        repl.register_function(
            "get_recent_log", lambda n=5: read_recent(vault, n=n)
        )
        repl.register_function(
            "read_page", lambda page_id: _read_page_for_repl(vault, page_id)
        )
        repl.register_function(
            "list_pages",
            lambda page_type=None: _list_pages_for_repl(vault, page_type),
        )
        repl.register_function("slugify", slugify)
        # Mend escalations — KEEP-decisions and ESCALATED contradictions
        # are higher-quality gap signals than raw mechanical output
        # because they've been LLM-filtered. Explorer reads these
        # alongside the mechanical signals so its proposals reflect
        # curated-reality, not just noisy slug counts.
        repl.register_function(
            "get_mend_escalations",
            lambda: _mend_escalations_for_repl(vault),
        )

    # ------------------------------------------------------------------ run

    def explore(self) -> ExpansionReport:
        """Run one Explore pass via REPL and write `_meta/expansion.md`."""
        topic = _read_topic(self._vault)
        page_count = len(list_pages(self._vault))
        report = ExpansionReport(
            timestamp=timestamp_iso(), topic=topic, proposals=[]
        )
        if page_count == 0:
            _write_empty_report(self._vault, report, topic)
            return report

        context = {
            "vault_topic": topic or "(unspecified)",
            "page_count": page_count,
        }
        try:
            raw = self.run(context, max_iterations_override=20)
        except Exception as exc:
            raise RuntimeError(f"Explorer REPL failed: {exc}") from exc

        if isinstance(raw, dict) and "proposals" in raw:
            report.proposals = list(_parse_proposals_dict(raw))
        elif isinstance(raw, str):
            report.proposals = list(_parse_proposals_text(raw))

        self._vault.meta.mkdir(parents=True, exist_ok=True)
        _write_expansion_files(self._vault, report)
        return report


# -------------------------------------------------------------- signals


def _gather_signals(vault: VaultPaths) -> _Signals:
    topic = _read_topic(vault)
    index_text = (
        vault.index_md.read_text(encoding="utf-8")
        if vault.index_md.exists()
        else ""
    )
    recent_log = read_recent(vault, n=4)

    # Broken wikilinks, grouped
    broken_raw = find_broken_wikilinks(vault)
    by_target: dict[str, list[str]] = {}
    for referrer, target in broken_raw:
        by_target.setdefault(target, []).append(referrer)
    broken_counts: list[tuple[str, int, list[str]]] = sorted(
        (
            (target, len(referrers), sorted(set(referrers))[:4])
            for target, referrers in by_target.items()
        ),
        key=lambda t: (-t[1], t[0]),
    )[:_MAX_BROKEN]

    # Orphans (source pages already excluded by find_orphans)
    orphans = find_orphans(vault)[:_MAX_ORPHANS]

    # Thin pages
    all_ids = list_pages(vault)
    thin: list[tuple[str, int]] = []
    for page_id in all_ids:
        page = read_page(vault, page_id)
        if page is None or page.type == PageType.SOURCE:
            continue
        wc = len(page.body.split())
        if wc < _THIN_WORD_COUNT:
            thin.append((page_id, wc))
    thin.sort(key=lambda t: t[1])
    thin = thin[:_MAX_THIN]

    # Source summaries — body excerpt per source page
    source_excerpts: list[tuple[str, str, str]] = []
    for src_id in list_pages(vault, PageType.SOURCE)[:_MAX_SOURCE_SUMMARIES]:
        page = read_page(vault, src_id)
        if page is None:
            continue
        body = page.body.strip()
        if len(body) > 2000:
            body = body[:2000] + "\n…[truncated]"
        source_excerpts.append((src_id, page.title, body))

    return _Signals(
        topic=topic,
        page_count=len(all_ids),
        index_text=index_text,
        recent_log=recent_log,
        broken_counts=broken_counts,
        orphan_pages=orphans,
        thin_pages=thin,
        source_summaries=source_excerpts,
    )


def _read_topic(vault: VaultPaths) -> str:
    if not vault.claude_md.exists():
        return ""
    text = vault.claude_md.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("# Vault Schema"):
            _, _, after = line.partition("—")
            return after.strip()
    return ""


# ---------------------------------------------------------- message build


def _build_user_message(s: _Signals) -> str:
    parts: list[str] = []

    parts.append(f"vault_topic: {s.topic or '(unspecified)'}")
    parts.append(f"page_count: {s.page_count}\n")

    parts.append("## vault_index\n")
    parts.append(s.index_text.strip() or "(empty)")
    parts.append("")

    if s.recent_log.strip():
        parts.append("## recent_log\n")
        parts.append(s.recent_log.strip())
        parts.append("")

    if s.broken_counts:
        parts.append("## broken_wikilinks")
        parts.append(
            "Format: <missing-target-slug>  (N references)  e.g. referrers: [...]"
        )
        for target, n, referrers in s.broken_counts:
            ref_list = ", ".join(referrers) if referrers else ""
            parts.append(f"- {target}  ({n} ref{'s' if n != 1 else ''})  referrers: [{ref_list}]")
        parts.append("")

    if s.orphan_pages:
        parts.append("## orphan_pages (no inbound wikilinks, excluding sources)")
        for pid in s.orphan_pages:
            parts.append(f"- {pid}")
        parts.append("")

    if s.thin_pages:
        parts.append("## thin_pages (< %d words)" % _THIN_WORD_COUNT)
        for pid, wc in s.thin_pages:
            parts.append(f"- {pid}  ({wc} words)")
        parts.append("")

    if s.source_summaries:
        parts.append("## source_summaries\n")
        for src_id, title, body in s.source_summaries:
            parts.append(f"### source: {src_id}  —  {title}")
            parts.append(body)
            parts.append("")

    parts.append(
        "Produce the JSON object described in the system prompt. JSON only, "
        "no prose or code fences."
    )
    return "\n".join(parts)


# ------------------------------------------------------------- parsing


def _parse_proposals_dict(data: dict):
    """Parse a proposals dict (already JSON-decoded, e.g. from REPL)."""
    for raw in data.get("proposals", []) or []:
        if not isinstance(raw, dict):
            continue
        try:
            kind = ExpansionKind(raw.get("kind"))
        except ValueError:
            continue
        priority = int(raw.get("priority") or 3)
        priority = max(1, min(5, priority))
        yield ExpansionProposal(
            kind=kind,
            title=(raw.get("title") or "").strip(),
            priority=priority,
            signal=(raw.get("signal") or "").strip(),
            rationale=(raw.get("rationale") or "").strip(),
            related=[str(r) for r in (raw.get("related") or [])],
        )


def _parse_proposals_text(text: str):
    """Parse a JSON-ish string emitted as `FINAL('…')` by an older agent."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return
    try:
        data = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return
    yield from _parse_proposals_dict(data)


# Backwards compat — any stale import of _parse_proposals still works.
_parse_proposals = _parse_proposals_text


# --------------------------------------------------------------- render


_KIND_ORDER: list[tuple[ExpansionKind, str]] = [
    (ExpansionKind.GAP, "Gaps (missing pages)"),
    (ExpansionKind.OPEN_QUESTION, "Open Questions"),
    (ExpansionKind.MISSED_CONNECTION, "Missed Connections"),
    (ExpansionKind.SOURCE_SUGGESTION, "Source Suggestions"),
    (ExpansionKind.THESIS_DRIFT, "Thesis Drift"),
]


# Active/archive split: once a report has more than this many
# proposals, split them by priority. p1-p2 stays in expansion.md as
# the "active" tier the loop draws from. p3-p5 go to
# expansion-archive.md for inspection and future promotion.
EXPANSION_ACTIVE_ARCHIVE_THRESHOLD = 15


def _render_expansion_md(report: ExpansionReport) -> str:
    lines: list[str] = ["# Expansion Proposals", ""]
    header = f"updated {today_iso()} · {len(report.proposals)} proposal" + (
        "s" if len(report.proposals) != 1 else ""
    )
    if report.topic:
        header = f"{report.topic} · " + header
    lines.append(f"*{header}*")
    lines.append("")
    lines.append(
        "> Rank 1 is highest priority. "
        "Sourced from mechanical signals (broken wikilinks, orphans, "
        "thin pages, Reviewer flags) synthesized by the Explorer."
    )
    lines.append("")

    if not report.proposals:
        lines.append("_No proposals yet. Ingest a source or two to populate._")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    for kind, heading in _KIND_ORDER:
        bucket = [p for p in report.proposals if p.kind == kind]
        if not bucket:
            continue
        bucket.sort(key=lambda p: (p.priority, p.title))
        lines.append(f"## {heading}")
        lines.append("")
        for i, p in enumerate(bucket, start=1):
            lines.append(f"### {i}. {p.title or '(untitled)'}")
            lines.append(f"- **priority**: {p.priority}")
            if p.signal:
                lines.append(f"- **signal**: {p.signal}")
            if p.rationale:
                lines.append(f"- **rationale**: {p.rationale}")
            if p.related:
                rel = ", ".join(f"`{r}`" for r in p.related)
                lines.append(f"- **related**: {rel}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _write_empty_report(
    vault: VaultPaths, report: ExpansionReport, topic: str
) -> None:
    vault.meta.mkdir(parents=True, exist_ok=True)
    report.topic = topic
    vault.expansion_md.write_text(
        _render_expansion_md(report), encoding="utf-8"
    )


def _write_expansion_files(
    vault: VaultPaths, report: ExpansionReport
) -> None:
    """Write expansion.md (active tier) + optional expansion-archive.md.

    Small reports (≤ threshold proposals) stay in one file as before.
    Larger reports split: priority 1-2 → expansion.md (what the loop
    consumes), priority 3-5 → expansion-archive.md (browsable backlog,
    promotable when active drains).
    """
    archive_path = vault.meta / "expansion-archive.md"

    if len(report.proposals) <= EXPANSION_ACTIVE_ARCHIVE_THRESHOLD:
        vault.expansion_md.write_text(
            _render_expansion_md(report), encoding="utf-8"
        )
        if archive_path.exists():
            try:
                archive_path.unlink()
            except OSError:
                pass
        return

    active = ExpansionReport(
        timestamp=report.timestamp,
        topic=report.topic,
        proposals=[p for p in report.proposals if p.priority <= 2],
    )
    archive = ExpansionReport(
        timestamp=report.timestamp,
        topic=report.topic,
        proposals=[p for p in report.proposals if p.priority > 2],
    )

    active_md = _render_expansion_md(active)
    if active.proposals and archive.proposals:
        # Point readers at the backlog
        active_md = active_md.rstrip() + (
            "\n\n---\n\n"
            f"_{len(archive.proposals)} lower-priority proposal"
            f"{'s' if len(archive.proposals) != 1 else ''} "
            "archived — see [[expansion-archive]]._\n"
        )
    vault.expansion_md.write_text(active_md, encoding="utf-8")

    if archive.proposals:
        archive_md = _render_expansion_md(archive).replace(
            "# Expansion Proposals",
            "# Expansion Proposals — Archive",
            1,
        )
        archive_md = archive_md.rstrip() + (
            "\n\n---\n\n"
            "_Priority 3-5 proposals. Promote by editing priority to 1 or 2 "
            "before the next explore run, or let them re-surface naturally "
            "as the vault grows._\n"
        )
        archive_path.write_text(archive_md, encoding="utf-8")
    else:
        if archive_path.exists():
            try:
                archive_path.unlink()
            except OSError:
                pass
