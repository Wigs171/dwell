# The Dwell Creed — one substrate, any output

> North-star vision + design commitments for Dwell. Read this before proposing
> features. **The one-line test for any idea:** *does it widen the transform layer,
> or does it sneak form into the substrate?* The first is Dwell; the second is the
> anti-pattern.

## The thesis
A Dwell **vault is a content-neutral semantic substrate.** Every reading experience
it produces — narrated article, step-by-step walkthrough, Socratic dialogue, FAQ,
abstract, timeline, glossary — is a **runtime transform** over that one pre-created
dataset. We never build output-specific vaults. We build the transform layer that
turns one dataset into any text-based output.

## What a vault stores (and never stores)
- **Stores: meaning.** Facts, the cross-linked concept graph (wikilinks), sources,
  claims, relations, entities, dates — everything under `wiki/` + `raw/`.
- **Never stores: form.** No "this is a tutorial," no fixed prose, no pre-rendered
  output. The *shape* of the reading is chosen at read time and synthesized on demand.

Think headless content / single-source-many-renderings — but **generative**: the
renderings aren't pre-authored templates, they're composed live by the renderer.

## This is already the architecture (the existence proof)
Reading **level** and narrator **voice** already prove it. The renderer takes one
stored content plan (a node's material + recap + tail) and synthesizes radically
different prose from it, **in place**, cached per (voice, level). The
elementary→scholar morph is the whole thesis in miniature. So broadening output
forms is **not a pivot** — it's widening a transform vocabulary the engine already
has.

Mechanism (so this stays concrete): `page_renders` stores each page's render context
(plan, tail, recap, hint); `/repage` re-derives the *same* page along a new axis
without moving the reader; the cache keys by the axis values, so flipping back is
instant and free.

## The two tiers of transform
**Tier 1 — re-pitch the page** (cheap, live, instant via `/repage`). Same content
plan, new shape. Composes orthogonally — any output is a coordinate in
**Form × Level × Lens × Length × Voice**:

> article ↔ steps/walkthrough ↔ Socratic dialogue ↔ FAQ/Q&A ↔ debate ↔
> second-person immersive ↔ TL;DR / deep-dive.

**Tier 2 — re-traverse / aggregate the graph** (a different *view*; more new code):

> timeline (chronological walk) · comparison (two+ nodes) · glossary / flashcards
> (atomic extraction across the vault) · study guide (claims + checkpoints) ·
> concept map / index · annotated bibliography (the sources axis).

**The dividing line:** Tier 1 changes the *words*; Tier 2 changes *what's on the page
and in what order*. Prefer Tier 1 (mostly prompt/axis work over existing infra);
reach for Tier 2 when the reader genuinely wants a different view of the data.

## No special vaults — ever (the anti-pattern)
Do **not** build "tutorial vaults," "course vaults," "FAQ vaults." Any vault renders
as any of those. **A tutorial is a recipe, not a vault:**

```
tutorial  =  form: steps  ×  traversal: goal-path  ×  gate: quiz
```

…applied to a neutral vault (the quiz gate reuses the existing retrieval-practice
system). The "tutorial-ness ceiling" is set by *content* — a cooking vault yields a
real do-this walkthrough; a Pythagoras vault yields an explain-and-check one — never
by a vault *type*.

## The leverage point: ONE universal ingest, not output verticals
Prose re-pitch (Tier 1) works on any decent vault. But a timeline needs dates, a
comparison needs the relation captured, a dependency-ordered walkthrough needs
prerequisite edges. So the **highest-leverage investment is upstream**: make ingest
capture queryable structure so the transform layer has something to reshape.

Crucially — this is **one universal, content-agnostic ingest, never per-output-type
curation.** The *same* extraction runs on every vault (Pythagoras, a recipe, a
codebase, a discography); each transform queries only the parts it needs; a vault
missing a dimension just can't produce that one view (graceful, not broken).
Universal data points (each works across all domains): **typed edges**
(`precedes`/`requires`, `influences`/`derives-from`, `part-of`, `contradicts`,
`causes`, `exemplifies`), **temporal anchors** (dates / durations / step-order), a
**claims layer** (propositions + provenance + salience), **terms/definitions**, and
**salience/centrality**. Additive over today's prose + wikilinks + `sources:` +
grounding + contradiction-ledger + centrality; cheapest first slice = *type the edges
you already extract + pull dates*. Full plan in `DWELL_ROADMAP.md`.

## Why it matters (the moat / economics)
**Ingest meaning once; render infinitely.** One corpus serves the commuter at
elementary level, the scholar, the kid who wants a story, the FAQ-seeker, the
debate-club kid — none pre-authored. Nobody ships the same book as forty documents;
Dwell ships the substrate and generates the forty on demand. The vault is a
**capital good** (one-time ingest cost; output is generated, not stored) →
subscription / site-license economics. See [[project_dwell_education]].

## Design commitments (build by these)
1. The vault encodes **meaning, never form**.
2. Every output form is a **runtime transform**; nothing is pre-rendered into the vault.
3. New capability = a **new transform axis or traversal mode**, never a new vault kind.
4. **Tier 1 before Tier 2** — cheap composable re-pitch before graph re-traversal.
5. **Invest in ingest richness** to unlock the widest transform space.
6. **One universal ingest, never per-output curation.** The substrate is extracted
   the same content-agnostic way for every domain (typed graph + claims + temporal
   index over the prose). Enrich every vault universally; never curate a vault for a
   single output type. Versatility of the data is the goal.
7. **Re-pitch in place** (keep the reader's spot via `/repage`), cached per
   axis-combination → flipping shapes is instant and free on re-view.
8. **Mercury (diffusion) is the morph engine** — whole-page refine-in-place is what
   makes a live shape-change feel magical instead of a reload. See
   `MERCURY_PROMPT_GUIDE.md`.
9. Be **honest about the content ceiling**: the form always works; the depth depends
   on the data.

## Status — live vs planned (keep current)
- **Live axes:** reading level (elementary→scholar, in place), narrator voice (vault
  voices + presets + free-text custom), output **form** (article / guided tour / Q&A /
  dialogue — shipped 2026-06-21; tutorial = `form=guided`). Form directives are
  grounded in each genre's established conventions (Diátaxis explanation-vs-how-to;
  the Socratic elenchus; FAQ/Q&A layout) — see `DWELL_ROADMAP.md`.
- **Planned Tier-1 axes:** **lens / stance** (historian / skeptic / mystic /
  mathematician / practitioner), **length / pace**, **abstraction**.
- **Planned Tier-2 views:** timeline, comparison, glossary, study guide, concept map.
- **Recommended next slice:** with `form` shipped, either another Tier-1 axis
  (**lens / stance** — reuses the `steer` infra) or begin the **universal ingest
  enrichment** (typed edges + temporal anchors) that unlocks Tier-2. See
  `DWELL_ROADMAP.md`. Always the general primitive, never a vertical.

## Related
- Architecture + handoff: `DWELL_HANDOFF.md`, `DWELL_APP_PLAN.md`.
- Engine prompting: `MERCURY_PROMPT_GUIDE.md`.
- Memory: [[project_the_current]] (Dwell project log) · [[project_dwell_education]]
  (education-mode + vault economics).

*Authored 2026-06-21.*
