# Dwell Roadmap — sequenced implementation plan

> Companion to `DWELL_CREED.md`. The creed is the *why* (one substrate, any output);
> this is the *what next*. Two workstreams run in parallel: **transforms** (downstream,
> ships on existing data) and **universal ingest** (upstream, unlocks the richer
> transforms). Keep this current as slices land.

---

## ✅ DONE — the `form` axis (Tier-1 transform) — shipped + validated 2026-06-21
Output **form** is now a first-class re-pitch axis beside level and voice: `article`
(house shape), `guided` ("Guided tour"), `qa`, `dialogue`. The *same* page re-pitches
in place into each shape; cached per (form × level × voice); `article + general` stays
legacy-key-compatible; persisted, restored on vault re-entry, Settings → Dwell picker.
**Tutorial = `form = guided`, zero special code.**

**Form directives are research-grounded** (redesigned 2026-06-21 after the first cut
produced ill-fitting prose — procedural "first you do this" for non-procedural
content, interview-style "dialogue," Q&A questions buried in the paragraph):
- **`guided`** (was `steps`/"Walkthrough" — renamed; "walkthrough" wrongly implied a
  *procedure*). Per the **Diátaxis** framework, conceptual material wants *explanation*
  (understanding-oriented), delivered with a *tutorial's* guided manner — a staged
  build-up (orient → ground → build → extend → situate), concrete-before-abstract,
  narrating the logical motion, never invented physical actions.
- **`dialogue`** = a real dialectical **elenchus**: one voice holds a confident,
  contestable claim that the other dismantles using the first voice's OWN admissions
  (claim → counterexample → concession → refined claim → aporia). Test: the position
  must visibly change. (The old version was an interview.) **GOTCHA (fixed
  2026-06-21):** the directive must say "Socratic *method*" carefully — naming
  "Socrates"/"Socratic" in the directive made the model insert *Socrates as a literal
  character* into an image-segmentation dialogue. The directive now uses **two UNNAMED
  voices, explicitly forbids the name "Socrates"**, and demands **direct first-person
  spoken lines** (no third-person reportage like "Socrates probes…" / "the interlocutor
  concedes…"). Lesson for any future form: don't let a method's namesake leak into the
  output; forms must be content-agnostic.
- **`qa`** follows FAQ convention (NN/g): real reader-voice question on its own line,
  surrounded by white space, answer-first (inverted pyramid), no filler. Made **terse +
  scannable** (1–2 sentence answers) so it's genuinely distinct from `dialogue` (a
  flowing argument) — they were converging.

**Structural visual formatting (2026-06-21):** the reader styles form pages by
structure — block-level only, so the karaoke/clarify/quiz offset-map stays intact:
- Each page carries a stable **`form`** field (server emits it on `start` + `done` →
  `PageView.form`). The reader routes by `page.form`, NOT by sniffing the text (which
  flipped the layout mid-stream). A shared `body` snippet picks form-styling vs plain
  prose, so it works **with or without an image** (the bug that hid it: a form page
  with 1 image went through the image branch → `pageBody`, bypassing form styling).
- `formBody` splits the text into per-line `<p>`s (`split(/\n+/)` — handles single-`\n`
  *and* blank-line separators, since Mercury varies). **Dialogue**: hanging em-dash
  indent, the two voices distinct (speaker B accent-tinted + *italic*, theme-proof).
  **Q&A**: questions prominent (weight 500, larger, accent) above tight answers. Form
  pages fit via `domFit` (DOM measure — styling is non-uniform).
- **✅ DONE — inline emphasis + headings (font sizes), 2026-06-21.** The model emits a
  light markdown subset (`**bold**`, `*italic*`/`_italic_`, `## `/`# ` headings, used
  sparingly); the client parses it ONCE (`marks.ts` `parseMarks`) into **clean `page.text`
  + `page.marks=[{start,end,kind}]`**; the reader renders the marks as real
  `<strong>`/`<em>`/`.rich-h1|h2` elements (`proseBody`/`inlineRender` over `splitBlocks`).
  page.text stays markup-free, so TTS + the offset-map are untouched. **Verified e2e:**
  injected `**tetractys**`/`*sacred*`/`## The Decad` → rendered strong/em/heading, rendered
  text == clean text, and a karaoke Range set on a word *inside* a `<strong>` highlighted
  exactly that word (proof the highlight spans inline elements — no split text). Live: the
  model italicized "harmonia" sparingly, `page.text` clean. Headings cover "different font
  sizes." Still optional: a node-title page header (just a heading field; reserve its
  height in `domFit`) and tuning how liberally the model emphasizes.
  *Earlier (superseded) note, kept as the lesson:* the "needs a display-vs-spoken text
  split" claim was WRONG —
  per the spec a Range spans inline elements and `::highlight()` (background/color/
  decoration only — never font-weight/style) coexists with `<strong>`/`<em>`; and the
  `proseSegs` offset map aligns by text *content*, which is unaffected by inline
  splitting (`TreeWalker(SHOW_TEXT)` never sees tags). So **no parallel text is
  needed** — emphasis is just **metadata** like `images`/`layout`/`form`:
  `page.text` stays clean (TTS + offset-map use it), `page.marks = [{start,end,kind}]`
  is parsed once from the model's markdown, and the reader wraps those ranges in real
  `<strong>`/`<em>` (adds elements, not characters → `textContent === page.text`).
  This is the same class of work as the form styling, not a special architecture.
  Titles = a separate heading field (never an offset-map concern; just reserve its
  height in `domFit`). Standard practice (read-along/immersive-reader) = one formatted
  DOM + ranges, confirmed via the CSS Highlight spec + MDN.
