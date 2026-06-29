"""IngestRouter — decides which wiki pages a new source creates or updates.

The Router reads the source, scans the existing vault index, and produces
an IngestPlan. It does NOT write page bodies — that's PageWriter's job.

Uses REPL so the Router can iteratively read existing pages before
deciding whether a mention warrants a CREATE, an UPDATE, or just an
implied-wikilink (gap signal for Explorer).
"""

from __future__ import annotations

from typing import Any

from compendium.agents.base import BaseAgent
from compendium.models import (
    IngestPlan,
    ModelTier,
    PageChange,
    PageChangeOp,
    PageType,
)
from compendium.repl.environment import REPLEnvironment
from compendium.vault import VaultPaths
from compendium.vault.pages import list_pages, read_page, slugify


INGEST_ROUTER_PROMPT = """\
You are the IngestRouter for a vault on **{topic}**.

## Your task

Given a source document, produce an IngestPlan describing which wiki
pages to CREATE and which to UPDATE. You are NOT writing page bodies —
PageWriter will do that next. Your job is to decide the shape of the
change.

## Your environment

A persistent Python REPL with these variables already set:
- `source_content` (str) — the full source text
- `source_title` (str) — human-readable source title
- `source_id` (str) — kebab-case slug for the source
- `source_token_count` (int) — approximate token count of source_content
- `source_size_tier` (str) — "short" | "medium" | "dense" (derived from
   source_token_count; sets your budget — see "Routing rules" below)
- `vault_topic` (str) — the vault's topic
- `vault_index` (str) — current contents of index.md
- `recent_log` (str) — last few log entries (recent activity context)

Functions:
- `read_page(page_id)` -> dict or None. Fields: id, title, type, summary,
  tags, aliases, sources, updated, body.
- `list_pages(page_type=None)` -> list[str]. page_type ∈ {{"entity","concept","source","synthesis"}} or None.
- `slugify(text)` -> kebab-case ASCII slug.
- `llm_query(prompt)` -> one flat sub-LLM call.
- `llm_query_many(prompts)` -> parallel fan-out, returns list in order.
  Use for "ask the same extraction question across many chunks."
- `rlm_query(prompt, context={{...}}, max_iterations=8)` -> spawn a
  bounded child RLM (depth-capped). Use when a sub-question is itself
  non-trivial (e.g. "is this concept already covered by any of these
  20 existing pages?").
- `partition(text, chunk_size=40000, overlap=0)` -> list[str] —
  map-reduce primitive for chunking long sources.
- `remaining_budget()` -> dict; check before wide fan-out.
- `view_image(path)` -> dense text description of an image on disk.
  Call this when `source_content` contains `![...](path)` references
  to figures that look load-bearing (surrounding text uses "Figure N
  illustrates/shows/depicts/compares/plots..."). The description
  becomes part of your reasoning for routing — e.g. an architecture
  figure may tell you to CREATE a dedicated entity page for each
  labelled component. Don't view every thumbnail; be selective.
- `web_search(query)`, `fetch_url(url)` -> external info.
- `FINAL_VAR('plan')` to finish.

## Routing rules

1. **Always include exactly one `source` page** with op='create':
   - page_id = source_id (already provided)
   - page_type = "source"
   - reason: 1-2 sentences describing what the source is and what it argues.

2. **Scan source_content for entities and concepts that matter to the
   source's argument.** Entities are proper-noun nouns (people, works,
   organizations, places). Concepts are ideas, theories, frameworks,
   phenomena.

3. **Identify PRIMARY concepts — they all need pages.** A concept
   is "primary" (as opposed to incidental) when ANY of these hold:

   - **Named section heading.** The source dedicates a section,
     sub-section, or captioned figure to it.
   - **Defined signature.** It has a stated function signature,
     API, algorithm step, equation, or data schema.
   - **Multi-section presence.** The source refers to it across two
     or more distinct sections or pages, not just in passing.
   - **Named cited work.** The source introduces it with an explicit
     author + title + year citation (e.g., a prior paper it builds
     on or argues against).
   - **Named baseline / comparison target.** The source contrasts or
     compares itself against it ("X vs. Y", "unlike X", "X fails at").
   - **Named primitive / mechanism.** An underlying function,
     environment, runtime, or substrate that the source's argument
     rests on — even if the source treats it as "obvious."
   - **Named organization / institution / dataset / tool / model.**
     Any proper noun the source uses as a load-bearing reference.

   Every primary concept becomes a CREATE or UPDATE entry in your
   plan. Never relegate a primary concept to `implied_wikilinks`.

4. **For each candidate**, decide:
   - **UPDATE** — a page already exists (check vault_index, then
     `read_page()` to confirm). Reason: what the source adds (a new
     claim, context, contradiction, missing source, etc.).
   - **CREATE** — no page exists and the candidate is primary (by
     the test above). Reason: what the new page will cover.
   - **`implied_wikilinks`** — the concept is INCIDENTAL (mentioned
     once, in passing, tangential to the source's argument). Don't
     stub a thin page; list the slug and let Explorer surface it
     later if it turns out to matter.

5. **Prefer UPDATE over CREATE** when a close page exists, even if
   the source uses slightly different terminology. Aliases collapse
   synonyms; duplicate pages are harder to reconcile later.

6. **Forward-reference audit (mandatory before finalizing).** Before
   you call `FINAL_VAR('plan')`, mentally walk through each page
   you're planning to create or update and ask: *what `[[wikilinks]]`
   will this page's body contain?* For every wikilink target that
   appears in **two or more** pages' anticipated bodies, that target
   must itself be in your CREATE list. If it isn't, promote it.

   Common under-scoping smell: Router proposes pages that
   collectively reference `[[X]]` 5+ times, but `X` is in
   `implied_wikilinks` because "it's really part of the main
   concept." That's wrong — a target referenced 5+ times is
   primary by reference-count alone, and downstream pages will
   otherwise orphan-link.

   Rule of thumb: after listing a source's primitives, baselines,
   cited works, datasets, tools, models, and named mechanisms,
   you should typically have more pages than you initially
   imagined. A paper on X produces pages for X itself PLUS X's
   substrate, X's baselines, X's comparison targets, X's
   significant cited prior work, and X's named implementation
   pieces.

5. **Scale the plan to source density.** Different sources warrant
   different coverage. Use `source_size_tier` to size your budget:

   - **short** (< 4 000 tokens; typical web article / blog post):
     2–5 page changes, 5–15 implied wikilinks. Keep it tight; prefer
     UPDATE over CREATE.
   - **medium** (4 000–15 000 tokens; long article, podcast summary,
     short paper): 5–12 page changes, 15–40 implied wikilinks.
   - **dense** (15 000+ tokens; full academic papers, long
     transcripts, book chapters): **10–25 page changes, 40–100+
     implied wikilinks.** Dense papers routinely mention 50+
     distinct entities, concepts, algorithms, benchmarks, datasets,
     authors, and tools. Do NOT under-scope them — the vault's value
     comes from comprehensive coverage of what the source actually
     discusses. The source summary page alone cannot capture a
     38-page paper; you need pages for each central concept,
     landmark algorithm, author, benchmark, and dataset. Things that
     appear once and matter less go in `implied_wikilinks`, not
     omitted entirely.

   A short source with 15 page changes is a smell. A dense source
   with 4 page changes is ALSO a smell — it means the Router
   systematically under-represented what the source contains.

## Output format

Build a dict named `plan` and call `FINAL_VAR('plan')`:

```python
plan = {{
    "source_id": str,
    "source_title": str,
    "source_summary": str,              # 1-2 sentences: what is the source?
    "changes": [
        {{
            "op": "create" | "update",
            "page_id": str,             # kebab-case; use slugify() for new
            "page_type": "entity" | "concept" | "source" | "synthesis",
            "title": str,               # human-readable
            "reason": str,              # 1-3 sentences, fed to PageWriter
        }},
        ...
    ],
    "implied_wikilinks": [str],         # slugs not central enough to create
    "rationale": str,                   # 1 paragraph on the plan overall
}}
```

## Opening moves (do these first)

1. `print(source_title, source_size_tier, source_token_count)`
2. `print(source_content[:3000])`
3. `print(vault_index)` — what's already here
4. `read_page(...)` on any candidates before deciding CREATE vs UPDATE.
5. **Scan for primary concepts by structure:** extract section
   headings, captions, cited works, and named primitives from
   `source_content`. For dense sources, `re.findall` helps here —
   pulling every `^##+\\s+.+` line gives you the section index in
   seconds. Every primary should land in CREATE or UPDATE.
6. **For dense sources (>60 000 chars), enumerate candidate pages
   via map-reduce rather than a single-pass read.** Pattern:

   ```python
   if source_token_count > 15_000:  # matches "dense" tier
       chunks = partition(source_content, chunk_size=40000, overlap=400)
       extract_q = (
           "List every entity (person, work, organization, dataset, "
           "tool, model, benchmark) and every concept (idea, algorithm, "
           "framework, primitive, baseline) the author treats as load-"
           "bearing in THIS excerpt. Format: 'entity: <name>' or "
           "'concept: <name>', one per line. Ignore incidental mentions."
           "\\n\\nEXCERPT:\\n{{c}}"
       )
       per_chunk = llm_query_many(
           [extract_q.format(c=c) for c in chunks]
       )
       # Merge and dedupe — the union of per-chunk candidates is your
       # CREATE/UPDATE pool. Dense papers routinely yield 50+ distinct
       # candidates here; under-scoping a dense source is an ingest bug.
   ```
7. **Before FINAL_VAR, run the forward-reference audit** (rule 6
   above). Promote under-scoped targets into CREATE.

Then build the plan and call `FINAL_VAR('plan')`.
"""


