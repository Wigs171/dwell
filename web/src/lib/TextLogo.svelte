<script lang="ts">
  // The DWELL wordmark, rendered as a grid of monospace text characters from a
  // baked mask (public/dwell-logo-mask.json — sampled from the real logo). The
  // tilted notebook-E cells carry the theme --accent; the rest is --fg. On mount
  // the glyphs diffuse in from noise (the house style); when `flow` is on, a
  // stream of words drifts through the letterforms.
  //
  // Layout is computed analytically (no DOM measure): cell advance comes from a
  // one-time canvas measureText, and the block is centred + scaled to fit.
  import { onMount, onDestroy } from 'svelte';

  interface Props {
    flow?: boolean;        // words drift through the shape
    flowSpeed?: number;    // cells / second
    intro?: boolean;       // diffuse-in on mount
    solid?: boolean;       // strip spaces so letterforms stay filled
    mode?: 'words' | 'blocks' | 'noise';
    fit?: number;          // 0..1 fraction of the container to fill
  }
  let { flow = true, flowSpeed = 4, intro = true, solid = true, mode = 'words', fit = 0.94 }: Props = $props();

  const FONT = 'ui-monospace, "SF Mono", Consolas, "Liberation Mono", monospace';
  const ASPECT_CELL = 0.507;   // sampled cell aspect (w:h) = (cw/cols)/(ch/rows)
  const PROSE =
    'dwell here a while and let the pages drift for every reading is a tide that ' +
    'turns the same knowledge into something never read the same way twice wander ' +
    'the graph and the words rise to meet you gathering and dispersing like swells ' +
    'upon a quiet sea each idea a current beneath the surface each link a wave ';
  const NOISE = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&$@?<>/\\=+*';
  const src = $derived(solid ? PROSE.replace(/ /g, '') : PROSE);

  type Run = { accent: boolean; text: string };
  let rows = $state<Run[][]>([]);
  let mask: { cols: number; rows: number; grid: number[][] } | null = null;
  let packIdx: number[][] = [];   // packed on-cell index per cell (row-major)

  let host = $state<HTMLDivElement>();
  let fsPx = $state(24), lhPx = $state(34), scale = $state(1);
  let alive = true, raf = 0, t0 = 0, off = 0, last = 0;

  function hash(n: number): number {
    let h = 2166136261 ^ n;
    h = Math.imul(h, 16777619); h ^= h >>> 13; h = Math.imul(h, 16777619);
    return (h >>> 0);
  }

  function charFor(r: number, c: number, intoT: number): string {
    const pk = packIdx[r][c];
    if (intro && intoT < 1) {
      const settle = (hash(pk * 7 + 11) % 1000) / 1000;
      if (settle > intoT) return NOISE[(Math.random() * NOISE.length) | 0];
    }
    if (mode === 'blocks') return '█';
    if (mode === 'noise') return NOISE[(hash(pk + (off | 0) * 131) % NOISE.length)];
    const s = src;
    const ch = s[(pk + (off | 0)) % s.length];
    return ch === ' ' ? ' ' : ch;
  }

  function build(intoT: number) {
    if (!mask) return;
    const g = mask.grid, out: Run[][] = [];
    for (let r = 0; r < mask.rows; r++) {
      const runs: Run[] = [];
      let c = 0;
      while (c < mask.cols) {
        const v = g[r][c];
        if (v === 0) {
          let s = c; while (c < mask.cols && g[r][c] === 0) c++;
          runs.push({ accent: false, text: ' '.repeat(c - s) });
        } else {
          const accent = v === 2; let txt = '';
          while (c < mask.cols && g[r][c] !== 0 && (g[r][c] === 2) === accent) { txt += charFor(r, c, intoT); c++; }
          runs.push({ accent, text: txt });
        }
      }
      out.push(runs);
    }
    rows = out;
  }

  function computeScale() {
    if (!host || !mask) return;
    const natW = mask.cols * fsPx * advance;
    const natH = mask.rows * lhPx;
    const hw = host.clientWidth, hh = host.clientHeight;
    if (!hw || !hh) return;
    scale = Math.min((hw * fit) / natW, (hh * fit) / natH);
  }

  let advance = 0.6;   // monospace advance / font-size, measured once
  function measureAdvance() {
    const cv = document.createElement('canvas');
    const cx = cv.getContext('2d')!;
    cx.font = `100px ${FONT}`;
    advance = cx.measureText('MMMMMMMMMM').width / 1000;   // px per (font-size * char)
    lhPx = Math.round((fsPx * advance) / ASPECT_CELL);
  }

  function loop(now: number) {
    if (!alive) return;
    if (!t0) t0 = last = now;
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    const intoT = intro ? Math.min(1, (now - t0) / 1200) : 1;
    if (flow) off += flowSpeed * dt;
    build(intoT);
    // keep animating while introducing or flowing; otherwise settle and stop.
    if (intoT < 1 || flow) raf = requestAnimationFrame(loop);
  }

  let ro: ResizeObserver | null = null;
  onMount(async () => {
    measureAdvance();
    try {
      mask = await (await fetch('/dwell-logo-mask.json')).json();
    } catch (e) { console.error('logo mask load failed', e); return; }
    // assign packed on-cell indices in reading order
    let k = 0; packIdx = [];
    for (let r = 0; r < mask!.rows; r++) {
      const row: number[] = [];
      for (let c = 0; c < mask!.cols; c++) { row.push(k); if (mask!.grid[r][c] !== 0) k++; }
      packIdx.push(row);
    }
    build(intro ? 0 : 1);
    computeScale();
    if (host) { ro = new ResizeObserver(computeScale); ro.observe(host); }
    raf = requestAnimationFrame(loop);
  });
  onDestroy(() => { alive = false; cancelAnimationFrame(raf); ro?.disconnect(); });

  // restart the loop if animation props change after mount
  $effect(() => {
    void flow; void mode; void solid; void flowSpeed;
    if (mask && alive) { cancelAnimationFrame(raf); last = 0; raf = requestAnimationFrame(loop); }
  });
</script>

<div class="logo-host" bind:this={host} aria-label="Dwell">
  <pre class="block" style="font-size:{fsPx}px; line-height:{lhPx}px; transform:translate(-50%,-50%) scale({scale});">{#each rows as row, ri (ri)}<div class="ln">{#each row as run, ci (ci)}{#if run.accent}<span class="ac">{run.text}</span>{:else}<span>{run.text}</span>{/if}{/each}</div>{/each}</pre>
</div>

<style>
  .logo-host { position: relative; width: 100%; height: 100%; overflow: hidden; }
  .block {
    position: absolute; top: 50%; left: 50%; transform-origin: center center;
    margin: 0; padding: 0; white-space: pre;
    font-family: ui-monospace, 'SF Mono', Consolas, 'Liberation Mono', monospace;
    font-weight: 700; letter-spacing: 0; color: var(--fg);
    /* halo in the page color punches the logo out of the sea behind it */
    text-shadow: 0 0 .6em var(--bg), 0 0 .6em var(--bg), 0 0 .35em var(--bg), 0 0 .35em var(--bg);
    user-select: none; -webkit-user-select: none;
  }
  .ln { display: block; }
  .ac { color: var(--accent); }
</style>
