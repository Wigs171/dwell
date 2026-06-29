<script lang="ts">
  // Dev harness (open /#layouts) for eyeballing the figure layouts under real
  // card constraints with the Wikimedia placeholder images. Not part of the
  // shipping app — it's how we pick & tune templates before engine integration.
  import { onMount } from 'svelte';
  import PageLayout from './PageLayout.svelte';
  import { predictContentHeight } from './fitLayout';
  import type { LayoutId, PageImage } from './types';

  type Entry = {
    file: string; title: string; license: string; author: string;
    width: number; height: number; aspect: PageImage['aspect']; source_url: string;
  };

  let manifest = $state<Entry[]>([]);
  onMount(async () => {
    try { manifest = await (await fetch('/layout-demo/manifest.json')).json(); }
    catch (e) { console.error('manifest load failed', e); }
  });

  const PROSE = `To the Pythagoreans, number was not a means of counting the world but the very stuff from which it was woven. A pebble laid beside a pebble made two; three rows of pebbles built a triangle; and in those small arrangements they believed they could read the architecture of the cosmos itself.

The discovery that haunted them most arrived through sound. Pressing a string at its midpoint, Pythagoras is said to have heard the same note an octave higher — a ratio of two to one. A third of the way along sounded a fifth, a quarter a fourth. Harmony, the most bodily of pleasures, turned out to obey plain arithmetic.

From this they drew a daring inference: if music was number made audible, perhaps the heavens were number made visible. The planets, wheeling at their measured distances, must sound their own vast chord — a music of the spheres too constant for mortal ears to notice.

The tetractys, a triangle of ten points in rows of one, two, three, and four, became their holy figure. Its rows summed to the perfect ten and held within them the very ratios of the musical scale. Initiates swore their oaths upon it rather than upon any god.

Yet the same devotion to number nearly undid them. When a student proved that the diagonal of a square could not be written as a ratio of whole numbers, the irrational had entered their perfect world. Legend says the man who first spoke of it aloud was lost at sea.

What survived was not the secrecy but the conviction — that beneath the noise of appearances runs a hidden order, and that the patient study of proportion might bring the soul itself into tune with it. It is an idea that has never quite left us.`;

  const find = (file: string) => manifest.find((m) => m.file === file);
  function pic(file: string, caption: string): PageImage | undefined {
    const e = find(file);
    if (!e) return undefined;
    return { src: `/layout-demo/${file}`, alt: e.title, caption, aspect: e.aspect, w: e.width, h: e.height };
  }

  type Demo = { id: LayoutId; name: string; req?: string; note: string; imgs: (PageImage | undefined)[]; text?: string };
  // Image choices follow each template's "best for" from the research.
  const demos = $derived<Demo[]>([
    { id: 'top', name: '1 · Top-anchored', note: 'Safe default. Full-width figure up top, text below. Suits landscape & wide images; near-impossible to break.',
      imgs: [pic('school-of-athens-frieze.jpg', 'Raphael, The School of Athens (detail).')] },
    { id: 'side', name: '2 · Side column', note: 'The everyday wrap — figure floats top-right, text wraps left & below. Flatters portraits. Needs ≥3 paragraphs.',
      imgs: [pic('pythagoras-bust.jpg', 'Roman bust of Pythagoras, Capitoline Museums.')] },
    { id: 'rail', name: '5 · Tall rail', note: 'Full-height image band beside the text. Absorbs very tall images that break every horizontal slot; height-locked so always safe.',
      imgs: [pic('theorica-musicae-titlepage.jpg', 'Title page of Gaffurio’s Theorica musicae, 1492.')] },
    { id: 'diagonal', name: '3 · Two-image diagonal', req: 'your request #1', note: 'Image top-right + a second partway-down-left. The offset is enforced so the text channel never starves. Pair complementary shapes.',
      imgs: [pic('monochord-pythagoras.jpg', 'A monochord divides one string into whole-number ratios.'),
             pic('pythagoras-with-bells.png', 'Pythagoras testing pitch with bells and pipes.')] },
    { id: 'magazine', name: '4 · Magazine (3-column)', req: 'your request #2', note: 'A thin, column-width portrait sitting in the middle column, with justified body text flowing in three balanced columns around it (CSS multicol — still ONE text flow, so karaoke/selection survive). Use a portrait/square image — it fits one column neatly.',
      imgs: [pic('pythagoras-bust.jpg', 'Roman bust of Pythagoras, Capitoline Museums.')] },
    { id: 'bottom', name: '6 · Bottom-anchored', note: 'Mirror of #1 — figure pinned to the foot. Exists for variety: alternate top/bottom across pages so single-image pages don’t feel templated.',
      imgs: [pic('parthenon-frieze-banner.jpg', 'Alma-Tadema, Phidias Showing the Frieze (detail).')] },
    { id: 'inset', name: '7 · Inset thumbnail', note: 'Small corner figure on a text-dominant page. Lowest-risk float; a palate-cleanser between richer pages.',
      imgs: [pic('golden-square.png', 'The golden ratio, constructed from a square.')] },
    { id: 'mosaic', name: '8 · Editorial mosaic', note: 'Banner + two floated details (3 images). The richest, tightest layout — use sparingly and never beside another image-dense page.',
      imgs: [pic('parthenon-frieze-banner.jpg', 'Phidias Showing the Frieze (detail).'),
             pic('harmonia-gaffurius.png', '“Harmonia” — the consonant ratios, 1492.'),
             pic('golden-square.png', 'Golden ratio.')] },
    { id: 'hero', name: '9 · Full-bleed hero → vault selection', note: 'NOT a reading layout. This is the VAULT-SELECTION card: image fills the card, vault title + blurb in a smooth bottom scrim, white drop-shadowed text legible over any image (no hard blur edge).',
      text: 'The Pythagoras Compendium\n\nA cross-linked vault of number, harmony, and the music of the spheres — 162 pages drawn from the Pythagorean tradition.',
      imgs: [pic('pythagoras-with-bells.png', '162 pages · 9 sources')] },
  ]);

  const SIZES: Record<string, [number, number]> = {
    'Portrait 5:7': [600, 840], 'Tablet 3:4': [720, 960], 'Phone': [380, 620], 'Wide 4:3': [840, 630],
  };
  let sizeKey = $state('Portrait 5:7');
  let fontPx = $state(16);
  let captions = $state(true);
  let autofit = $state(true);
  let validate = $state(true);
  const dims = $derived(SIZES[sizeKey]);
  // pretext-vs-DOM validation, per template
  let report = $state<Record<string, { font: number; domH: number; predH: number }>>({});
  const paras = PROSE.split(/\n{2,}/).map((s) => s.trim()).filter(Boolean);

  // strip captions when toggled off (tests the no-caption look)
  function maybeCap(imgs: (PageImage | undefined)[]): PageImage[] {
    return imgs.filter(Boolean).map((i) => (captions ? i! : { ...i!, caption: undefined }));
  }

  function overflowOf(card: HTMLElement): number {
    const pl = card.querySelector('.page-layout') as HTMLElement | null;
    if (!pl) return 0;
    let o = pl.scrollHeight - card.clientHeight;
    const rt = pl.querySelector('.rail-text') as HTMLElement | null;   // rail has its own scroll box
    if (rt) o = Math.max(o, rt.scrollHeight - rt.clientHeight);
    const body = pl.querySelector('.body') as HTMLElement | null;      // bottom's flex:1 text box hides overflow from scrollHeight
    if (body) o = Math.max(o, body.scrollHeight - body.clientHeight);
    return o;
  }

  // Mirror the real reader's fit-to-page (shrink the font until the page fits the
  // card) AND validate pretext against it: at the fitted font, compare pretext's
  // PREDICTED content height to the browser's ACTUAL height. Close ⇒ pretext can
  // drive the image-aware fit in the real reader without DOM reflow.
  $effect(() => {
    void fontPx; void sizeKey; void captions; void autofit; void validate; void demos.length; void manifest.length;
    queueMicrotask(() => {
      const rep: Record<string, { font: number; domH: number; predH: number }> = {};
      for (const d of demos) {
        const card = document.querySelector<HTMLElement>(`.lab .card[data-tpl="${d.id}"]`);
        const pl = card?.querySelector('.page-layout') as HTMLElement | null;
        if (!card || !pl) continue;
        let fitted = fontPx;
        if (autofit) {
          const fit = (fs: number) => { card.style.fontSize = fs + 'px'; return overflowOf(card) <= 1; };
          if (!fit(fontPx)) {
            let lo = 8, hi = fontPx, best = 8;
            for (let i = 0; i < 9; i++) { const mid = (lo + hi) / 2; if (fit(mid)) { best = mid; lo = mid; } else hi = mid; }
            fitted = best; card.style.fontSize = best + 'px';
          }
        } else { card.style.fontSize = fontPx + 'px'; }
        if (validate) {
          const cap = captions && d.id !== 'rail' ? d.imgs[0]?.caption : undefined;
          const rt = pl.querySelector('.rail-text') as HTMLElement | null;
          const domH = rt ? rt.scrollHeight : pl.scrollHeight;
          const predH = predictContentHeight(d.id, card.clientWidth, card.clientHeight, fitted, { paras, caption: cap });
          rep[d.id] = { font: Math.round(fitted * 10) / 10, domH: Math.round(domH), predH: Math.round(predH) };
        }
      }
      report = rep;
    });
  });