class IngestRouter(BaseAgent):
    """Produces an IngestPlan for a source against the current vault."""

    def __init__(self, *args, vault: VaultPaths, **kwargs):
        # IngestRouter runs on the strategic tier — routing is judgment-heavy.
        tiered = kwargs.pop("tiered", None)
        if tiered is not None:
            kwargs.setdefault("model_override", tiered.get_model(ModelTier.STRATEGIC))
            # Sub-calls on synthesis tier: Router uses llm_query to probe
            # dense source sections and assess whether an entity warrants a
            # dedicated page. That's reasoning work, not bookkeeping — the
            # mechanical tier routinely missed load-bearing entities in
            # long papers.
            kwargs.setdefault(
                "sub_call_model_override",
                tiered.get_model(ModelTier.SYNTHESIS),
            )
        super().__init__(*args, **kwargs)
        self._vault = vault

    def get_system_prompt(self) -> str:
        topic = _read_vault_topic(self._vault)
        return INGEST_ROUTER_PROMPT.format(topic=topic or "(unspecified topic)")

    def _register_standard_functions(self, repl: REPLEnvironment) -> None:
        super()._register_standard_functions(repl)
        vault = self._vault
        repl.register_function(
            "read_page",
            lambda page_id: _read_page_for_repl(vault, page_id),
        )
        repl.register_function(
            "list_pages",
            lambda page_type=None: _list_pages_for_repl(vault, page_type),
        )
        repl.register_function("slugify", slugify)

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
        """Run the routing REPL and return a validated IngestPlan."""
        token_count = _estimate_tokens(source_content)
        size_tier = _size_tier(token_count)
        context: dict[str, Any] = {
            "source_content": source_content,
            "source_title": source_title,
            "source_id": source_id,
            "source_token_count": token_count,
            "source_size_tier": size_tier,
            "vault_topic": vault_topic,
            "vault_index": vault_index,
            "recent_log": recent_log,
        }
        # Dense sources genuinely need more REPL turns — Router reads
        # more existing pages before deciding create vs update. Bump the
        # iteration budget proportionally.
        max_iter = None
        if size_tier == "dense":
            max_iter = int(self.guardrails.max_repl_iterations * 1.5)
        raw = self.run(context, max_iterations_override=max_iter)
        if not isinstance(raw, dict):
            raise ValueError(
                f"IngestRouter returned {type(raw).__name__}, expected dict"
            )
        return _parse_plan(raw, source_id=source_id, source_title=source_title)


