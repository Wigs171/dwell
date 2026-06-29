// Pretext-driven text backgrounds for the home hero — a "sea of words".
//
// In the spirit of github.com/bluedusk/awesome-pretext: the WRAP GEOMETRY is
// computed by @chenglou/pretext (DOM-free line breaking / word measurement),
// then we paint to a canvas with our own wave displacement so the words flow
// like an ocean. Three renderers share one canvas + rAF scaffold:
//
//   • sea-of-words  — prose wrapped by pretext, the whole field rises on a tide
//                     while every word bobs on a 2D swell (the headline look).
//   • word-tide     — horizontal lanes of words drift on a current, seamless
//                     wrap (lane width measured by pretext).
//   • rising-words  — single words ascend like bubbles, fading on a sine drift.
//
// Colors are passed in as hex (the Svelte host reads them from CSS theme vars),
// so switching theme re-tints the sea. Nothing here touches the DOM for layout.

import {
  prepareWithSegments, layoutWithLines, measureNaturalWidth,
  type PreparedTextWithSegments,
} from '@chenglou/pretext';

export type BgName = 'sea-of-words' | 'word-tide' | 'rising-words' | 'none';

export interface BgOptions {
  name: BgName;
  fg: string;            // base ink (hex #rrggbb)
  accent: string;        // book/accent ink (hex)
  bg: string;            // page background (hex) — used for edge melt
  speed: number;         // 0.2 .. 3   (time multiplier)
  density: number;       // 0.5 .. 2   (smaller font + more words as it grows)
  opacity: number;       // 0 .. 1     (overall sea brightness)
  accentChance: number;  // 0 .. 1     (fraction of words tinted accent)
  calm: number;          // 0 .. 1     (how much the logo band is dimmed)
}

export const BG_NAMES: { id: BgName; label: string }[] = [
  { id: 'sea-of-words', label: 'Sea of Words' },
  { id: 'word-tide', label: 'Word Tide' },
  { id: 'rising-words', label: 'Rising Words' },
  { id: 'none', label: 'None' },
];

const FAMILY = 'Georgia, "Times New Roman", serif';

// A flowing meditation — read as one continuous body so pretext wraps it into a
// "surface". Loops seamlessly (last clause flows back into the first).
const PROSE =
  'Dwell here a while and let the pages drift, for every reading is a tide that ' +
  'turns the same knowledge into something never read the same way twice. Wander ' +
  'the graph and the words rise to meet you, gathering and dispersing like swells ' +
  'upon a quiet sea, each idea a current beneath the surface, each link a wave ' +
  'that carries you onward into the deep and patient water of attention, where ' +
  'meaning is not found but slowly forms, dissolves, and forms again. ';

// Single evocative words for the lane / bubble renderers.
const WORDS = (
  'dwell read wander drift tide current wave swell deep attention meaning page ' +
  'graph link knowledge idea quiet patient flow gather disperse rise surface ' +
  'water onward form dissolve again here while every never same twice slowly'
).split(/\s+/);

