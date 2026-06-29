# Learn — build plan (prior art folded in)

> Companion to [DWELL_LEARN_PRIOR_ART.md](DWELL_LEARN_PRIOR_ART.md). Decisions: **Claude Agent SDK**,
> **structured (no REPL)**, **UI scaffolding first**. Principle: reuse the existing `compendium`
> pipeline; borrow only the mechanics we lack. Backend = `dwell_server.py` + new `dwell_learn.py`
> (APIRouter); frontend = `dwell-web/`.

## Phase 1 — DONE
Home/Read/Learn nav (`dwell.page`), `Home.svelte` (diffusion title), `Learn.svelte` (intake form, stubbed Build).

## Phase 2 — Intake + gather→curate→commit  ← BUILDING NOW
The "gather" + "curate" steps (commit/ingest = Phase 3). Borrows: **NotebookLM** toggleable source rows +
**AnythingLLM** source-file-vs-workspace split (collect raw first, ingest as a *separate* committed step).

Backend `dwell_learn.py` (router `/learn/*`), reusing `compendium.vault`:
- `POST /learn/create {name, topic}` → `VaultPaths.for_vault(VAULT_ROOT/slug)` + init (CLAUDE.md, dirs, index, log) + write `_meta/learn.json` manifest `{status:"draft", topic, prompt:"", links:[]}`. Returns `{vault, name}`.
- `POST /learn/upload` (multipart) → stash files into `<vault>/raw/uploads/`. Returns the source list.
- `POST /learn/meta {vault, prompt, links[]}` → update the manifest (research prompt + web/video links).
- `GET /learn/sources?vault=` → uploaded files (reuse `_vault_source_list`) **+** manifest links, as toggleable rows.
- `DELETE /learn/source?vault=&id=` → remove an upload or a link (curate).
Doc front-end: **Docling (MIT)** is the chosen PDF→Markdown+figures extractor, but extraction is **ingest-time
(Phase 3)** — Phase 2 only stashes raw bytes. (Reuse the existing `compendium` `_prepare_local_source`/Gemma-4
vision path first; swap in Docling only if quality demands.)

Frontend `Learn.svelte`: real flow — create draft → add material (upload w/ progress, links, prompt) →
**curated source list** (remove rows) → "Build" still stubbed (Phase 3). Store: `learn*` state + `api.learn*`.

## Phase 3 — Ingest swarm (Claude Agent SDK, structured) + live build screen
Reuse `compendium.agents.ingest_orchestrator` / `learn_op`; wrap as an Agent-SDK orchestrator→subagents loop.
Borrowed mechanics (the bits we lack):
- **Dedup = resolve-before-write** (Graphiti) — formalize `canonical-slugs.md` into an orchestrator step.
- **Gleanings** (GraphRAG) — re-prompt a chunk "miss any entities?" once for recall.
- **Schema-guided extraction** (Cognee/LlamaIndex) — subagents emit Pydantic-typed concept/entity/synthesis pages.
- **Set-merge incremental ingest** (LightRAG) — new material unions in; reindex only touched nodes.
- **Grounding gate** = PaperQA2 **RCS** (summarize+score passages in-context, never raw chunks into the writer) + our existing `ground` pass; **bi-temporal** contradiction handling (Graphiti) — supersede, don't delete.
- **Community-summary = synthesis pages** (GraphRAG) — cluster the wikilink graph, one synthesis page per cluster.
- **Orchestration bounds** (Anthropic) — spawn caps, loop guards, batched status.
Research-prompt path: **STORM** perspective fan-out (survey related pages → one subagent per "angle") → grounded pages.
Live build screen (SSE): **cancellation-token** Stop cascading orchestrator→subagents (Anthropic); **named-stage
per-item progress** search→fetch→chunk→ground→write→link (AnythingLLM/Verba); **checkpoint/resume** (Onyx).
On commit, the finished vault appears in the Read gallery.