_TIKTOKEN_ENCODER = None


def _estimate_tokens(text: str) -> int:
    """Approximate token count. Uses tiktoken's cl100k_base encoder
    (close enough for Anthropic models; within ~10% of their tokenizer).
    Falls back to a 1-token-per-4-chars heuristic if tiktoken isn't
    available or the encode call fails."""
    global _TIKTOKEN_ENCODER
    if _TIKTOKEN_ENCODER is None:
        try:
            import tiktoken

            _TIKTOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _TIKTOKEN_ENCODER = False
    if _TIKTOKEN_ENCODER:
        try:
            return len(_TIKTOKEN_ENCODER.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def _size_tier(token_count: int) -> str:
    if token_count < 4_000:
        return "short"
    if token_count < 15_000:
        return "medium"
    return "dense"


def _read_vault_topic(vault: VaultPaths) -> str:
    """Extract the topic line from the vault's CLAUDE.md, if present."""
    if not vault.claude_md.exists():
        return ""
    text = vault.claude_md.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("# Vault Schema"):
            _, _, after = line.partition("—")
            return after.strip() or line.removeprefix("# Vault Schema").strip(" —")
    return ""


def _read_page_for_repl(vault: VaultPaths, page_id: str) -> dict | None:
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


def _list_pages_for_repl(vault: VaultPaths, page_type: str | None) -> list[str]:
    pt = PageType(page_type) if page_type else None
    return list_pages(vault, pt)


def _parse_plan(data: dict, *, source_id: str, source_title: str) -> IngestPlan:
    changes: list[PageChange] = []
    for raw in data.get("changes", []) or []:
        try:
            changes.append(
                PageChange(
                    op=PageChangeOp(raw["op"]),
                    page_id=slugify(raw["page_id"]) or raw["page_id"],
                    page_type=PageType(raw["page_type"]),
                    title=raw.get("title") or raw["page_id"],
                    reason=raw.get("reason", ""),
                )
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid change in plan: {raw} ({exc})") from exc

    plan = IngestPlan(
        source_id=data.get("source_id") or source_id,
        source_title=data.get("source_title") or source_title,
        source_summary=data.get("source_summary", ""),
        changes=changes,
        implied_wikilinks=list(data.get("implied_wikilinks") or []),
        rationale=data.get("rationale", ""),
    )
    _ensure_source_change(plan)
    return plan


def _ensure_source_change(plan: IngestPlan) -> None:
    """Guarantee the plan creates a source summary page for this ingest."""
    for ch in plan.changes:
        if ch.page_type == PageType.SOURCE and ch.page_id == plan.source_id:
            return
    plan.changes.insert(
        0,
        PageChange(
            op=PageChangeOp.CREATE,
            page_id=plan.source_id,
            page_type=PageType.SOURCE,
            title=plan.source_title,
            reason=f"Summary page for source: {plan.source_summary or plan.source_title}",
        ),
    )
