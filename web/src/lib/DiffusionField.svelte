<script lang="ts">
  // The home hero as a TEXT-DIFFUSION field. The background is a lattice of small
  // jumbled monospace characters (the noise a text-diffusion model starts from).
  // The DWELL wordmark sits inside that lattice as SOLID BLOCKS — every logo cell
  // is a "pixel". Independently and at random, individual pixels run a tiny
  // diffusion animation: they churn through jumbled characters for a few frames,
  // then resolve back into a block. Only a minority churn at once, so the mark
  // stays legible while shimmering. No opacity ramps — every block is full ink;
  // the book (tilted notebook-E) is the theme accent.
  //
  // A faint "masonry gridlines" lattice (the masonry-gridlanes look from
  // awesome-pretext) sits behind. One canvas, sized by a one-time canvas measure
  // (no DOM reflow); colors come from theme vars.
  import { onMount, onDestroy } from 'svelte';

  interface Props {
    diffuseFrac?: number;   // target fraction of logo pixels churning at once
    diffuseLen?: number;    // how many shuffle-steps a pixel churns before resolving
    cellPref?: number;      // preferred character cell width (px); fit may shrink it
    logoFrac?: number;      // logo width as a fraction of the container
    vPos?: number;          // vertical position of the logo (0=top .. 0.5=center .. 1=bottom)
    noiseAlpha?: number;    // faintness of the background jumble
    logoAlpha?: number;     // ink of the logo blocks (constant — no ramp)
    shuffleHz?: number;     // how often the jumble + churn step
    accentChance?: number;  // fraction of background jumble chars tinted accent
    gridlines?: boolean;    // masonry gridlines overlay
    noise?: boolean;        // fill the space around the logo with jumbled characters
  }
  let {
    diffuseFrac = 0.14, diffuseLen = 6, cellPref = 13, logoFrac = 0.82, vPos = 0.4,
    noiseAlpha = 0.13, logoAlpha = 0.95, shuffleHz = 9, accentChance = 0.05,
    gridlines = true, noise = true,
  }: Props = $props();

  const NOISE = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&$@?<>/\\=+*{}[]~';
  const BLOCK = '█';   // █

  let host = $state<HTMLDivElement>();
  let canvas = $state<HTMLCanvasElement>();
  let mask: { cols: number; rows: number; aspect?: number; grid: number[][] } | null = null;
  let pack: number[][] = [];          // on-cell index per logo cell (row-major)
  let onCount = 0;                    // number of logo "pixels"
  let diffEnd: Int32Array = new Int32Array(0);   // step at which each pixel's churn ends

  let ctx: CanvasRenderingContext2D;
  let dpr = 1, W = 0, H = 0;
  let colW = 13, rowH = 26, F = 13;
  let logoX0 = 0, logoY0 = 0;
  let col = { fg: '#ccc', accent: '#e06c75', bg: '#222', border: '#444' };
  let bands: { x: number; y: number; w: number; h: number }[] = [];
  let raf = 0, alive = true, lastDraw = 0, shuffleSeed = 0, lastShuffle = 0, step = 0;

  const hash = (n: number) => { let h = 2166136261 ^ n; h = Math.imul(h, 16777619); h ^= h >>> 13; return (h >>> 0); };
  const frac = (n: number) => (hash(n) % 100000) / 100000;

  function readColors() {
    const cs = getComputedStyle(document.documentElement);
    col = {
      fg: cs.getPropertyValue('--fg').trim() || '#ccc',
      accent: cs.getPropertyValue('--accent').trim() || '#e06c75',
      bg: cs.getPropertyValue('--bg').trim() || '#222',
      border: cs.getPropertyValue('--border').trim() || '#444',
    };
  }

  let advRatio = 0.6;
  function measure() {
    const cv = document.createElement('canvas'); const cx = cv.getContext('2d')!;
    cx.font = `100px ui-monospace, "SF Mono", Consolas, monospace`;
    advRatio = cx.measureText('MMMMMMMMMM').width / 1000;
  }

  function layoutGrid() {
    if (!mask || !host) return;
    W = host.clientWidth; H = host.clientHeight;
    const ca = mask.aspect ?? 2.4164;             // logo content width:height
    const byW = (W * logoFrac) / mask.cols;       // fit width
    const byH = (H * 0.7 * ca) / mask.cols;       // fit height (logo h = cols*colW/ca)
    colW = Math.max(2, Math.min(cellPref, byW, byH));
    rowH = (mask.cols * colW) / (mask.rows * ca); // keep the logo's true proportions
    F = colW / advRatio;
    logoX0 = (W - mask.cols * colW) / 2;
    logoY0 = (H - mask.rows * rowH) * vPos;
    bands = [];
    if (gridlines) {
      const lanes = Math.max(3, Math.round(W / 230));
      const laneW = W / lanes;
      for (let l = 0; l < lanes; l++) {
        let y = -frac(l * 31 + 5) * 120, i = 0;
        while (y < H) {
          const bh = rowH * (3 + Math.floor(frac(l * 91 + i * 17) * 6));
          bands.push({ x: l * laneW, y, w: laneW, h: bh });
          y += bh; i++;
        }
      }
    }
    canvas!.width = Math.max(1, W * dpr); canvas!.height = Math.max(1, H * dpr);
    canvas!.style.width = '100%'; canvas!.style.height = '100%';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  // advance the per-pixel diffusion state one shuffle-step: each settled pixel has
  // a small chance to START churning; churning pixels resolve when their timer ends.
  function stepDiffusion() {
    step++;
    const startProb = diffuseLen > 0 ? diffuseFrac / diffuseLen : 0;
    for (let i = 0; i < onCount; i++) {
      if (step >= diffEnd[i] && Math.random() < startProb) {
        diffEnd[i] = step + 1 + Math.floor(diffuseLen * (0.6 + Math.random() * 0.8));
      }
    }
  }

  function draw(now: number) {
    if (!alive || !mask) return;
    raf = requestAnimationFrame(draw);
    if (!lastDraw) lastDraw = lastShuffle = now;
    if (now - lastDraw < 1000 / 30) return;
    lastDraw = now;
    if (now - lastShuffle >= 1000 / shuffleHz) { shuffleSeed++; lastShuffle = now; stepDiffusion(); }

    ctx.clearRect(0, 0, W, H);

    if (gridlines && bands.length) {
      ctx.globalAlpha = 0.1; ctx.strokeStyle = col.border; ctx.lineWidth = 1;
      ctx.beginPath();
      for (const b of bands) ctx.rect(Math.round(b.x) + 0.5, Math.round(b.y) + 0.5, Math.round(b.w), Math.round(b.h));
      ctx.stroke();
    }

    ctx.textBaseline = 'top';
    ctx.font = `${F}px ui-monospace, "SF Mono", Consolas, monospace`;
    let curStyle = '';
    const setStyle = (s: string) => { if (s !== curStyle) { ctx.fillStyle = s; curStyle = s; } };

    const mcMin = Math.floor((0 - logoX0) / colW) - 1, mcMax = Math.ceil((W - logoX0) / colW) + 1;
    const mrMin = Math.floor((0 - logoY0) / rowH) - 1, mrMax = Math.ceil((H - logoY0) / rowH) + 1;

    for (let mr = mrMin; mr <= mrMax; mr++) {
      const y = logoY0 + mr * rowH;
      for (let mc = mcMin; mc <= mcMax; mc++) {
        const x = logoX0 + mc * colW;
        const inLogo = mc >= 0 && mc < mask.cols && mr >= 0 && mr < mask.rows;
        const val = inLogo ? mask.grid[mr][mc] : 0;

        if (val !== 0) {
          // a logo pixel: solid block, unless it's mid-churn (jumbled chars)
          const id = pack[mr][mc];
          const churning = step < diffEnd[id];
          const ch = churning ? NOISE[hash(id * 2654435761 + shuffleSeed) % NOISE.length] : BLOCK;
          ctx.globalAlpha = logoAlpha;
          setStyle(val === 2 ? col.accent : col.fg);
          ctx.fillText(ch, x, y);
          continue;
        }
        if (!noise) continue;                          // logo only — no surrounding jumble
        // background jumble
        const r = hash(mc * 92821 + mr * 31 + shuffleSeed * 2654435761);
        ctx.globalAlpha = noiseAlpha * (0.45 + (r % 100) / 100 * 0.85);
        setStyle((r % 1000) < accentChance * 1000 ? col.accent : col.fg);
        ctx.fillText(NOISE[r % NOISE.length], x, y);
      }
    }
    ctx.globalAlpha = 1;
  }

  let ro: ResizeObserver | null = null, mo: MutationObserver | null = null;
  onMount(async () => {
    ctx = canvas!.getContext('2d')!;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    measure(); readColors();
    try { mask = await (await fetch('/dwell-logo-mask.json')).json(); }
    catch (e) { console.error('logo mask load failed', e); return; }
    if (!mask) return;
    let k = 0; pack = [];
    for (let r = 0; r < mask.rows; r++) { const row: number[] = []; for (let c = 0; c < mask.cols; c++) { row.push(k); if (mask.grid[r][c] !== 0) k++; } pack.push(row); }
    onCount = k; diffEnd = new Int32Array(k);
    layoutGrid();
    ro = new ResizeObserver(() => layoutGrid()); ro.observe(host!);
    mo = new MutationObserver(() => readColors());
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'class'] });
    raf = requestAnimationFrame(draw);
  });
  onDestroy(() => { alive = false; cancelAnimationFrame(raf); ro?.disconnect(); mo?.disconnect(); });

  $effect(() => { void cellPref; void logoFrac; void gridlines; void vPos; if (mask && host) layoutGrid(); });
</script>

<div class="field" bind:this={host} aria-label="Dwell">
  <canvas bind:this={canvas}></canvas>
</div>

<style>
  .field { position: absolute; inset: 0; overflow: hidden; }
  canvas { display: block; width: 100%; height: 100%; }
</style>
