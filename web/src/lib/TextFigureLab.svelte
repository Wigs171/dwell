<script lang="ts">
  // Dev harness (open /#textfigures) for eyeballing the DERIVED TEXT-FIGURES
  // under real card constraints — the text edition of LayoutLab. Not part of the
  // shipping app: it's how we pick & tune templates before engine integration.
  // Payloads are hand-authored from the same Pythagoras prose the image lab uses;
  // the engine will derive them per page later (Tier-1 from one page; Tier-2 gates
  // on the universal-ingest enrichment — those cells are tagged "needs …").
  import TextFigures from './TextFigures.svelte';
  import type { TextFigureId, TextFigureData } from './types';
  import { FORMS, affinity, type FormId } from './figureForms';

  // Same prose as the image lab, for continuity.
  const PROSE = `To the Pythagoreans, number was not a means of counting the world but the very stuff from which it was woven. A pebble laid beside a pebble made two; three rows of pebbles built a triangle; and in those small arrangements they believed they could read the architecture of the cosmos itself.

The discovery that haunted them most arrived through sound. Pressing a string at its midpoint, Pythagoras is said to have heard the same note an octave higher — a ratio of two to one. A third of the way along sounded a fifth, a quarter a fourth. Harmony, the most bodily of pleasures, turned out to obey plain arithmetic.

From this they drew a daring inference: if music was number made audible, perhaps the heavens were number made visible. The planets, wheeling at their measured distances, must sound their own vast chord — a music of the spheres too constant for mortal ears to notice.

The tetractys, a triangle of ten points in rows of one, two, three, and four, became their holy figure. Its rows summed to the perfect ten and held within them the very ratios of the musical scale. Initiates swore their oaths upon it rather than upon any god.

Yet the same devotion to number nearly undid them. When a student proved that the diagonal of a square could not be written as a ratio of whole numbers, the irrational had entered their perfect world. Legend says the man who first spoke of it aloud was lost at sea.

What survived was not the secrecy but the conviction — that beneath the noise of appearances runs a hidden order, and that the patient study of proportion might bring the soul itself into tune with it. It is an idea that has never quite left us.`;

  type Demo = {
    id: TextFigureId; name: string; tier: 1 | 2; slot: string;
    needs?: string; note: string; data?: TextFigureData;
  };

  // Hand-authored derived figures (stand-ins for the engine's extraction).
  const demos: Demo[] = [
    // ── Tier 1 — derivable from a single page (build first) ──────────────
    { id: 'key-takeaways', name: '1 · Key takeaways ⭐', tier: 1, slot: 'top banner',
      note: 'Strongest UX evidence of any device — at the top it lets a reader judge relevance in a glance. Surfaces the engine’s existing per-page recap as a visible box. BUILD FIRST.',
      data: { takeaways: [
        'Number was the substance of reality, not merely a tool for counting it.',
        'Musical harmony obeys simple whole-number ratios — the octave is 2:1, the fifth 3:2.',
        'If sound is number made audible, the heavens might be number made visible: the music of the spheres.',
        'The tetractys (1+2+3+4 = 10) was the sacred figure holding those ratios.',
        'The irrational diagonal of a square broke the dream of a wholly rational world.',
      ] } },
    { id: 'callout', name: '2 · Callout / admonition', tier: 1, slot: 'inline box',
      note: 'ONE component, typed (note·tip·key-insight·question·caution·quote). Highest-value + most on-creed — a re-pitched body sentence flagged as a box. GitHub / MkDocs / Notion. Cap 1–2 per page.',
      data: { callout: { kind: 'key-insight', text: 'If music is number made audible, then perhaps the heavens are number made visible — the leap from a plucked string to an entire cosmos.' } } },
    { id: 'pull-quote', name: '3 · Pull-quote', tier: 1, slot: 'margin lane',
      needs: 'aria-hidden (duplicates body)',
      note: 'The single most striking body line, lifted into the margin beside its source sentence. It DUPLICATES body text, so the figure is aria-hidden (already out of the TTS walk). Suppress during narration of that passage.',
      data: { quote: { text: 'Harmony, the most bodily of pleasures, turned out to obey plain arithmetic.' } } },
    { id: 'tldr', name: '4 · TL;DR', tier: 1, slot: 'top',
      note: 'One-sentence ultra-compression — the visible form of the creed’s length axis.',
      data: { tldr: 'The Pythagoreans believed the cosmos was built from whole numbers — a faith born in musical ratios and shaken by the irrational.' } },
    { id: 'headline-stack', name: '5 · Headline stack', tier: 1, slot: 'top',
      note: 'The opening, typographically segmented: kicker → display title → deck. Establishes hierarchy and an entry point.',
      data: { kicker: 'Pythagorean cosmos', title: 'The Music of the Spheres', deck: 'How a faith in whole numbers turned harmony into a theory of the heavens.' } },
    { id: 'deck', name: '6 · Deck / standfirst', tier: 1, slot: 'top',
      note: 'A 1–2-sentence framing under the title, larger than body — an entry point.',
      data: { deck: 'How a faith in whole numbers turned harmony into a theory of the heavens — and how a single proof nearly broke it apart.' } },
    { id: 'kicker', name: '7 · Kicker / eyebrow', tier: 1, slot: 'top',
      note: 'A teaser / section label above the title (small caps, tracked). Can map from a graph-centrality “section.”',
      data: { kicker: 'Pythagorean cosmos · harmony' } },
    { id: 'stepped-list', name: '8 · Stepped list', tier: 1, slot: 'inset panel',
      note: 'Enumerable / sequential content as a numbered panel — ties to form=guided. Derivable when the page lays out a sequence.',
      data: { steps: [
        'Pluck a string, then stop it at its midpoint — the pitch leaps an octave (a 2:1 ratio).',
        'Stop it a third of the way along — a perfect fifth sounds (3:2).',
        'Stop it a quarter of the way — a perfect fourth (4:3).',
        'Conclude: the consonances are nothing but small whole-number ratios.',
      ] } },
    { id: 'comparison', name: '9 · Comparison / contrast', tier: 1, slot: 'panel / banner',
      note: 'Two juxtaposed items (A vs B); stacks under a narrow card. The contradiction-ledger “by-design tensions” are a natural source.',
      data: { comparison: {
        aTitle: 'The rational', a: 'Octave 2:1, fifth 3:2, fourth 4:3 — every concord a ratio of whole numbers.',
        bTitle: 'The irrational', b: 'The diagonal of a unit square: a length no ratio of whole numbers can name.',
      } } },
    { id: 'accordion', name: '10 · Accordion / detail', tier: 1, slot: 'inline',
      note: 'A deep-dive behind a native <details> toggle (keyboard + Ctrl-F once open). Never hide must-read content — only the optional dive.',
      data: { accordion: {
        summary: 'Why was the man who revealed the irrational “lost at sea”?',
        detail: 'Legend names him Hippasus, drowned — by the gods, or by his fellow Pythagoreans — for divulging that √2 cannot be written as a ratio of whole numbers. The tale is almost certainly apocryphal, but it captures how dangerous the discovery felt to a brotherhood that had staked everything on number.',
      } } },
    { id: 'read-time', name: '11 · Read-time / progress', tier: 1, slot: 'chrome strip',
      note: 'Estimated time + progress. Drive the progress from NARRATION time, not word count (the engine already has a cost/coast meter).',
      data: { readTime: { mins: 3, progress: 0.42 } } },
    { id: 'sidenote', name: '12 · Sidenote / marginalia', tier: 1, slot: 'margin lane',
      note: 'A gloss keyed to one line, in the margin. Flow-safe (saccade only) — the safest companion to narration; may sit live while a passage is read.',
      data: { sidenote: { afterPara: 3, marker: '*', text: 'The tetractys: ten points in rows of 1, 2, 3 and 4, forming a triangle whose rows hold the musical ratios.' } } },
    { id: 'drop-cap', name: '13 · Drop-cap', tier: 1, slot: 'inline (::first-letter)',
      note: 'A dropped initial via initial-letter / ::first-letter — NEVER a node extracted from the text, so the offset map is untouched. Float fallback for Firefox.' },
    { id: 'raised-initial', name: '14 · Raised initial', tier: 1, slot: 'inline (::first-letter)',
      note: 'The easier-to-set alternative to the drop-cap (Bringhurst): a large cap on the baseline. One opening device per page.' },

    // ── Tier 2 — needs the universal-ingest enrichment (mock data) ───────
    { id: 'big-number', name: '15 · By-the-numbers', tier: 2, slot: 'inset', needs: 'claims layer',
      note: 'A number + its referent as an F-pattern landmark. Fires only on a clean number+referent or it fabricates precision.',
      data: { bigNumber: { value: '10', label: 'the perfect number — the rows of the tetractys (1+2+3+4) sum to ten' } } },
    { id: 'see-also', name: '16 · Related / see-also', tier: 2, slot: 'margin / end', needs: 'wikilink graph + centrality (have)',
      note: 'Surfaces edges you already have; pair with the “✧ missed-connection” detector. Keep the list short.',
      data: { seeAlso: [
        { title: 'The Tetractys', note: 'the sacred triangle of ten' },
        { title: 'Music of the Spheres' },
        { title: 'Hippasus & the Irrational' },
        { title: 'The World Soul', note: '✧ unexpected link · 0.85' },
      ] } },
    { id: 'source-strip', name: '17 · Source / grounding strip', tier: 2, slot: 'foot strip', needs: 'sources: + grounding (have)',
      note: 'Doubles as a TRUST strip for model-generated prose — “synthesized from N sources, grounded.”',
      data: { sources: { count: 9, grounded: true } } },
    { id: 'glossary', name: '18 · Running glossary', tier: 2, slot: 'margin', needs: 'terms / definitions',
      note: 'The multi-term aggregate of the single key-term box.',
      data: { glossary: [
        { term: 'Tetractys', def: 'A triangle of ten points in rows of 1–4; the Pythagorean holy figure.' },
        { term: 'Monochord', def: 'A single-string instrument for hearing whole-number ratios.' },
        { term: 'Harmonia', def: 'The fitting-together of opposites into a tuned whole.' },
      ] } },
    { id: 'definition', name: '19 · Inline definition tooltip', tier: 2, slot: 'inline', needs: 'terms (focus + Esc, WCAG 1.4.13)',
      note: 'The TERM stays in the body (narrated); the gloss is data-narration="skip" and revealed on hover OR keyboard focus. Hover the dotted word.',
      data: { definition: { term: 'tetractys', def: 'A triangle of ten points in rows of one, two, three and four — the rows sum to the perfect ten.', afterPara: 3 } } },
    { id: 'timeline', name: '20 · Timeline strip', tier: 2, slot: 'banner', needs: 'temporal anchors',
      note: 'A chronology strip — really a Tier-2 VIEW (chronological traversal), shown here as a per-page banner. Knight Lab TimelineJS pattern.',
      data: { timeline: [
        { when: 'c. 570 BCE', what: 'Pythagoras born on Samos' },
        { when: 'c. 530 BCE', what: 'Founds the school at Croton' },
        { when: '5th c. BCE', what: 'The monochord ratios' },
        { when: '5th c. BCE', what: 'The irrational discovered' },
      ] } },
  ];

  const SIZES: Record<string, [number, number]> = {
    'Portrait 5:7': [600, 840], 'Tablet 3:4': [720, 960], 'Phone': [380, 620], 'Wide 4:3': [840, 630],
  };
  let sizeKey = $state('Portrait 5:7');
  let fontPx = $state(16);
  let autofit = $state(true);
  let formSel = $state<FormId>('article');   // which output form to gate figures against
  let hideBlocked = $state(false);           // dim vs hide figures blocked in this form
  const dims = $derived(SIZES[sizeKey]);
  const formLabel = $derived(FORMS.find((f) => f.id === formSel)?.label ?? formSel);
  const AFF_TAG: Record<string, string> = { native: '★ native', allowed: '✓ allowed', blocked: '✗ blocked' };

  let report = $state<Record<string, { font: number; over: number }>>({});

  // Mirror the real reader's fit-to-page: shrink the font until the page fits the
  // card (text-figures route through DOM-fit, like forms/marks). Report the
  // fitted font + any residual overflow so a template that can't fit shows up.
  function overflowOf(card: HTMLElement): number {
    const pl = card.querySelector('.tf-page') as HTMLElement | null;
    if (!pl) return 0;
    return pl.scrollHeight - card.clientHeight;
  }
  $effect(() => {
    void fontPx; void sizeKey; void autofit; void demos.length;
    queueMicrotask(() => {
      const rep: Record<string, { font: number; over: number }> = {};
      for (const d of demos) {
        const card = document.querySelector<HTMLElement>(`.tflab .card[data-tpl="${d.id}"]`);
        if (!card) continue;
        let fitted = fontPx;
        if (autofit) {
          const fit = (fs: number) => { card.style.fontSize = fs + 'px'; return overflowOf(card) <= 1; };
          if (!fit(fontPx)) {
            let lo = 8, hi = fontPx, best = 8;
            for (let i = 0; i < 9; i++) { const mid = (lo + hi) / 2; if (fit(mid)) { best = mid; lo = mid; } else hi = mid; }
            fitted = best; card.style.fontSize = best + 'px';
          }
        } else { card.style.fontSize = fontPx + 'px'; }
        rep[d.id] = { font: Math.round(fitted * 10) / 10, over: Math.round(overflowOf(card)) };
      }
      report = rep;
    });
  });
