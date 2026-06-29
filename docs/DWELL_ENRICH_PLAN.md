# Dwell — Universal Ingest Enrichment (`cli.py enrich`) — plan

> **Status: PHASE A + PHASE B SHIPPED + validated (2026-06-23).**
> Phase B (`--mode hybrid`): `compendium/agents/enrichment.py` `type_edges_llm()` types the
> untyped wikilink edges + lifts 2–5 atomic propositions on the **top-N nodes by centrality**
> (`--top` frac / `--top-n`), via Haiku (`--model`, default `claude-haiku-4-5`), **budget-capped**
> (`--max-cost`, default $2) and **content-hash-gated** (re-runs reuse unchanged nodes for free).
> CLI: `cmd_enrich` gained `--mode {mechanical,hybrid} --top --top-n --model --max-cost`; default
> stays `mechanical` (Phase B is opt-in). **Validated live on Pythagoras**: ~$0.007/node;
> top-3 then top-5 → quality is high (`derives-from→thales`/`thales` studied-under, `part-of→croton`,
> `exemplifies→pythagorean-theorem`, `related` honest-fallback for `samos`, varied predicate
> distribution, real evidence phrases); propositions are clean atomic facts with salience
> (iamblichus 0.95, plato 0.9). Idempotency confirmed: re-running top-5 reused 3 unchanged + made
> only 2 new calls. Graceful budget stop (`stopped_budget`). No-auth → skips Phase B with a notice.
> *(Deferred refinement: grounding the LLM propositions via `grounding._ground_one`.)*
>
> **Full Pythagoras typing done (2026-06-23):** top-20% (~36 nodes) → 302 LLM-typed edges +
> 166 propositions for **$0.196** (reused the 5 from the validation run for free). The vault now
> has 428 typed edges + 788 claims.
>
> **First Tier-2 CONSUMER SHIPPED — the Timeline view (2026-06-23).** `GET /timeline?session=&min_conf=`
> in `dwell_server.py` reads `enrichment-temporal.json`, thresholds conf (default 0.7 = era-explicit
> dates+periods; drops the 0.55 bare-year noise → 1119→313 events on Pythagoras), resolves node
> titles, sorts by year. Frontend: `Timeline.svelte` overlay (mirrors `Missed`), `dwell.showTimeline/`
> `hideTimeline/jumpToEvent`, `api.timeline`, `TimelineEvent`/`TimelineData` types, a "🕒 Timeline"
> sidebar button. **Gotcha (fixed):** a new endpoint must be added to the Vite proxy allow-list
> (`vite.config.ts` `apiRoutes` regex) AND the dev server restarted, or the proxy serves the SPA
> shell and the client throws "Unexpected token '<'". **Verified e2e in the reader** (dry session):
> overlay renders 313 events with a BCE/CE divider; clicking an event seeds a thread at that node.
>
> **Phase A** (`cli.py enrich --vault <path> [--no-claims]`) runs the free mechanical core:
> `compendium/agents/enrichment.py` (`enrich_vault` → `save_enrichment`) + four `VaultPaths`
> sidecar paths + the `enrich` subcommand. **Validated on two different-domain vaults** (content-
> neutral): Pythagoras (176 nodes → 2634 edges incl. 126 `contradicts` from the ledger, 1119
> temporal anchors, 149 terms, 622 quote-claims) and art-critique (121/875/575/101). Centrality
> ranks `pythagoras` 1.00 / `neoplatonism` 0.74 (matches Dwell's popular-nodes); the timeline is
> clean BCE-first ("3101 BCE" … "c. 570 BCE"), now/future bare-year metadata filtered out
> (content-neutral: drop years ≥ the authoring year). Bare years are conf 0.55 (citation-ish) vs
> era-explicit 0.8–0.92 → **the timeline view should threshold on conf (~0.7) and let the reader
> opt into low-conf years.** Self-contained spec below.
>
> **Original status: PLANNED (2026-06-23).** Self-contained spec so a fresh window can build it.
> The upstream half of the creed: ONE content-agnostic enrichment pass that serves every
> Tier-2 transform (timeline / comparison / study-guide / glossary / concept-map), never
> per-output curation. Companion to `DWELL_CREED.md`, `DWELL_ROADMAP.md` (see its "PARALLEL
> UPSTREAM" section), and `DWELL_TEXT_FIGURES_PLAN.md` (the downstream consumer).
>
> **Decisions locked with the user (2026-06-23):**
> - **Approach = HYBRID.** A free mechanical core on every node + a *bounded* LLM pass that
>   types edges and extracts propositions on **high-salience nodes only** (top-N by centrality,
>   budget-capped). Not full-LLM-per-node (MPH has 1,069 nodes — cost + time).
> - **First unlocks = Timeline · Comparison/concept-map · Study-guide/quiz** (NOT glossary first;
>   terms are emitted anyway since they're nearly free). ⇒ prioritize **temporal anchors**
>   (mechanical), **typed edges** (the LLM step), and the **claims layer** (free from grounding +
>   richer from the LLM pass).

---

## The reframe: 4 of the 5 data points are mostly FREE (reuse, don't rebuild)
The exploration of the codebase found the machinery already exists:

| Data point | How we get it | Cost | Reuses |
|---|---|---|---|
| **Salience / centrality** | `[[wikilinks]]` → `build_backlinks()` in-degree (+ optional PageRank) | **$0** | `compendium/vault/links.py` (the graph Dwell's "popular nodes" already uses) |
| **Terms → gloss** | every `entity`/`concept` page IS a term: `title`+`summary`+`aliases` | **$0** | page frontmatter (`pages.py` `read_page`) |
| **Claims + provenance** | the grounding engine already extracts quoted passages + verifies vs. cited raw sources (grounded/loose/not-found) | **$0** | `compendium/agents/grounding.py` `ground_vault()` → `QuoteCheck` |
| **`contradicts` edges** | the contradiction-ledger already stores them by page-pair | **$0** | `compendium/vault/contradiction_ledger.py` |
| **Temporal anchors** | regex over bodies for dates/periods ("c. 570 BCE", "1492", "5th c. BCE") | **~$0** | new small extractor |

**The ONE thing that genuinely needs an LLM: typing the edges.** The graph exists (every wikilink is
an edge) but the links are **untyped** — only a model can label a link `precedes` / `requires` /
`influences` / `derives-from` / `part-of` / `causes` / `exemplifies`. Same model call can also lift a
few **atomic propositions** (richer claims than verbatim quotes). That LLM work is bounded to
high-salience nodes (hybrid).

---

## Architecture
A new **`cli.py enrich`** command (registered exactly like `cmd_ground`, argparse subparser →
`cmd_enrich(args)`). Two phases, additive over everything that already exists (prose + wikilinks +
`sources:` + grounding + contradiction ledger + centrality):

**Phase A — mechanical core ($0, runs on every node, always):**
1. Build the graph from wikilinks (`build_backlinks`/`parse_wikilinks`), compute **in-degree +
   centrality** → `salience`.
2. Emit **every wikilink as an edge** `{target, type: null, via:"wikilink"}` (the untyped backbone).
3. Merge **`contradicts`** edges from the contradiction-ledger.
4. **Terms**: each `entity`/`concept` page → `{term: title, gloss: summary, aliases}`.
5. **Temporal**: regex each body for dates/periods/durations → normalized `year`/`span`.
6. **Quote-claims**: run `ground_vault()` (or reuse its cached report) → claims with provenance +
   grounding verdict.

**Phase B — bounded LLM typing pass (hybrid; high-salience nodes only, budget-capped):**
7. Take the **top-N nodes by centrality** (`--top`, default e.g. top 20% or `--budget`-bounded).
   For each, one LLM call: given the node (title + body or summary) + its outgoing wikilink targets
   (with target titles/summaries) + the fixed predicate set, return JSON:
   - each edge → a **type** (or `"related"` if none fit) + `conf` + a short `evidence` phrase;
   - 2–5 **atomic propositions** (claim text + salience), for the claims layer.
   Skip nodes whose `content_hash` is unchanged since the last enrich (idempotent re-runs → only
   new/changed nodes re-typed). Model tier configurable (`--model`); **default Haiku** (~$0.005/node:
   MPH top-20% ≈ 214 nodes ≈ ~$1; Pythagoras top-20% ≈ 30 nodes ≈ ~$0.30). Local `gemma4:e4b` = $0
   fallback; Sonnet for quality. Cost tracked via `CostTracker`, hard budget via `check_budget()`.

`enrich` is **idempotent + incremental**: Phase A always recomputes (cheap); Phase B is content-hash
gated so re-runs are nearly free. Safe to run after every `ingest` (like `lint`/`ground`).

---

## The `_meta/` sidecars (atomic write, schema-versioned — same pattern as `contradiction-ledger.json`)
Written under `wiki/_meta/` via the tmp-file + `os.replace` pattern (`contradiction_ledger.py:161`).
Add `VaultPaths` properties (`layout.py`) for each. Four files, one per data point (so a consumer
loads only what it needs; a vault missing a dimension simply lacks that file — graceful, not broken):

**`enrichment-graph.json`** — typed graph + salience (drives comparison / concept-map / dependency walks)
```json
{ "version": 1, "timestamp": "2026-06-23", "method": "hybrid",
  "nodes": {
    "pythagoras": {
      "in_degree": 181, "centrality": 0.91, "content_hash": "ab12…",
      "edges": [
        {"target": "monad-pythagorean", "type": "part-of",   "conf": 0.86, "via": "llm", "evidence": "the monad is the source of the decad"},
        {"target": "plato",             "type": "influences", "conf": 0.90, "via": "llm"},
        {"target": "hippasus",          "type": "contradicts", "via": "ledger"},
        {"target": "samos",             "type": null,          "via": "wikilink"}
      ]
    }
  }
}
```

**`enrichment-temporal.json`** — time index, pre-sorted (drives the timeline view)
```json
{ "version": 1, "events": [
  {"page": "pythagoras", "kind": "date",   "text": "c. 570 BCE", "year": -570, "conf": 0.9, "via": "regex"},
  {"page": "croton-school", "kind": "period", "text": "5th c. BCE", "start": -500, "end": -401}
] }
```

**`enrichment-claims.json`** — propositions + provenance + grounding (drives study-guide / quiz / FAQ / summary)
```json
{ "version": 1, "pages": {
  "decad-pythagorean": [
    {"text": "The tetractys' rows (1+2+3+4) sum to ten.", "kind": "proposition", "salience": 0.8, "provenance": ["yt-…"], "grounding": "grounded", "via": "llm"},
    {"text": "Harmony obeys plain arithmetic.", "kind": "quote", "provenance": ["src-id"], "grounding": "grounded", "score": 0.99, "via": "grounding"}
  ]
} }
```

**`enrichment-terms.json`** — term → gloss (drives glossary / inline glossing / define-X)
```json
{ "version": 1, "terms": {
  "tetractys": {"page": "tetractys", "gloss": "A triangle of ten points in rows of 1–4 …", "aliases": ["tetraktys"], "salience": 0.7}
} }
```

Plus a human-readable **`enrichment-report.md`** (counts per layer, top-salience nodes, edges typed
vs untyped, dated events, terms) — like `grounding-report.md`.

### The predicate set (small, universal, extensible)
`precedes` · `requires` · `influences` · `derives-from` · `part-of` · `instance-of` · `contradicts`
· `causes` · `exemplifies`. Plus a free-string long tail the LLM may propose, and `null`/`"related"`
for untyped wikilinks. Keep the core small so it never hardens into a domain schema (the lesson from
the forms work: content-agnostic).

---

## The LLM typing prompt (Phase B) — design notes
- One call **per node** (not per edge): cheaper, and the model sees the node's whole context.
- Input: node title + body (or summary if long) + a compact list of its outgoing wikilink targets
  `[{id, title, summary}]` + the predicate set + 1 positive & 1 negative example.
- Output: a SINGLE JSON object (no prose/fences — the linter's proven pattern), parsed with
  `json.loads`: `{"edges": [{"target","type","conf","evidence"}], "claims": [{"text","salience"}]}`.
- **Critical rules LAST** (recency): "use `related` if no predicate fits — do NOT force one";
  "evidence must be a phrase from the page"; "propositions must be atomic + verifiable".
- Reuse `make_llm_query_fn` / the `client.messages.create` + `cost_tracker.record_call` pattern
  (`repl/functions.py`, `agents/linter.py`). Grounding the LLM's propositions: run each through the
  existing fuzzy matcher (`grounding._ground_one`) against the node's sources → a `grounding` verdict
  for free (catches fabricated propositions).

---

## Build order (sequenced by the chosen unlocks)
1. **Scaffold**: `cmd_enrich` + `VaultPaths` sidecar properties + the atomic-write helper + the
   `enrichment-report.md` renderer. Wire `enrich` into the CLI and (optionally) auto-run after `ingest`.
2. **Mechanical core** (Phase A) — salience/centrality → `enrichment-graph.json` (untyped + `contradicts`);
   **temporal regex** → `enrichment-temporal.json` (unlocks **Timeline**); terms → `enrichment-terms.json`;
   quote-claims (reuse `ground_vault`) → `enrichment-claims.json`. All $0; validate on Pythagoras (150).
3. **Bounded LLM typing pass** (Phase B) — typed edges (unlocks **Comparison/concept-map**) + atomic
   propositions (enriches **Study-guide/quiz**), top-N by salience, content-hash-gated, Haiku default.
   Validate cost + quality on Pythagoras top-20%, then MPH.
4. **Consumers** (separate slices, downstream): the Tier-2 *views* read these sidecars —
   **timeline** ← `enrichment-temporal.json`; **comparison / concept-map** ← `enrichment-graph.json`
   typed edges; **study-guide / quiz** ← `enrichment-claims.json` (the Dwell `Renderer.quiz` can be fed
   real claims instead of deriving them). Dwell's server already reads `_meta/`.

---

## Idempotency, scope, cost control
- Phase A recomputes every run (cheap, deterministic).
- Phase B skips unchanged nodes via `content_hash`; `--top N` / `--budget $X` bound how many nodes get
  typed; `CostTracker.check_budget()` is the hard stop. `--mode mechanical|hybrid|full` selects depth.
- A vault that lacks a dimension (no dates, no sources) just produces an emptier sidecar — every
  consumer degrades gracefully (the view simply can't render, never breaks).

## Code pointers (the reusable infrastructure)
- CLI wiring: `cli.py` `cmd_ground` (2275) + subparser (2615) — clone for `enrich`.
- Graph / centrality: `compendium/vault/links.py` (`parse_wikilinks`, `build_backlinks`, `resolve_target`).
- Claims (free): `compendium/agents/grounding.py` `ground_vault()` → `QuoteCheck`; reuse `_RawIndex` +
  `_ground_one` to verify LLM propositions too.
- `contradicts` edges: `compendium/vault/contradiction_ledger.py` `ContradictionLedger.load()`.
- Page loading: `compendium/vault/pages.py` `read_page` / `list_pages` (frontmatter → terms).
- LLM + cost: `compendium/config.py` `create_anthropic_client`; `compendium/guardrails/cost_tracker.py`;
  prompt/parse pattern in `compendium/agents/linter.py`.
- Sidecar write: `compendium/vault/contradiction_ledger.py:161` (atomic) + `history.py` (append) +
  `layout.py` `VaultPaths` properties.

## Open decisions (flag at build time)
- PageRank vs raw in-degree for centrality (in-degree is enough for v1; PageRank if betweenness matters).
- One merged `enrichment.json` vs the four split files (split chosen: per-consumer loading, graceful gaps).
- Whether `enrich` auto-runs after `ingest` (recommended: Phase A yes, Phase B opt-in for cost).
- Default `--top` fraction (proposed 20%) and default model tier (proposed Haiku).

*Authored 2026-06-23. Update as slices land.*