function hexRgb(hex: string): [number, number, number] {
  const h = (hex || '#888').replace('#', '');
  const s = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const n = parseInt(s, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const rgba = (rgb: [number, number, number], a: number) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;

export function createPretextBg(canvas: HTMLCanvasElement, initial: BgOptions) {
  const ctx = canvas.getContext('2d')!;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let opts = { ...initial };
  let W = 0, H = 0;
  let raf = 0;
  let t0 = performance.now();
  let alive = true;

  // ---- per-renderer state (rebuilt on resize / density / renderer change) ----
  let inkFg: [number, number, number] = hexRgb(opts.fg);
  let inkAc: [number, number, number] = hexRgb(opts.accent);

  // sea-of-words
  let seaLines: { words: { text: string; x: number; accent: boolean }[]; w: number }[] = [];
  let seaLH = 0, seaBlockH = 0;

  // word-tide
  type Lane = { y: number; dir: 1 | -1; sp: number; items: { text: string; x: number; w: number; accent: boolean }[]; total: number };
  let lanes: Lane[] = [];

  // rising-words
  type Bubble = { text: string; w: number; x: number; y: number; vy: number; phase: number; amp: number; accent: boolean; fs: number };
  let bubbles: Bubble[] = [];

  const widthCache = new Map<string, number>();
  function wordWidth(word: string, fontPx: number): number {
    const key = fontPx + '|' + word;
    let w = widthCache.get(key);
    if (w == null) {
      const p = prepareWithSegments(word, `${fontPx}px ${FAMILY}`) as PreparedTextWithSegments;
      w = measureNaturalWidth(p);
      widthCache.set(key, w);
    }
    return w;
  }

  function fontPx(): number {
    // density grows by shrinking the glyphs (more words on screen)
    const base = Math.max(13, Math.min(W, H) / 26);
    return Math.round(base / opts.density);
  }

  function buildSea() {
    const fs = Math.max(11, Math.round(fontPx() * 0.66)); // finer than the logo → reads as texture, not headlines
    seaLH = Math.round(fs * 1.95);                         // airy line spacing keeps it from becoming a wall of text
    ctx.font = `${fs}px ${FAMILY}`;
    // pretext owns the wrap: where each word lands at this width.
    const prepared = prepareWithSegments(PROSE.repeat(3), `${fs}px ${FAMILY}`) as PreparedTextWithSegments;
    const { lines } = layoutWithLines(prepared, W + fs * 2, seaLH); // bleed past the right edge
    seaLines = lines.map((ln) => {
      const words = ln.text.split(' ').filter(Boolean);
      let x = 0;
      const out: { text: string; x: number; accent: boolean }[] = [];
      for (const word of words) {
        out.push({ text: word, x, accent: hash(word + x) % 1000 < opts.accentChance * 1000 });
        x += ctx.measureText(word + ' ').width;
      }
      return { words: out, w: ln.width };
    });
    seaBlockH = Math.max(1, seaLines.length) * seaLH;
  }

  function buildLanes() {
    const fs = fontPx();
    const lh = Math.round(fs * 2.1);
    const n = Math.max(3, Math.floor(H / lh) + 2);
    lanes = [];
    for (let i = 0; i < n; i++) {
      const dir: 1 | -1 = i % 2 === 0 ? 1 : -1;
      const items: Lane['items'] = [];
      let x = 0;
      // fill at least 1.6× the width so the ribbon wraps seamlessly
      while (x < W * 1.6 + 400) {
        const word = WORDS[(hash(i * 131 + items.length * 17)) % WORDS.length];
        const w = wordWidth(word, fs);
        items.push({ text: word, x, w, accent: hash(i * 7 + items.length * 53) % 1000 < opts.accentChance * 1000 });
        x += w + fs * 1.4;
      }
      lanes.push({ y: lh * (i + 0.7), dir, sp: 14 + (hash(i * 991) % 18), items, total: x });
    }
  }

  function buildBubbles() {
    const fs = fontPx();
    const n = Math.max(10, Math.floor((W * H) / 52000 / opts.density));
    bubbles = [];
    for (let i = 0; i < n; i++) bubbles.push(spawnBubble(fs, true));
  }
  function spawnBubble(fsBase: number, scatter: boolean): Bubble {
    const fs = Math.round(fsBase * (0.7 + Math.random() * 0.9));
    const text = WORDS[Math.floor(Math.random() * WORDS.length)];
    return {
      text, w: wordWidth(text, fs), fs,
      x: Math.random() * W,
      y: scatter ? Math.random() * H : H + 40,
      vy: (10 + Math.random() * 18),
      phase: Math.random() * Math.PI * 2,
      amp: 12 + Math.random() * 34,
      accent: Math.random() < opts.accentChance,
    };
  }

  function build() {
    inkFg = hexRgb(opts.fg); inkAc = hexRgb(opts.accent);
    if (opts.name === 'sea-of-words') buildSea();
    else if (opts.name === 'word-tide') buildLanes();
    else if (opts.name === 'rising-words') buildBubbles();
  }

  // Dim the horizontal band where the logo sits so it stays legible. A flat
  // plateau (fully dimmed) covers the central ~64% (the logo height), ramping
  // back to full sea by the top/bottom quarters. Returns a 0..1 multiplier.
  function calmAt(y: number): number {
    const d = Math.abs(y - H / 2) / (H / 2);          // 0 at centre, 1 at edges
    const inner = 0.34, outer = 0.5;
    let t = d <= inner ? 0 : d >= outer ? 1 : (d - inner) / (outer - inner);
    t = t * t * (3 - 2 * t);                          // smoothstep ramp
    return 1 - opts.calm * (1 - t);
  }
  // Melt the sea into the page near the top/bottom edges.
  function edgeAt(y: number): number {
    const m = H * 0.14;
    if (y < m) return Math.max(0, y / m);
    if (y > H - m) return Math.max(0, (H - y) / m);
    return 1;
  }

  function drawSea(t: number) {
    const drift = (t * 9 * opts.speed) % seaBlockH;   // tide rising
    ctx.textBaseline = 'middle';
    const passes = Math.ceil(H / seaBlockH) + 1;
    for (let pass = 0; pass < passes; pass++) {
      const base = pass * seaBlockH - drift;
      for (let li = 0; li < seaLines.length; li++) {
        const line = seaLines[li];
        const ly = base + (li + 0.5) * seaLH;
        if (ly < -seaLH || ly > H + seaLH) continue;
        for (const w of line.words) {
          // 2D swell: combine two travelling sines on world x + line y
          const wy = ly
            + Math.sin(w.x * 0.011 + ly * 0.012 - t * 1.1 * opts.speed) * seaLH * 0.42
            + Math.sin(w.x * 0.027 - t * 1.9 * opts.speed) * seaLH * 0.2;
          const a = opts.opacity * edgeAt(wy) * calmAt(wy)
            * (0.5 + 0.5 * Math.sin(w.x * 0.02 + t * opts.speed)); // crest shimmer
          if (a <= 0.01) continue;
          ctx.fillStyle = rgba(w.accent ? inkAc : inkFg, a);
          ctx.fillText(w.text, w.x, wy);
        }
      }
    }
  }

  function drawTide(t: number) {
    const fs = fontPx();
    ctx.font = `${fs}px ${FAMILY}`;
    ctx.textBaseline = 'middle';
    for (const lane of lanes) {
      // seamless marquee: one positive offset, each item wrapped into [0,total)
      // and drawn at e and e-total to cover the seam. total ≥ 1.6×W guarantees
      // the visible band [0,W] is always tiled.
      const off = lane.dir * (t * lane.sp * opts.speed);
      for (const it of lane.items) {
        const e = (((it.x + off) % lane.total) + lane.total) % lane.total;
        for (const x of [e, e - lane.total]) {
          if (x < -it.w - 20 || x > W + 20) continue;
          const y = lane.y + Math.sin(x * 0.006 + lane.y * 0.02 - t * 0.8 * opts.speed) * 16;
          const a = opts.opacity * edgeAt(y) * calmAt(y);
          if (a <= 0.01) continue;
          ctx.fillStyle = rgba(it.accent ? inkAc : inkFg, a);
          ctx.fillText(it.text, x, y);
        }
      }
    }
  }

  function drawRising(t: number, dt: number) {
    ctx.textBaseline = 'middle';
    const fs = fontPx();
    for (const b of bubbles) {
      b.y -= b.vy * opts.speed * dt;
      if (b.y < -30) Object.assign(b, spawnBubble(fs, false));
      const x = b.x + Math.sin(t * 0.6 * opts.speed + b.phase) * b.amp;
      const life = b.y / H;                          // 1 at bottom → 0 at top
      const fade = Math.sin(Math.min(1, Math.max(0, life)) * Math.PI); // in then out
      const a = opts.opacity * fade * calmAt(b.y) * 0.9;
      if (a <= 0.01) continue;
      ctx.font = `${b.fs}px ${FAMILY}`;
      ctx.fillStyle = rgba(b.accent ? inkAc : inkFg, a);
      ctx.fillText(b.text, x, b.y);
    }
  }

  let last = t0;
  function frame() {
    if (!alive) return;
    const now = performance.now();
    const t = (now - t0) / 1000;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    ctx.clearRect(0, 0, W, H);
    if (opts.name === 'sea-of-words') drawSea(t);
    else if (opts.name === 'word-tide') drawTide(t);
    else if (opts.name === 'rising-words') drawRising(t, dt);
    raf = requestAnimationFrame(frame);
  }

  function resize() {
    const parent = canvas.parentElement;
    W = parent ? parent.clientWidth : window.innerWidth;
    H = parent ? parent.clientHeight : window.innerHeight;
    canvas.width = Math.max(1, W * dpr);
    canvas.height = Math.max(1, H * dpr);
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    build();
  }

  const ro = new ResizeObserver(() => resize());
  if (canvas.parentElement) ro.observe(canvas.parentElement);
  resize();
  raf = requestAnimationFrame(frame);

  return {
    update(next: Partial<BgOptions>) {
      const rebuild =
        (next.name && next.name !== opts.name) ||
        (next.density != null && next.density !== opts.density) ||
        (next.accentChance != null && next.accentChance !== opts.accentChance);
      opts = { ...opts, ...next };
      inkFg = hexRgb(opts.fg); inkAc = hexRgb(opts.accent);
      if (rebuild) build();
    },
    stop() { alive = false; cancelAnimationFrame(raf); ro.disconnect(); },
  };
}

// tiny deterministic hash for stable per-word accent picks
function hash(n: number | string): number {
  let h = 2166136261;
  const s = String(n);
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0);
}
