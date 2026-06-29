# Dwell Text-Figures — plan + research

> **Status: SLICE 1 SHIPPED TO MAIN (2026-06-23).** The real engine + Reader now emit and render
> derived text-figures — the two ZERO-TOKEN figures first: **drop-cap** and **pull-quote**, gated
> by the form-affinity scheduler + a density dial. (The `/#textfigures` lab still shows all 20.)
>
> **What shipped (Slice 1):**
> - `prototypes/text_figures.py` — `choose_text_figure(page_text, form, pos, node_id, …)`: the
>   scheduler. Form-affinity (mirror of `figureForms.ts`) × content-affordance × density spacing
>   (`off|sparse|normal|rich`, default **sparse**) × per-node phase. Images win the slot
>   (text-figures only fill no-image pages). Deterministic by the node's stable page ordinal
>   (re-pitch/coast safe). `_pick_quote` lifts a clean verbatim sentence (capital-start guard).
> - `dwell_server.py` — `_page_text_figure(...)` folded into BOTH `done` emits (`_produce_page` +
>   `_reproduce_page`); `DwellSession.text_fig_density`. Payload gains `text_figure: {kind,slot,payload}|null`.
> - `types.ts` `TextFigureView` + `PageView.textFigure`; `dwell.svelte.ts` sets it in the advance +
>   repitch `done` handlers; `Reader.svelte` renders pull-quote (a `<figure>` floated in `.prose`,
>   text wraps; `aria-hidden` + `data-narration="skip"`) and drop-cap (`.dropcap` → `::first-letter`,
>   no node); `queueFit` routes text-figure pages to `domFit`.
> - **Verified end-to-end at ZERO token cost** (dry session on the Pythagoras vault): engine emits
>   pull-quote + drop-cap sparsely in article form; the mounted reader renders both (drop-cap
>   `initial-letter:3` accent; pull-quote `float:right`, body wraps, aria-hidden); the karaoke
>   `proseSegs` walk excludes the figure (offset map intact); `npm run check` + Python clean.
>
> **Next slices:** (2) model-/recap-derived figures — **key-takeaways** ⭐ + **callout** (need a
> cheap derivation pass, since `recap` is a breadcrumb trail, not page points); (3) a reader-facing
> **density dial** + per-type **cooldown** state on the session; (4) Tier-2 (see-also/timeline/…)
> behind the ingest enrichment. Keep `text_figures._AFFINITY` in sync with `figureForms.ts`.
>
> ---
> *(Original spec follows — still the source of truth for the catalog + architecture.)* A dev
> harness renders all 20 catalog devices over one page of prose, at the `/#textfigures` route — the
> text edition of the image `/#layouts` lab. Companion to `DWELL_CREED.md` / `DWELL_ROADMAP.md`.
> Research grounded in Müller-Brockmann *Grid Systems*, Bringhurst *Elements of Typographic Style*,
> Tufte CSS, NN/g long-form UX studies, GitHub/MkDocs/Notion admonitions, and MDN/WCAG.
>
> **The lab (built):** `dwell-web/src/lib/` → `TextFigureLab.svelte` (harness, `#textfigures`
> route in `App.svelte`) · `TextFigures.svelte` (renders body + ONE figure per slot; the
> `PageLayout.svelte` analog) · `textfigures.css` (device styles + the margin-lane grid) ·
> `TextFigureId`/`TextFigureData`/`CalloutKind` in `types.ts`. Every figure renders as a
> `<figure data-narration="skip">`; drop-cap/raised-initial are pure `::first-letter`; pull-quote
> is `aria-hidden`. Verified e2e: all 20 fit (Portrait + Phone, 0 console errors); the four
> sidebar figures (pull-quote · sidenote · see-also · glossary) **float right and the body text
> wraps around them** (see-also/glossary at the top so the block runs down the left), collapsing
> to a block in a narrow card; the typed callout, `initial-letter` drop-cap, and `aria-hidden`
> pull-quote all confirmed. (The margin-lane grid was tried first and rejected — see DESIGN CHANGE
> below.) Open question carried into engine work: the figure **scheduler** that keeps versatile
> text-figures from over-representing — see *Scheduling* below.
>
> **Two gotchas (cost real debugging — heed for the engine wiring):**
> 1. **Namespace the page-container modifier.** Don't reuse the image lab's `l-{layout}` pattern
>    as `tf-{figure}` on the page `<div>` — `figure="callout"` then makes the page match the
>    INNER `.tf-callout` box rule (`display:grid; grid-template-columns:auto 1fr`), collapsing the
>    text column to ~2px (also hits kicker/tldr/accordion/sidenote/glossary/timeline). Use a
>    distinct prefix (`tff-{figure}`), or don't class the container at all.
> 2. **`container-type` goes on an ANCESTOR, never the element that restyles.** A `@container`
>    query resolves against the nearest *ancestor* container — an element can't query its own
>    size. The narrow-card collapse of the floats (`@container (max-width:520px)`) needs the host
>    page CARD to be the container (`.tf-card-host` / the lab `.card`), NOT `.tf-page` itself. The
>    real Reader card must declare `container-type: inline-size`. (Caught the hard way when the
>    container was on `.tf-page`: only descendants responded; the page-level rule never matched.)
>    (Lab aside: tie the grid column min-width to the selected card size, else `max-width:100%`
>    clamps every card to the narrow grid cell and a float never gets a wide-enough card to show.)