</script>

<div class="tflab" style="--reader-fs:{fontPx}px;">
  <header class="bar">
    <strong>Dwell · text-figure lab</strong>
    <span class="sub">{demos.length} templates · derived figures over one page of prose</span>
    <span class="spacer"></span>
    <label>Form
      <select bind:value={formSel}>{#each FORMS as f}<option value={f.id}>{f.label}</option>{/each}</select>
    </label>
    <label>Card
      <select bind:value={sizeKey}>{#each Object.keys(SIZES) as k}<option>{k}</option>{/each}</select>
    </label>
    <label>{autofit ? 'Max font' : 'Font'} {fontPx}px
      <input type="range" min="11" max="20" bind:value={fontPx} />
    </label>
    <label class="chk"><input type="checkbox" bind:checked={autofit} /> Auto-fit</label>
    <label class="chk"><input type="checkbox" bind:checked={hideBlocked} /> Hide blocked</label>
    <span class="hint">Each figure declares a <strong>form affinity</strong> (★ native · ✓ allowed · ✗ blocked) — a figure must never appear in a form that blocks it (e.g. a stepped list in a dialogue). Switch <em>Form</em> to see which light up; blocked ones dim (or hide). Every figure is a <code>&lt;figure data-narration="skip"&gt;</code>, so the body prose is never reshuffled.</span>
  </header>

  <div class="grid" style="--cardw:{dims[0]}px">
    {#each demos as d (d.id)}
      {@const aff = affinity(d.id, formSel)}
      {#if !(hideBlocked && aff === 'blocked')}
      <section class="cell" class:dim={aff === 'blocked'}>
        <div class="label">
          <span class="name">{d.name}</span>
          <span class="tier tier{d.tier}">Tier {d.tier}</span>
          <span class="aff aff-{aff}" title="{AFF_TAG[aff].slice(2)} in {formLabel}">{AFF_TAG[aff]}</span>
          <span class="slot">{d.slot}</span>
          {#if d.needs}<span class="needs">{d.needs}</span>{/if}
          <p class="desc">{d.note}</p>
        </div>
        <div class="card" data-tpl={d.id} style="width:{dims[0]}px; height:{dims[1]}px;">
          <TextFigures figure={d.id} text={PROSE} data={d.data} />
        </div>
        {#if report[d.id]}
          {@const r = report[d.id]}
          <div class="vbadge" class:ok={r.over <= 1} class:bad={r.over > 1}>
            fit {r.font}px · {r.over <= 1 ? 'fits ✓' : `clipped +${r.over}px`}
            <span class="safe">🔇 narration-safe</span>
            {#if d.id === 'pull-quote'}<span class="aria">aria-hidden (dup)</span>{/if}
          </div>
        {/if}
      </section>
      {/if}
    {/each}
  </div>

  <footer class="credits">
    <strong>How this maps to the engine</strong> — text-figures reuse the image-layout machinery
    (<code>DWELL_TEXT_FIGURES_PLAN.md</code>): a “figure” generalizes to image-OR-text, cycled into the
    same slots one-per-page by a shared scheduler. The sidebar figures (pull-quote · sidenote ·
    see-also · glossary) <strong>float right and the body text wraps around them</strong> like an image
    (try the <em>Phone</em> size — they collapse to a block); drop-cap / raised-initial are pure
    <code>::first-letter</code> (no node extraction). A figure is eligible only where it makes sense:
    <strong>form affinity</strong> (this <em>Form</em> picker — blocked figures must never appear) ×
    content-affordance × per-type cooldown × a density dial. Tier-2 cells use mock data — they gate
    behind the universal-ingest enrichment (typed edges · temporal anchors · claims · terms).
  </footer>
</div>

<style>
  .tflab { height: 100vh; overflow: auto; padding: 0 0 60px; background: var(--bg); color: var(--fg); }
  .bar {
    position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
    padding: 10px 20px; background: color-mix(in srgb, var(--panel) 92%, transparent);
    border-bottom: 1px solid var(--border); backdrop-filter: blur(8px);
  }
  .bar strong { font-size: 15px; }
  .sub { color: var(--meta); font-size: 12px; }
  .spacer { flex: 1 1 auto; }
  .bar label { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--meta); }
  .bar select, .bar input[type="range"] { accent-color: var(--accent); }
  .chk { cursor: pointer; }
  .hint { flex-basis: 100%; color: var(--meta); font-size: 11px; font-style: italic; opacity: 0.85; }
  .hint code { font-style: normal; font-size: 10.5px; background: color-mix(in srgb, var(--ink) 8%, transparent); padding: 0 4px; border-radius: 4px; }

  /* Column min tracks the SELECTED card width so cards render at their intended
     size (not clamped to a narrow cell) — otherwise every lane template would
     always collapse and the card-size selector would be inert. */
  .grid {
    display: grid; gap: 30px 26px; padding: 26px 20px;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, var(--cardw, 380px)), 1fr));
    justify-items: center; align-items: start;
  }
  .cell { display: flex; flex-direction: column; gap: 10px; max-width: 100%; transition: opacity 0.15s; }
  /* A figure blocked in the selected form: dimmed (it must never appear there). */
  .cell.dim { opacity: 0.38; }
  .cell.dim:hover { opacity: 0.85; }
  .label { max-width: 560px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
  .name { font-weight: 700; font-size: 14px; }
  .tier { font-size: 10.5px; padding: 1px 7px; border-radius: 999px; font-weight: 700; }
  .tier1 { background: var(--accent); color: var(--bg); }
  .tier2 { background: color-mix(in srgb, var(--ink) 14%, transparent); color: var(--meta); }
  .aff { font-size: 10.5px; padding: 1px 7px; border-radius: 999px; font-weight: 700; border: 1px solid transparent; }
  .aff-native { background: color-mix(in srgb, var(--accent) 22%, transparent); color: var(--accent); border-color: color-mix(in srgb, var(--accent) 45%, transparent); }
  .aff-allowed { color: var(--meta); border-color: var(--border); }
  .aff-blocked { color: #c0392b; border-color: color-mix(in srgb, #c0392b 45%, var(--border)); background: color-mix(in srgb, #c0392b 10%, transparent); }
  .slot { font-size: 11px; color: var(--meta); font-style: italic; }
  .needs { font-size: 10.5px; color: var(--meta); border: 1px dashed var(--border); padding: 0 6px; border-radius: 5px; }
  .desc { flex-basis: 100%; margin: 2px 0 0; color: var(--meta); font-size: 12.5px; line-height: 1.45; }

  /* Mirrors the real reading card (Reader.svelte): theme surface, rounded,
     shadowed, overflow:hidden so a layout that doesn't fit shows as clipped.
     container-type (via .tf-card-host) lets the margin lane collapse by CARD
     width — the card is the query container for the page + its margin figures. */
  .card {
    flex: 0 0 auto; max-width: 100%;
    background: var(--pane); color: var(--ink);
    border-radius: 14px; box-shadow: 0 12px 50px #0006;
    overflow: hidden auto;
    scrollbar-gutter: stable both-edges;
    font-size: var(--reader-fs, 15px);
    container-type: inline-size;
  }

  .vbadge {
    display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: center; max-width: 560px;
    font-family: Consolas, monospace; font-size: 11px;
    padding: 3px 8px; border-radius: 6px; border: 1px solid var(--border);
    background: color-mix(in srgb, var(--panel) 70%, transparent); color: var(--meta);
  }
  .vbadge.ok { color: #2e7d32; border-color: color-mix(in srgb, #2e7d32 40%, var(--border)); }
  .vbadge.bad { color: var(--err, #c62828); border-color: color-mix(in srgb, #c62828 40%, var(--border)); }
  .vbadge .safe, .vbadge .aria { color: var(--meta); opacity: 0.8; }
  .vbadge .aria { font-style: italic; }

  .credits { margin: 10px 22px 0; color: var(--meta); font-size: 12px; line-height: 1.6; max-width: 1000px; }
  .credits code { font-size: 11px; background: color-mix(in srgb, var(--ink) 8%, transparent); padding: 0 4px; border-radius: 4px; }
</style>
