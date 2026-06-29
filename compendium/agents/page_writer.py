"""PageWriter — writes (or updates) a single wiki page from a PageChange.

For CREATEs, PageWriter drafts the page from the source content and
whatever cross-references help. For UPDATEs, it reads the existing
page, integrates new material, and emits the merged version.

Runs at the synthesis tier by default (sonnet-class). Lower REPL
iteration budget than Router — the scope is narrower.
"""

from __future__ import annotations

from typing import Any

from compendium.agents.base import BaseAgent
from compendium.models import (
    ModelTier,
    Page,
    PageChange,
    PageChangeOp,
    PageType,
)
from compendium.repl.environment import REPLEnvironment
from compendium.vault import VaultPaths
from compendium.vault.pages import list_pages, read_page, slugify


PAGE_WRITER_PROMPT = """\
You are the PageWriter for a vault on **{topic}**.

You are given a single page-level change and the source that motivated
it. Your job is to produce the final markdown body and frontmatter
fields for this one page. Do NOT work on other pages.

## Your environment

Variables already set in the REPL:
- `change` (dict) — the PageChange: {{op, page_id, page_type, title, reason}}
- `source_content` (str) — the source that motivated this change
- `source_id` (str), `source_title` (str)
- `existing_page` (dict or None) — the current page on disk (UPDATE only)
- `sibling_index` (str) — current index.md, for wikilink targets

Functions:
- `related_pages(limit=5)` -> list of {{id, title, summary, body, tags,
  sources, score}}. **Call this FIRST.** Returns existing pages most
  likely to overlap with your `change` — shared title words, aliases,
  tags, or page type. If any of these mention the same entity/concept
  as your change, you MUST reconcile with them before drafting
  (or you'll contradict facts another page asserts).
- `read_page(page_id)` -> dict or None. Use to look up existing pages
  you might want to link to.
- `list_pages(page_type=None)` -> list[str].
- `slugify(text)` -> kebab-case slug.
- `llm_query(prompt)` -> one flat sub-LLM call.
- `llm_query_many(prompts)` -> list of sub-LLM calls in parallel
  (order-preserving). Use when you want to extract the same kind of
  fact from many chunks without waiting for each round-trip.
- `rlm_query(prompt, context={{...}}, max_iterations=8)` -> spawn a
  bounded child RLM that has its own REPL. Use when a sub-task is
  itself non-trivial (multi-step reasoning, another map-reduce).
  Keep `max_iterations` small (5-10). Depth is capped — nested calls
  past the depth limit will return `[RLM DEPTH CAP]`.
- `partition(text, chunk_size=40000, overlap=0)` -> list[str].
  Map-reduce primitive: chunks are whitespace-aligned where possible.
  Use for dense sources where `source_content` exceeds ~60k chars.
- `remaining_budget()` -> dict with spent_usd, remaining_usd,
  sub_calls_remaining. Check before fanning out wide.
- `view_image(path)` -> dense text description of an image on disk.
  If `source_content` or `existing_page` references a figure with
  `![...](path)` and that figure is relevant to THIS page, call
  view_image() and weave the returned description into your page
  body. Cite the figure's actual content (axis labels, numeric
  values, diagram components) rather than vague "Figure X shows...".
  You may also emit a markdown image link in your body so Obsidian
  renders it inline.
- `web_search(query)`, `fetch_url(url)` -> external info (use sparingly,
  the source is usually enough).
- `FINAL_VAR('page')` to finish.

## Writing rules

- **Reference-grade, not essay-grade.** Dense with facts, wikilinks,
  short paragraphs. No "in this article we will…" throat-clearing.
- **Cross-link generously with [[wikilinks]].** For each entity or
  concept you mention that plausibly has (or should have) its own
  page, emit a wikilink. Broken wikilinks to not-yet-existing pages
  are FINE — they're gap signals for Explorer.

  **Wikilink form (Obsidian-compatible):** targets must be kebab-case
  slugs. Use `[[page-slug]]` when the display is the same as the slug,
  or `[[page-slug|Display Text]]` when you want the prose to read
  differently than the raw slug. Obsidian resolves links by filename,
  so `[[Sacred Geometry]]` will be UNRESOLVED in Obsidian (the file is
  `sacred-geometry.md`), even though our internal tools resolve it.
  Always use: `[[sacred-geometry|Sacred Geometry]]` — NOT `[[Sacred Geometry]]`.
  Run `slugify(text)` if you aren't sure what a title's slug form is.
- **Cite sources** for non-trivial claims, either inline as
  `(see [[source-id]])` or in the `sources` frontmatter list. Claims
  without attribution are a red flag.
- **MANDATORY: preserve formal content verbatim in fenced blocks.**
  Hard requirement, not a suggestion. Applies to every source type
  (papers, articles, transcripts, local files, web fetches).

  Before writing the body, enumerate fenced blocks in
  `source_content` with:

  ```python
  import re
  formal_blocks = re.findall(
      r"```[a-zA-Z]*\n(.*?)\n```", source_content, re.DOTALL
  )
  ```

  Fenced blocks contain whatever formal content the source had that
  can't be losslessly paraphrased — code, pseudocode, equations,
  command snippets, algorithm specs, API signatures, schemas, config,
  query DSL, grammars, data samples, etc.

  For each block, ask one question: *does this block describe a
  thing my page is about?* A block is "about" your page when:
  - it uses the same identifiers your page names (function names,
    classes, commands, operators, formula variables), OR
  - the surrounding prose in the source (the page section that
    contains the block) describes the same concept as your page.

  If the answer is yes, **include the block verbatim in your page
  body** inside the matching fenced-block syntax. Preserve whitespace,
  variable names, string literals, method calls, operators exactly.
  Do NOT paraphrase, summarize, or rewrite into prose.

  **Failure mode to avoid:** a page whose topic is operational (any
  algorithm, architecture, API, protocol, data format, scaffold,
  query, equation, recipe — anything with formal semantics) and
  whose source contains a fenced block that describes that exact
  concept, but whose body describes the block in English instead of
  quoting it. This is an ingest bug, not a stylistic choice. Later
  query-time retrieval needs the actual tokens present in the page.

  If a page's topic is purely conceptual and the source has no
  fenced blocks describing it, no code block is required. The rule
  fires on *available evidence*, not on topic alone.
- **For every figure you describe, also EMBED it.** When you called
  `view_image()` on a figure (or when you're narrating what a figure
  shows based on source text), do BOTH:
  (a) describe the figure's content in prose — numeric values, axis
      labels, diagram components, and any code it contains as a
      fenced block — so the content is text-retrievable for later
      queries, AND
  (b) embed the figure inline with a markdown image link so Obsidian
      renders it in the page.
  Paths: the source's companion .md lives at `raw/papers/<source-slug>.md`
  or `raw/articles/<source-slug>.md`, and the images it references
  with `![...](../assets/<source-slug>/fig-pNNN-MM.png)`. Your page
  lives two levels deeper at `wiki/<type>/<your-slug>.md`, so rewrite
  the path as `../../raw/assets/<source-slug>/<filename>`. The
  `source_id` context variable gives you `<source-slug>`.
- **For UPDATE:** integrate — don't replace. Preserve earlier claims
  that the new source doesn't contradict. If the source contradicts
  an existing claim, DO NOT silently overwrite; add an
  `## Open questions` section describing the tension and naming the
  disagreeing sources.
- **Start the body with `# {{title}}`** as the top heading.
- **Keep the page focused.** Target 300–900 words for a well-formed
  page. The hard ceiling is {max_tokens} tokens (~{word_ceiling} words).

## Source-type pages (page_type == "source")

These summarize an ingested source. Structure:
1. `# Title`
2. 2–4 sentence abstract
3. "## Key claims" — bulleted, each with a brief note
4. "## Entities mentioned" and "## Concepts introduced" — bulleted
   wikilinks (targets may or may not yet exist)
5. Optional "## Notes" for context (author, date, methodology, bias)

## Evidence metadata — **required on every page**

You must assign two fields that the Mender uses for rule-based
contradiction resolution. These are structural judgments, not prose.

**`source_tier`** — the evidence tier this page rests on. Pick exactly one:

- `"primary"` — the source IS the subject (original paper, canonical
  documentation, historical document, a practitioner's own writing/talk
  about their own method, dataset card for its own dataset, spec/RFC,
  court filing, first-hand account).
- `"secondary"` — analysis or review OF a primary source by someone
  other than the originator (blog post explaining a paper, a literature
  review, a journalist's interview summary, a textbook chapter
  synthesizing multiple primaries).
- `"tertiary"` — aggregation or summary-of-summaries (Wikipedia-style
  overview, encyclopedic listing, a "top 10 X" roundup).

For **SOURCE-type pages**: this is the tier of THIS source. Check the
provenance header at the top of `source_content` (look for
`<!-- source_type: paper|article|transcript -->`). Papers and
transcripts-of-originator default to primary; articles depend — read
the content and decide.

For **non-source pages** (concept/entity/synthesis): this is the BEST
tier among cited sources — if any cited source is primary, set
`"primary"`; otherwise use the best tier present.

**`confidence`** — how confident you are in the core factual claims of
the page body. Pick exactly one:

- `"high"` — claims are directly stated in a primary source, specific
  (named numbers, dates, proper nouns, exact definitions), and either
  corroborated by another cited source OR self-evident from the source
  text itself.
- `"medium"` — claims are well-supported by the source(s) but involve
  some interpretation, or the source is secondary/tertiary, or there
  is only a single source and the claims are non-trivial.
- `"low"` — claims are speculative, extrapolated, inferred from weak
  evidence, or the source is a tertiary aggregation with no primaries
  to chase. Also use `"low"` if the page is mostly stub-level and
  you're establishing the topic more than the facts.

Pick `"low"` over `"medium"` when in doubt. Over-confident pages that
get contradicted later cause the Mender to auto-supersede them; a
`"low"` page just asks a future iteration to corroborate.

## Output format

Build a dict named `page`:

```python
page = {{
    "id": str,                  # = change['page_id']
    "title": str,               # = change['title']
    "type": str,                # = change['page_type']
    "summary": str,             # ONE line for index.md
    "tags": [str],
    "aliases": [str],           # alternate names (collapses synonyms)
    "sources": [str],           # source page IDs this draws from;
                                # ALWAYS include source_id if the source
                                # contributed to this page
    "source_tier": str,         # "primary" | "secondary" | "tertiary"
    "confidence": str,          # "high" | "medium" | "low"
    "body": str,                # full markdown body with # heading
}}
```

Then `FINAL_VAR('page')`.

## Opening moves

1. `print(change)` and `print(source_id, source_title)`.
2. **Sibling-awareness — MANDATORY**:
   ```python
   siblings = related_pages(limit=5)
   for s in siblings:
       print(f"[{{s['score']}}] {{s['id']}} ({{s['type']}}): {{s['summary']}}")
   ```
   For each sibling whose body makes claims about the same entity or
   concept as your change, READ the body (it's already in `s['body']`
   — no extra call). Your draft MUST be consistent with those claims.
   If you find a contradiction you can't reconcile, add an
   `## Open questions` section to YOUR page naming the disagreement
   and both page IDs — do NOT silently assert the conflicting version.
3. If UPDATE: `print(existing_page['body'][:2000])`.
4. **Find code blocks in the source relevant to this page:**
   ```python
   import re
   code_blocks = re.findall(r"```[a-zA-Z]*\n(.*?)\n```", source_content, re.DOTALL)
   print(f"{{len(code_blocks)}} code blocks found in source")
   for i, cb in enumerate(code_blocks[:8]):
       print(f"--- code block {{i}} ({{len(cb)}} chars) ---")
       print(cb[:500])
   ```
   For every block that relates to this page's topic, you MUST
   include it verbatim in your page body. No paraphrasing.
5. `print(source_content[:3000])` — for prose context.
6. **If `source_content` is dense (>60 000 chars), use map-reduce
   instead of reading the whole thing into your own context.** The
   REPL has the primitives for this:

   ```python
   if len(source_content) > 60_000:
       chunks = partition(source_content, chunk_size=40000, overlap=500)
       # Ask each chunk the SAME focused question about this page.
       qs = [
           f"From this excerpt, extract every fact, claim, quote, or "
           f"code block directly about '{{change['title']}}'. "
           f"Preserve fenced code blocks VERBATIM. Ignore anything "
           f"unrelated. If nothing relevant, return 'N/A'.\\n\\n"
           f"EXCERPT:\\n{{c}}"
           for c in chunks
       ]
       b = remaining_budget()
       if b['remaining_usd'] < 0.20 or b['sub_calls_remaining'] < len(qs):
           # Tight budget — sequential, one chunk at a time, stop early
           # once we have enough material.
           per_chunk = [llm_query(q) for q in qs[:4]]
       else:
           per_chunk = llm_query_many(qs)
       print(f"gathered {{len([p for p in per_chunk if 'N/A' not in p])}} relevant chunks")
   ```
   Then draft the body from `per_chunk` rather than from
   `source_content[:3000]` — that truncation loses 95% of a dense
   paper.

7. Draft the body (with the required code blocks). Assemble the
   `page` dict. Call `FINAL_VAR('page')`.
"""