## The idea (and the anti-pattern)
Make text-only pages as visually rich and varied as image pages — by **reusing the existing
image layout slots** (`top` · `bottom` · `side` · `inset` · `rail` · `banner`/`magazine`)
to hold **derived TEXT-figures** (a pull-quote, a key-takeaways box, a callout, a big number,
a see-also rail…) instead of (or alongside) an image. A "figure" becomes *image-or-text*; the
same aspect/cycling engine that places images places text-figures.

**Anti-pattern (do NOT do): randomizing the body text's own layout** (random column count /
measure / alignment per page). An image is a discrete focal object — placement variety is
pleasant. Body prose is a continuous flow that wants a *stable, consistent measure*; randomizing
it is fatiguing and gimmicky (multicol body especially — see CSS notes). **Variety comes from
the discrete derived figures in the slots, never from reshuffling the reading column.**

## Governing UX principles (NN/g; obey these or it backfires)
- **People scan, they don't read** (F-pattern; ~20–28% of words read). Every figure must earn
  its place as a *scannable landmark / entry point*, not decoration.
- **Salience is a budget — cap it: 1–2 figures per page, never consecutive** (GitHub's own
  admonition rule). Same cap across the whole figure family. (Mirrors the image one-per-page
  cycling already shipped.)
- **Key-takeaways / summary box has the STRONGEST evidence** of any device — at the *start* it
  lets a reader judge relevance; per-section it gives skim checkpoints. Build this first.
- **Pull-quotes are double-edged**: they flip an engaged *linear* reader into scan mode. Keep
  them short, place them *adjacent to their source sentence*, and (for a narrated reader)
  **don't fire scan-triggers mid-narration** — schedule pull-quote / big-number at section
  boundaries; sidenotes & inline glosses are flow-safe (saccade-only) and may sit live.
- **A11y/TTS parity**: hover-only devices (tooltip, margin note) are invisible to audio +
  keyboard — every one needs a focus/Esc affordance (WCAG 1.4.13) + a non-hover reveal (reuse
  select-to-clarify). Drive any progress indicator from **narration time**, not word count.

## THE ARCHITECTURE (one decision drives everything)
A text-figure is **narration-invisible iff its text nodes sit inside a rejected container**.
The karaoke/clarify offset map (`Reader.proseSegs`, a `TreeWalker(SHOW_TEXT)`) **already rejects
`figure`/`figcaption`**. So:

> **Every text-figure = `<figure data-narration="skip">` placed in the DOM immediately before the
> text it relates to. Sidebar-style figures (pull-quote · sidenote · see-also · glossary) reuse
> the IMAGE `side` slot — `float: right` so the body text WRAPS around them, exactly like an
> image; block figures (key-takeaways, callout, comparison, stepped-list, TL;DR, timeline,
> source-strip …) are full-width in-flow blocks. Both collapse to a full-width block in a narrow
> card.** Centralize the reject as `figure, figcaption, aside, [data-narration="skip"]` (use
> `FILTER_REJECT` so the walker never descends).

> **DESIGN CHANGE (2026-06-22, from user feedback on the lab):** the original plan put sidebar
> figures in a separate **CSS-Grid margin lane**. In practice that *reserved a permanent empty
> column and shoved the body to one side — it looked wrong.* The user's call: a text-figure should
> behave like a floated image — **text wraps around it**, and a pull-quote/see-also can literally
> **stand in for an image in the `side`/`diagonal` slot** (see Scheduling below). So the margin-
> lane grid is **superseded by `float: right`** (the same float `.l-side` already uses). see-also /
> glossary float at the *top* so the whole text block runs down the left; pull-quote / sidenote
> float beside their anchor line.

