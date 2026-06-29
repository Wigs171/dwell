# Learn feature — prior art to adapt (don't reinvent)

> Research 2026-06-27. Reference for building the Learn (vault-builder) feature. The point:
> Dwell/Compendium is **already** the rarest part of this space (an agent that writes a
> wikilinked Markdown vault with typed pages + per-claim grounding). The Learn build is
> mostly **wiring what exists + borrowing a few well-understood mechanics**, not inventing.

## The most direct cousin: the "LLM-Wiki" lineage — and you're ahead of it
The closest prior art to *what Dwell is* (not a graph DB — a **Markdown vault**): the Karpathy "LLM
Wiki" pattern — `raw/` (immutable sources) → `wiki/` (`[[wikilinked]]` LLM pages) → a `CLAUDE.md`
schema; ops = `ingest`/`query`/`lint`. **That is literally the Compendium CLI.** Implementations to
diff our conventions against: `rvk7895/llm-knowledge-bases` (a Claude-Code plugin that compiles/
queries/lints an Obsidian wiki — same Agent-SDK orchestration we're planning), `kytmanov/obsidian-llm-wiki-local`,
`green-dalii/obsidian-llm-wiki`. **None has our agent-swarm + grounding gate + contradiction ledger**
— we're ahead on rigor. Borrow their Markdown-native prompt/schema choices (transferable 1:1, unlike
graph-DB projects whose output we'd have to render back to Markdown).

---

## Directly adaptable (license-clean code/architecture)

| Project | License | Adopt for | Borrow specifically |
|---|---|---|---|
| **STORM / Co-STORM** (Stanford OVAL) | open (`knowledge-storm` pip) | the **research-prompt path** | Perspective-guided question generation → one research subagent per "angle"; Co-STORM's **dynamic mind-map** = slot findings into the growing vault as you go |
| **PaperQA2** (FutureHouse) | **Apache-2.0** | **grounding/citation** | **RCS** — never inject raw chunks into the writer; summarize-and-score each passage *in the context of the claim*, inject only top summaries + passage-level citation keys. Pairs with our `ground` pass |
| **Graphiti / Zep** | **Apache-2.0** | **dedup + temporal** | **Entity-resolution** (extract → match canonical → merge) and **bi-temporal edge invalidation** (contradictions set `valid_to`, don't delete). The rigorous version of our contradiction ledger + `canonical-slugs.md` |
| **LightRAG** | **MIT** | **incremental ingest** | **Set-merge** — new material → local subgraph → union into vault, reindex only touched nodes (no full rebuild). Embed entities *and* relations (feeds missed-connection detector) |
| **nano-graphrag** | **MIT** | **reference skeleton** | ~1100-LOC clean GraphRAG: extract → Leiden cluster → community-summary → local/global query. Read this (not MS GraphRAG) if we ever want a graph index beside the Markdown |
| **Docling** (IBM) | **MIT** | **PDF→Markdown+figures** | Default self-hosted doc front-end (~94% table accuracy, figure descriptions, clean MD). Keep our Gemma-4 vision path for figures |
| **Anthropic multi-agent research system** (blog) | guide | **the Agent-SDK orchestration** | Orchestrator(Opus)→3-5 parallel subagents(Sonnet); + the failure modes we *will* hit: over-spawning, phantom-source loops, status-noise → build spawn caps, loop guards, batched status |

Also-rans worth knowing: **Microsoft GraphRAG** (MIT, the reference; gleanings + community summaries),
**fast-graphrag** (MIT, Personalized-PageRank ranking over the wikilink graph — near-free "what's near
this node"), **Cognee** (Apache, Pydantic-typed ontology as extraction contract), **R2R** (MIT, closest
full "backend+API+live-build" analog — study its ingestion-API + progress model), **RAGFlow** (Apache,
layout-aware chunking), **LlamaIndex PropertyGraphIndex** / **LangChain LLMGraphTransformer** (MIT,
schema-guided extraction).

---

## Patterns to COPY, not invent
- **Dedup = resolve-before-write:** extract → match against a canonical registry → merge descriptions (Graphiti/GraphRAG/LightRAG). Formalize `canonical-slugs.md` into an orchestrator step.
- **Gleanings:** after extraction, re-prompt the *same* chunk "miss any entities?" once or twice (GraphRAG). Cheap recall boost; fits the verify gate.
- **Community-summary = synthesis pages:** cluster the wikilink graph (Leiden, or the centrality we already compute), one subagent writes a synthesis page per cluster. GraphRAG's single best idea, maps onto our `voice`/synthesis pages.
- **Schema-guided extraction:** constrain subagents to a **Pydantic/typed ontology** so they can't emit off-taxonomy page types (kills a class of our YAML/tag pitfalls).
- **Incremental set-merge:** new material → local pages → union into vault, reindex only touched nodes (LightRAG). No full re-lint per add.
- **Bi-temporal grounding gate:** every claim carries citation + validity window; contradictions invalidate (`invalid_at`) rather than delete (Graphiti). Upgrades grounding-report + contradiction-ledger into one model.
- **No raw chunks into the writer:** summarize-and-score in-context first (PaperQA2 RCS).
- **Second-pass fact-check:** generate → keep only claims verifiable against the corpus → re-retrieve → recombine (WikiChat). Cheap post-write gate.

## UX patterns for the live-build screen (copy)
- **Cancellation-token propagation** for the Stop button — one intent cascades orchestrator → subagents → children. Design in from day one (Anthropic).
- **Named-stage progress per item** — each source moves through explicit stages (search → fetch → chunk → ground → write → link), not one spinner (AnythingLLM `emitProgress`, Verba status page).
- **Gather → review/curate → commit** as a deliberate two-step — toggleable source rows; prune before committing to the graph (NotebookLM source panel + AnythingLLM source-file-vs-workspace split).
- **Checkpoint/resume** — long build (or a Stop) resumes from the last valid checkpoint, not a restart (Onyx).
- **Each ingested item = a first-class, inspectable, toggleable source row** with its own status (NotebookLM).

---

## Net for Dwell
- The **graph-DB projects (Graphiti, LightRAG, GraphRAG)** are where the *mechanics* (dedup, incremental merge, temporal edges, cluster-summaries) are most worth lifting.
- The **LLM-Wiki + Docling lineage** is where the *Markdown-native output shape* matches us and should anchor conventions.
- The **research-prompt path** = STORM's fan-out + PaperQA2's grounding + Anthropic's orchestration bounds.
- We're already ahead on agent-swarm + grounding rigor. The two gaps to close: **dedup-as-resolution** and **incremental set-merge**.

### Mapping to the Learn build phases
- **Phase 2 (intake + scaffold):** Docling for PDFs; gather→curate→commit two-step; toggleable source rows.
- **Phase 3 (ingest swarm):** reuse our `IngestOrchestrator`/`learn_op`; add resolve-before-write dedup (Graphiti), gleanings, schema-guided Pydantic output, set-merge incremental ingest (LightRAG); grounding gate = PaperQA2 RCS + our `ground`. Orchestration bounds from the Anthropic guide.
- **Research-prompt path:** STORM perspective-fan-out → per-angle subagents → grounded pages.
- **Live-build screen:** cancellation-token Stop, named-stage progress, checkpoint/resume.

Sources: STORM https://github.com/stanford-oval/storm · PaperQA2 https://github.com/Future-House/paper-qa · Graphiti https://github.com/getzep/graphiti (paper https://arxiv.org/abs/2501.13956) · LightRAG https://github.com/HKUDS/LightRAG · nano-graphrag https://github.com/gusye1234/nano-graphrag · GraphRAG https://github.com/microsoft/graphrag · Docling (PDF benchmark) https://procycons.com/en/blogs/pdf-data-extraction-benchmark/ · R2R https://github.com/SciPhi-AI/R2R · Cognee https://github.com/topoteretes/cognee · Anthropic multi-agent https://www.anthropic.com/engineering/multi-agent-research-system · GPT-Researcher https://github.com/assafelovic/gpt-researcher · WikiChat https://github.com/stanford-oval/wikichat · llm-knowledge-bases https://github.com/rvk7895/llm-knowledge-bases