class PageWriter(BaseAgent):
    """Writes a single wiki page from a PageChange + source context."""

    def __init__(self, *args, vault: VaultPaths, **kwargs):
        tiered = kwargs.pop("tiered", None)
        if tiered is not None:
            kwargs.setdefault("model_override", tiered.get_model(ModelTier.SYNTHESIS))
            # Sub-calls default to the same tier as the parent: per the RLM
            # paper, child llm_query/rlm_query invocations are full agents
            # that may themselves reason and delegate, not cheap summarizers.
            # Downgrading to the mechanical tier here silently hurt quality
            # on long-context work (see docs/compendiums-meet-rlm.md).
            kwargs.setdefault(
                "sub_call_model_override",
                tiered.get_model(ModelTier.SYNTHESIS),
            )
        super().__init__(*args, **kwargs)
        self._vault = vault

    def get_system_prompt(self) -> str:
        topic = _read_vault_topic(self._vault)
        max_tokens = self.guardrails.max_tokens_per_page
        word_ceiling = int(max_tokens * 0.75)  # rough tokens->words
        return PAGE_WRITER_PROMPT.format(
            topic=topic or "(unspecified topic)",
            max_tokens=max_tokens,
            word_ceiling=word_ceiling,
        )

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
        # Sibling-awareness: highest-overlap existing pages for the
        # current change. Fights cross-page factual drift (the audit's
        # "MBV formed in 1983 vs 1984 across 4 pages" failure mode) by
        # letting the Writer reconcile facts with siblings BEFORE drafting.
        repl.register_function(
            "related_pages",
            lambda limit=5: _related_pages_for_repl(
                vault, repl, limit=limit,
            ),
        )
        repl.register_function("slugify", slugify)

    def write(
        self,
        *,
        change: PageChange,
        source_id: str,
        source_title: str,
        source_content: str,
        sibling_index: str,
    ) -> Page:
        """Run the writer REPL and return a validated Page."""
        existing = read_page(self._vault, change.page_id)
        context: dict[str, Any] = {
            "change": {
                "op": change.op.value,
                "page_id": change.page_id,
                "page_type": change.page_type.value,
                "title": change.title,
                "reason": change.reason,
            },
            "source_id": source_id,
            "source_title": source_title,
            "source_content": source_content,
            "sibling_index": sibling_index,
            "existing_page": _read_page_for_repl(self._vault, change.page_id)
            if existing is not None
            else None,
        }

        max_iter = max(10, self.guardrails.max_repl_iterations // 2)
        raw = self.run(context, max_iterations_override=max_iter)
        if not isinstance(raw, dict):
            raise ValueError(
                f"PageWriter returned {type(raw).__name__}, expected dict"
            )
        return _parse_page(raw, change=change, source_id=source_id)


def _read_vault_topic(vault: VaultPaths) -> str:
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


def _related_pages_for_repl(
    vault: VaultPaths,
    repl: "REPLEnvironment",
    *,
    limit: int = 5,
) -> list[dict]:
    """Find pages likely related to the Writer's current `change` variable.

    Scoring: title-word overlap (Jaccard on slug-tokens) + page-type
    match + sources-overlap when known. The Writer can rely on this
    instead of listing every page and picking favorites — we're giving
    it the 5 most-likely siblings so it can reconcile facts first.

    Returns `[{id, title, summary, body, tags, sources, score}]`.
    """
    import re as _re

    # Fish the `change` dict out of the REPL namespace. REPLEnvironment
    # exposes `get(name)` for variable retrieval; fall back to the
    # private `_namespace` dict if the API changes.
    change = None
    if hasattr(repl, "get"):
        change = repl.get("change")
    if change is None:
        change = getattr(repl, "_namespace", {}).get("change")
    change = change or {}
    change_title = (change.get("title") or "").strip()
    change_type = (change.get("page_type") or "").strip()
    change_page_id = (change.get("page_id") or "").strip()

    if not change_title:
        return []

    _STOPWORDS = {
        "the", "a", "an", "and", "or", "of", "to", "for", "in", "on",
        "at", "by", "with", "from", "as", "is", "are", "be",
    }

    def _tokens(s: str) -> set[str]:
        return {
            t for t in _re.findall(r"[a-z0-9]+", s.lower())
            if t and t not in _STOPWORDS and len(t) > 1
        }

    target_tokens = _tokens(change_title)
    if not target_tokens:
        return []

    scored: list[tuple[float, dict]] = []
    for pid in list_pages(vault):
        if pid == change_page_id:
            continue  # don't include self
        page = read_page(vault, pid)
        if page is None:
            continue
        other_tokens = _tokens(page.title) | _tokens(
            " ".join(page.aliases or [])
        ) | _tokens(" ".join(page.tags or []))
        if not other_tokens:
            continue
        overlap = len(target_tokens & other_tokens)
        if overlap == 0:
            continue
        # Jaccard index, slightly boosted when page-type matches.
        jaccard = overlap / len(target_tokens | other_tokens)
        score = jaccard + (0.1 if page.type.value == change_type else 0.0)
        scored.append((
            score,
            {
                "id": page.id,
                "title": page.title,
                "type": page.type.value,
                "summary": page.summary,
                "tags": list(page.tags),
                "sources": list(page.sources),
                "updated": page.updated,
                "body": page.body,
                "score": round(score, 3),
            },
        ))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [d for _, d in scored[:limit]]


def _parse_page(data: dict, *, change: PageChange, source_id: str) -> Page:
    page_id = data.get("id") or change.page_id
    page_type_raw = data.get("type") or change.page_type.value
    try:
        page_type = PageType(page_type_raw)
    except ValueError:
        page_type = change.page_type

    sources = list(data.get("sources") or [])
    # Guarantee the triggering source is recorded on the page.
    if change.op == PageChangeOp.CREATE or source_id not in sources:
        if source_id not in sources:
            sources.append(source_id)

    body = (data.get("body") or "").strip()
    if not body.startswith("# "):
        body = f"# {data.get('title') or change.title}\n\n{body}".rstrip()

    # Evidence metadata — validate against the allowed vocabularies.
    # Unknown values degrade silently to "" so the Mender's rule branch
    # treats the page as unspecified (and falls back to LLM judgment)
    # rather than tripping a schema error and losing the whole write.
    source_tier = (data.get("source_tier") or "").strip().lower()
    if source_tier not in {"primary", "secondary", "tertiary"}:
        source_tier = ""
    confidence = (data.get("confidence") or "").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = ""

    return Page(
        id=page_id,
        title=data.get("title") or change.title,
        type=page_type,
        summary=(data.get("summary") or "").strip(),
        tags=list(data.get("tags") or []),
        aliases=list(data.get("aliases") or []),
        sources=sources,
        updated="",  # write_page() will stamp today's date
        source_tier=source_tier,
        confidence=confidence,
        body=body,
    )