Validated live on the MPH **Atlantis** node (a genuine elenchus that ends "I concede
the tablet likely held a concise chronicle and that Plato added symbolic meaning").
Built exactly as below (kept for reference):

1. **Renderer (`dwell.py`)** — add a `form` param + a `_FORMS` directive map
   (`article` = today's default, plus `steps`, `qa`, `dialogue`). Thread it through
   the prompt like voice/level; add `form` to the renderer's `cache_key` (already
   keys on voice + level) so every (form × level × voice) caches independently.
2. **Server (`dwell_server.py`)** — a `/form` endpoint paralleling `/level`; carry
   `form` on the session. `_reproduce_page` already re-pitches in place, so form
   rides along with no new flow.
3. **Store (`dwell.svelte.ts`)** — `form` state + `setForm()` (copy of `setLevel`:
   calls `/form`, repages in place), persisted.
4. **UI** — a segmented form picker beside the level control in Settings → Dwell;
   later a main-area control for tablets (like the volume rail).
5. **Validate live** — the same page morphs article ↔ steps ↔ Q&A ↔ dialogue in
   place, cached, still markdown-free spoken prose (narration-safe).

Seed set: **article, steps, Q&A, dialogue** (confirmed).

---

## PARALLEL UPSTREAM — universal ingest enrichment ← the substrate investment
**→ Detailed spec: [`DWELL_ENRICH_PLAN.md`](DWELL_ENRICH_PLAN.md) (PLANNED 2026-06-23).** Key
finding + locked decisions: **4 of the 5 data points are nearly FREE** (salience←backlinks;
terms←page title/summary/aliases; claims+provenance←the grounding engine; `contradicts`←the
ledger; dates←regex) — the ONLY thing needing an LLM is **typing the edges**. Approach =
**HYBRID** (free mechanical core on every node + a bounded LLM typing pass on top-N high-salience
nodes, content-hash-gated, Haiku default). First unlocks = **Timeline · Comparison/concept-map ·
Study-guide/quiz**. Writes four `_meta/` sidecars (`enrichment-graph/temporal/claims/terms.json`)
via `cli.py enrich`.

**Principle:** ONE content-agnostic ingest that serves every transform — never
per-output-type curation. The same extraction runs on every vault (Pythagoras,
a recipe, a codebase, a discography); a transform queries only the parts it needs;
a vault missing a dimension simply can't produce that one view (graceful, not broken).

**Additive over what already exists** (prose + wikilinks + `sources:` + the grounding
engine's claim-checking + the contradiction ledger's `contradicts` edge + centrality).
Ship as a **`cli.py enrich`** pass writing a `_meta/` sidecar (`graph.json`,
`claims.json`, temporal index) so existing vaults adopt incrementally without
re-ingesting.

**Universal data points** (each passes the test: works for history *and* a recipe
*and* code *and* music):

| Data point | Unlocks |
| --- | --- |
| **Typed edges** — `precedes`/`requires`, `influences`/`derives-from`, `part-of`/`instance-of`, `contradicts`, `causes`, `exemplifies` (small universal core, free-string long tail) | tutorial step-order, comparison, concept map, debate, dependency walkthrough |
| **Temporal anchors** — dates / periods / durations / step-index | timeline, chronological walkthrough, "how long" |
| **Claims layer** — atomic propositions + provenance + salience | study guide, flashcards, quiz, FAQ, summary, grounding, skeptic lens |
| **Terms / definitions** — term → gloss | glossary, inline glossing, ELI5, "define X" |
| **Salience / centrality** (centrality already exists) | summary, foundational-first ordering, level adaptation |

**Incremental path:** (a) **type the edges you already extract** + **pull temporal
anchors** first — cheap, unlocks comparison / concept-map / timeline / prerequisite
ordering; then (b) the **claims + terms** layer (generalize the grounding engine);
salience mostly exists. Keep the predicate set small + extensible so it never hardens
into a domain-specific schema. Cost is one-time (capital good).

---

## THEN — more Tier-1 axes (same clone-the-pattern build)
- **Lens / stance** — historian / skeptic / mystic / mathematician / practitioner;
  reuses the `steer` infra, made sticky. (Deepens once typed edges exist.)
- **Length / pace** — distill ↔ dilate; pairs with the recliner + narration.

At that point the full re-pitch matrix is live: **Form × Level × Lens × Length × Voice.**

---

## THEN — Tier-2 views (need the enriched substrate)
A different *view* of the data, not a restyle of one page (re-traversal / aggregation):
- **timeline** ← temporal anchors
- **comparison** ← contrast edges + claims
- **glossary / flashcards** ← terms + claims
- **study guide** ← claims + salience
- **concept map / index** ← typed edges
- **annotated bibliography** ← provenance
- **paths / guided journeys** ← goal-path traversal + gates + tempo + dream — the
  `traversal × gate` primitive; spans education↔fiction. **Spec: `DWELL_PATHS.md`.**

---

## THEN — Paths: the directed reader (`traversal × gate`) — spec: `DWELL_PATHS.md` (PLANNED 2026-06-30)
The **itinerary axis** — *which* nodes, in *what* order, and *how* the reader is carried
between them — layered on top of free-wander (which is untouched). **One system spans
education → fiction via a Convey→Dramatize dial** (a node is a *payload to convey*, or
*canon for a dreamed scene*; the middle = scenario/simulation, which upgrades tutorials
too). Resolved architecture:
- **Frozen spine of gates** — committed once at session start (authored *or*
  agent-rolled), then immutable; **guarantees arrival**. Steering never re-plans the
  destination, only the corridor. (Simplifies the horizon buffer: top resolution is
  fixed per session.)
- **Fluid corridors** — tempo = tween/**confluence-frame** count, **authored + adaptive**;
  **dwell expands in place** (deepen, don't advance), skim → **montage**.
- **Horizon buffer** (three resolutions: arc-skeleton / committed-intent / committed-prose)
  — **generate ahead, reveal late** (tweens build toward a hidden keyframe = mechanized
  setup-and-payoff / pedagogical scaffolding).
- **Weaver + resolution tracker** — a Path is a **braid of arcs** (main + subplots);
  the **dream dial = emergence budget**; every opened thread gets a `resolve_by`
  deadline → **wild in the middle, tidy at the seams**.
- **Gates** — `beat`/`read`/`self-report`/`quiz`/`task`; quiz reuses retrieval-practice
  + `enrich` claims (no manual authoring); **fail → remediate → retry** (= a strong
  dwell signal). `canon_strictness` decides block-vs-advisory.

Depends on **`enrich`** (planner substrate; gap→Learn re-plan), **form** (per-node lens),
**voices** (POV), **retrieval-practice** (gates), **Mercury/`/repage`** (render),
**reading-memory** (continuity + playthroughs). Build order in `DWELL_PATHS.md`
(Phase 0 = frozen-spine + `read`-gates on the existing `/repage`, no tweens).

---

## Interop — Open Knowledge Format (OKF) — spec: `DWELL_OKF.md` (PLANNED 2026-07-01)
Google's **OKF** (v0.1) standardizes ~exactly the Dwell vault (Markdown + YAML frontmatter,
cross-linked graph, `index.md`/`log.md`, `type`-only-required, "format not platform",
producer/consumer split — "formalizes the LLM-wiki pattern"). **Decision: converge via a thin
compat layer, not a rewrite** — read BOTH `[[wikilinks]]` and OKF `[text](/path.md)` links,
alias the frontmatter (`description`↔`summary`, `timestamp`↔`updated`, `resource`; path-as-id),
make the vault marker optional (also settles CLAUDE.md→AGENTS.md). Then Dwell can render ANY
OKF bundle → **Dwell = the diffusion reader on a standard substrate.** The ONE engine change is
a Markdown-link regex beside `_WIKILINK_RE` in `Brain.load`; everything above the substrate
(Paths, confluence, transforms) is untouched. Build order in `DWELL_OKF.md`.

---

## Loose ends (independent, small — don't lose)
- `magazine` auto-rotation: keep or cut the 3-column reflow.
- Multi-image layout cadence (every-other feasible page → maybe rarer).
- Cross-reload vault-stash persistence (localStorage + rehydrate; today in-memory only).
- Covers for the other vaults (only Pythagoras has an explicit `cover.jpg`).
- A real foreground/tablet listen for audio (only plumbing verified headless).

## Pre-existing roadmap (from before this arc)
- Timed auto-play · vault-mode + ⚡ tension · Phase 4 PWA/Tauri + FastAPI-serves-`dist`.

*Authored 2026-06-21. Update as slices land.*