</script>

<div class="lab" style="--reader-fs:{fontPx}px;">
  <header class="bar">
    <strong>Dwell · layout lab</strong>
    <span class="sub">{demos.length} templates · {manifest.length} placeholder figures</span>
    <span class="spacer"></span>
    <label>Card
      <select bind:value={sizeKey}>{#each Object.keys(SIZES) as k}<option>{k}</option>{/each}</select>
    </label>
    <label>{autofit ? 'Max font' : 'Font'} {fontPx}px
      <input type="range" min="11" max="20" bind:value={fontPx} />
    </label>
    <label class="chk"><input type="checkbox" bind:checked={autofit} /> Auto-fit</label>
    <label class="chk"><input type="checkbox" bind:checked={captions} /> Captions</label>
    <label class="chk"><input type="checkbox" bind:checked={validate} /> Validate pretext</label>
    <span class="hint">Auto-fit shrinks each card's font to fit the page (what the real reader does); turn it off to see raw overflow at a fixed size.</span>
  </header>

  <div class="grid">
    {#each demos as d (d.id)}
      <section class="cell">
        <div class="label">
          <span class="name">{d.name}</span>
          {#if d.req}<span class="req">{d.req}</span>{/if}
          <p class="desc">{d.note}</p>
        </div>
        <div class="card" data-tpl={d.id} style="width:{dims[0]}px; height:{dims[1]}px;">
          <PageLayout layout={d.id} text={d.text ?? PROSE} images={maybeCap(d.imgs)} />
        </div>
        {#if validate && report[d.id]}
          {@const r = report[d.id]}
          {#if d.id === 'hero'}
            <div class="vbadge ok">fit {r.font}px · image-driven — text overlay always fits</div>
          {:else}
            {@const dpct = r.domH ? Math.round(((r.predH - r.domH) / r.domH) * 1000) / 10 : 0}
            <div class="vbadge" class:ok={Math.abs(dpct) < 4} class:warn={Math.abs(dpct) >= 4 && Math.abs(dpct) < 10} class:bad={Math.abs(dpct) >= 10}>
              fit {r.font}px · pretext {r.predH} vs DOM {r.domH}px · Δ{dpct > 0 ? '+' : ''}{dpct}%
            </div>
          {/if}
        {/if}
      </section>
    {/each}
  </div>

  {#if manifest.length}
    <footer class="credits">
      <strong>Placeholder figures</strong> (Wikimedia Commons — swapped for real vault images at integration):
      <ul>
        {#each manifest as m}
          <li><a href={m.source_url} target="_blank" rel="noopener">{m.title}</a> — {m.license} · {m.width}×{m.height} ({m.aspect})</li>
        {/each}
      </ul>
    </footer>
  {/if}
</div>

<style>
  .lab { height: 100vh; overflow: auto; padding: 0 0 60px; background: var(--bg); color: var(--fg); }
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
  .hint { flex-basis: 100%; color: var(--meta); font-size: 11px; font-style: italic; opacity: 0.8; }

  .grid {
    display: grid; gap: 30px 26px; padding: 26px 20px;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 380px), 1fr));
    justify-items: center; align-items: start;
  }
  .cell { display: flex; flex-direction: column; gap: 10px; max-width: 100%; }
  .label { max-width: 540px; }
  .name { font-weight: 700; font-size: 14px; }
  .req {
    margin-left: 8px; font-size: 11px; padding: 1px 7px; border-radius: 999px;
    background: var(--accent); color: var(--bg); vertical-align: middle;
  }
  .desc { margin: 4px 0 0; color: var(--meta); font-size: 12.5px; line-height: 1.45; }

  /* Mirrors the real reading card (Reader.svelte): theme surface, rounded,
     shadowed, and overflow:hidden so a layout that doesn't fit shows as clipped. */
  .card {
    flex: 0 0 auto; max-width: 100%;
    background: var(--pane); color: var(--ink);
    border-radius: 14px; box-shadow: 0 12px 50px #0006;
    overflow: hidden auto;   /* fits cleanly at the fitted font; scrolls when font is scaled past fit (mirrors the reader's zoom) */
    scrollbar-gutter: stable both-edges; /* reserve the gutter on BOTH sides: no fit-feedback when it appears, and content stays symmetric (centred images stay centred) */
    font-size: var(--reader-fs, 15px);
  }

  .vbadge {
    max-width: 540px; font-family: Consolas, monospace; font-size: 11px;
    padding: 3px 8px; border-radius: 6px; border: 1px solid var(--border);
    background: color-mix(in srgb, var(--panel) 70%, transparent); color: var(--meta);
  }
  .vbadge.ok { color: #2e7d32; border-color: color-mix(in srgb, #2e7d32 40%, var(--border)); }
  .vbadge.warn { color: #b8860b; border-color: color-mix(in srgb, #b8860b 40%, var(--border)); }
  .vbadge.bad { color: var(--err, #c62828); border-color: color-mix(in srgb, #c62828 40%, var(--border)); }

  .credits { margin: 10px 22px 0; color: var(--meta); font-size: 12px; line-height: 1.5; }
  .credits ul { margin: 6px 0 0; padding-left: 18px; }
  .credits a { color: var(--accent); }
</style>