Why this wins on every axis (verbatim extension of the shipped image-layout decision — *CSS
renders the text, the fitter predicts the fit, figures are excluded from the walk*):
- **Narration/karaoke**: it's a `<figure>` → already out of the offset walk, zero new mechanism.
  page.text stays the clean canonical string (same invariant as the `marks` work). Text wrapping
  around a float changes only line-breaking, not text nodes → karaoke Ranges are unaffected.
- **DOM-adjacent to its text** (placed *before* the wrapped paragraph) → reading/tab/collapse
  order is correct WITHOUT `order` hacks or the Chromium-only `reading-flow` API (WCAG 1.3.2
  clean); the responsive collapse to a block lands in the right place for free.
- **Fit**: floats reuse the image layouts' **wrapped-fit** path — `measureParasWrapped` in
  `fitLayout.ts` already predicts text height around a float via pretext's variable-width line
  API (no new mechanism), and `.tf-page { display: flow-root }` contains the float so even a tall
  one is measured. Block figures add their height in-flow (a plain rectangle measure). **Never
  multicol the body.** **Never** build the drop-cap by extracting the letter into a node — use
  `::first-letter` + `initial-letter` (float fallback for Firefox); a pseudo-element leaves the
  offset map untouched.

**ARIA nuance (the one real subtlety):** a pull-quote that *duplicates* a body sentence → mark the
`<figure aria-hidden="true">` (correct use — duplicated content, avoids a screen-reader double-read;
it's already out of the TTS walk). A *net-new* derived figure (gloss, stat, see-also) → semantic
`<figure>`/`<aside>` with **no** `aria-hidden`, so AT users keep it. Rule: **duplicated body text →
`aria-hidden`; net-new derived text → no `aria-hidden`.**

**Müller-Brockmann grid logic** (for slot sizing): module height is keyed to the body's leading —
each figure's height should snap to a whole number of body lines and inter-block gutters = 1×
(or n×) leading, so figures and body cross-align. Derive the lane/module geometry from the fitted
body `line-height`.

## Scheduling — avoiding over-representation (the real risk)
Text-figures are derivable from *almost any* page, so without restraint a pull-quote or callout
would land on every page and the reader becomes a wall of boxes. (Images are naturally scarce —
a node has 0–N — so they self-limit; text-figures do not.) The fix is **one figure scheduler for
images AND text, gated four ways** — generalize `_build_image_schedule` → `_build_figure_schedule`,
where a "figure" is image-OR-text and both compete for the same per-page slots:

1. **Shared slots + hard cap, images preferred.** Same cap as today (≤1–2 figures/page, never
   consecutive). A page **with images uses them**; text-figures fill the slot **only when there's
   no image** (or, for variety, occasionally instead of one). So an image-rich node rarely shows
   text-figures; a text-only node gets its variety from them. The pull-quote-as-image-substitute
   the user described *is literally this*: on a page whose slot is `side`/`diagonal`, the scheduler
   may drop a pull-quote there in place of a picture.
2. **Eligibility gating — the real frequency limiter.** A figure enters the candidate pool only
   when BOTH gates pass:
   - **(2a) Form affinity** — the figure must make sense in the *active form*. Each figure declares
     a per-form affinity: **native** (its home form — scheduler boost), **allowed**, or **blocked**
     (never appears). Source of truth: `dwell-web/src/lib/figureForms.ts` (lab renders it as a
     badge + dims blocked figures; the engine `_build_figure_schedule` mirrors it via the session's
     `form`). The shape of the matrix: a **stepped-list** is native to `guided`, **blocked** in
     `qa`/`dialogue`; **accordion** is native to `qa` (expandable answers); **key-takeaways /
     callout / glossary** are native to `guided` and **blocked** in `dialogue` (a dialogue must not
     pre-summarize itself — the journey is the point); **drop-cap** is `article`-only;
     **comparison** is blocked in `dialogue` (the two voices already ARE the contrast); `dialogue`
     keeps only framing/quote/gloss devices. `article` is the richest surface (nothing blocked).
   - **(2b) Content affordance** — the page must genuinely *afford* it, not merely "could we make
     one": pull-quote → a line over a strikingness threshold; key-takeaways → the recap has ≥3
     distinct points; comparison → an actual A-vs-B (or a contradiction-ledger by-design tension);
     timeline/big-number/see-also/glossary → the ingest enrichment supplies dates/claims/edges/terms
     (Tier-2, self-gating). ⇒ *structural* figures (comparison, timeline, stepped-list) are rare by
     nature; only the *versatile* few (pull-quote, callout, key-takeaways) need active rate-limiting.
3. **Per-type cooldown + rotation.** Exactly like image cycling avoids repeating an image: track
   recently-used figure **types** (per node/session) and forbid repeating a type within N pages;
   rotate among eligible types so no single device dominates a dwell. A small per-type budget
   (e.g. pull-quote ≤ 1 in K pages).
4. **A global density dial** (sparse ↔ rich), defaulting **low**, so the baseline is mostly clean
   prose and a figure is the exception that earns attention — mirroring how sparse images already
   feel. A Tier-1 re-pitch axis like level/form, if we want it reader-facing.

**Dividing line that keeps it safe:** the page's *content* decides **eligibility**; the scheduler
decides **which** eligible figure shows and **how often**. Versatility is bounded by
eligibility × cooldown × cap × density — never by "can we derive it." (Engine work, not yet built;
the lab shows one figure per page in isolation so each template can be judged on its own.)

## The catalog
Each: device · what · which existing slot · derivable from ONE page? · prior art. We already
*discussed* (not built) pull-quote, key-term box, epigraph, drop-cap — included for completeness.

### Tier 1 — derivable from a single page (pure runtime transform; BUILD FIRST, on-creed)
| Device | What it holds | Slot | Notes / prior art |
|---|---|---|---|
| **Callout / admonition** ⭐ (ONE component, `type` enum: note·tip·**key-insight**·question·caution·quote) | a re-pitched body sentence as a flagged box | inset / side / inline box | Highest-value + most on-creed. GitHub alerts, MkDocs Material, Notion. Cap 1–2/page. |
| **Key-takeaways / "what you'll learn"** ⭐ | the page's 2–5 major points | top banner / side | STRONGEST UX evidence. Surface the engine's existing per-page `recap` as a visible figure. |
| **TL;DR box** | 1-sentence ultra-compression | top | The visible form of the creed's length axis. |
| **Pull-quote** | the single most striking body line | side / banner | Decorative duplicate → `aria-hidden`; place by its source; suppress during narration of that passage. |
| **Block quotation** (≠ pull-quote) | a *verbatim* source passage, in-flow | inline, indented | In reading flow (not lifted out); roman→italic or −1pt + space above/below. |
| **Deck / standfirst** | 1–2-sentence framing under the title | top | Larger than body, smaller than title; an "entry point". |
| **Kicker / eyebrow** | a teaser line / section label above the title | top | Small caps, tracked; eyebrow can map from graph-centrality "section". |
| **Headline stack** | the title, typographically segmented | top | Display hierarchy at the opening. |
| **Raised initial / small-caps lead-in** | the opening letter/words | inline | **Alternatives to the drop-cap — one opening device per page.** Raised cap = easier to set than drop cap (Bringhurst). |
| **Stepped / numbered list block** | enumerable/sequential content | inset / panel | For procedures, rankings, stages (ties to `form=guided`). |
| **Comparison / contrast block** | two+ juxtaposed items (A vs B) | banner / panel | Derivable when the page sets up a contrast; the contradiction-ledger "by-design tensions" are a natural source. |
| **Accordion / expandable detail** | the page's deep-dive, behind a toggle | inline | Visible twin of the abstraction axis. Caveat: hidden from Ctrl-F + F-pattern until opened — never hide must-read content. |
| **Read-time / progress indicator** | est. time + progress | chrome (top/edge) | Compute progress from **narration time**. Engine already has a cost/coast meter. |
| **Marginalia / sidenote** | a gloss/aside keyed to one line | rail / margin lane | Flow-safe (saccade) — safest companion to narration. *Content* page-derivable; *citation target* needs `sources:`. |

### Tier 2 — needs structured data (gate behind the universal-ingest enrichment in the roadmap)
| Device | Needs | Slot | Notes |
|---|---|---|---|
| **By-the-numbers / big-number** | the **claims layer** (number + provenance) | inset / banner | Only fire on a clean number+referent or it fabricates precision. Big numerals = premier F-pattern landmark. |
| **Related / see-also aside** | the **wikilink graph + centrality** (have) | rail / end | Surfaces edges you already have; pair with the "✧ missed-connection" detector. Keep the list short. |
| **Author / source / grounding strip** | **`sources:` + grounding** (have) | foot / top strip | Doubles as a *trust* strip for model-generated prose ("synthesized from N sources, grounded"). |
| **Timeline / chronology strip** | **temporal anchors** | banner (full-width) | This is a **Tier-2 *view*** (chronological traversal), not a per-page figure — see roadmap. Knight Lab TimelineJS pattern. |
| **Running glossary** | **terms/definitions** extraction | rail | The multi-term aggregate of the single key-term box. |
| **Definition tooltip / inline gloss** | **terms** layer | inline (dotted underline) | WCAG 1.4.13 (focus + Esc + `role=tooltip`); provide a non-hover reveal for TTS/AT parity. |

## Fit / responsive (the parts most likely to bite)
- **Prefer the grid margin lane** (fit-friendly: figure height extends the margin track, body stays
  analytic). Float/`shape-outside` only for true text-wrap, with a fixed width+height slot or the
  `domFit` fallback. `position:absolute` only for fixed-size corner features (a 2-digit big-number);
  it's invisible to the fitter, so YOU must prevent overflow.
- Fit algorithm: measure body via `pretext` as today; DOM-measure each figure (1–3, cheap); card
  target = scale where `body ≤ available` AND `each figure ≤ its lane`; binary-search the body,
  `clamp()` figure font-size separately. Re-run on the existing deck `ResizeObserver`.
- Responsive: under ~760px the margin lane collapses to a full-width in-flow block (DOM-adjacency
  makes the order correct for free); kill floats under ~560px (or when remaining text width < ~22ch).
  Don't copy Tufte's *hide-by-default* on mobile — collapse to inline instead.

## Build order
1. **Engine first**: generalize a "figure" to image-or-text. `_page_images` (rename intent →
   `_page_figures`) emits text-figures into the same slots, cycled like images (one per page,
   `_node_page_pos`). A text-figure record = `{kind, slot, payload}`.
2. **Tier-1 cheap wins, in order of evidence**: **key-takeaways box** → **callout family** (typed) →
   **pull-quote** (placement + `aria-hidden` dedup) → **drop-cap / raised initial** (pure CSS) →
   **TL;DR** / **deck**. Each reuses the `<figure data-narration="skip">` + grid-lane pattern.
3. **Tier-2** as the ingest enrichment lands (typed edges / temporal anchors / claims / terms):
   see-also, source/grounding strip, by-the-numbers, glossary, timeline (a Tier-2 view).
4. Cross-cutting from day one: the **density cap** (≤1–2/page), **narration scheduling** (no
   scan-triggers mid-passage), **a11y parity**, and the **collapse-to-block** responsive rule.

## Fit with the rest of Dwell
- **Creed**: a text-figure is a *transform/extraction* over the page (the striking line, the key
  terms, the thesis) — content-neutral, no special vault. Tier-2 figures are exactly the payoff of
  the **universal ingest enrichment** (the reason to capture typed edges / dates / claims / terms).
- **Engine reuse**: the figure-placement + aspect/cycling system (shipped for images) and the
  `marks`/offset-map invariant (shipped for inline emphasis) carry over unchanged — text-figures are
  more `<figure>` subtypes.

## Code pointers (where this plugs in)
- Figure resolution + slot/cycle: `dwell_server.py` `_page_images` / `_choose_layout` /
  `_node_page_pos`; reader `figEl` / `body` / `proseBody` / the `l-*` slot CSS in `Reader.svelte`.
- Offset-map reject (extend to text-figures): `Reader.svelte` `proseSegs` `acceptNode`
  (`figcaption, figure` today → add `aside, [data-narration="skip"]`).
- Fit: `domFit` / `pretextFit` / `queueFit` (route figure pages to DOM measure, as forms/marks do).
- Clean-text-+-metadata precedent: `marks.ts` + `PageView.marks` (text-figures are the same shape:
  metadata beside a clean `page.text`).
- Emphasis/headings already shipped (`marks`); text-figures are the block-level, slot-placed sibling.

*Authored 2026-06-21. Sources: Müller-Brockmann *Grid Systems*; Bringhurst *Elements of Typographic
Style*; Tufte CSS + gwern.net/sidenote; NN/g (F-pattern, long-form formatting, tooltips, progress,
progressive disclosure); GitHub alerts / MkDocs Material / Notion callouts; MDN (`shape-outside`,
`initial-letter`, `<aside>`, multicol, grid a11y); WCAG 1.3.2 / 1.4.13.*
