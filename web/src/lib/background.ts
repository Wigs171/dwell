// Animated backgrounds — a faithful port of the Odysseus canvas + WebGL effects.
// Each pattern prepends a fixed, pointer-events-none canvas behind the app and
// animates it with requestAnimationFrame; switching patterns bumps a token so the
// old loop tears itself down. `dots`/`synapse` also use a CSS base layer (app.css).
// Brightness follows --bg-effect-intensity (canvas opacity); size --bg-effect-size;
// hue --bg-effect-color (falls back to --fg). WebGL effects (caustics/silk/topo)
// auto-fall back to a 2D cousin if WebGL2 / shader compile is unavailable.

let token = 0;
let current = 'none';

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
// Effect colour: a pinned --bg-effect-color wins; otherwise every effect drifts through a
// shared palette cycle (one hue at a time, like daydream) — never a washed-out fallback.
function color(): string {
  const ec = css('--bg-effect-color');
  if (ec) return ec;
  const [r, g, b] = cycleRgb();
  return '#' + [r, g, b].map((x) => x.toString(16).padStart(2, '0')).join('');
}
function bgColor(): string { return css('--bg') || '#282c34'; }
function intensity(): number { const v = parseFloat(css('--bg-effect-intensity')); return isNaN(v) ? 1 : v; }
function size(): number { const v = parseFloat(css('--bg-effect-size')); return isNaN(v) ? 1 : v; }
function rgba(hex: string, a: number): string {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
function hexToRgb(hex: string): [number, number, number] {
  const h = (hex || '').replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return Number.isNaN(n) ? [255, 255, 255] : [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
  let h = 0, s = 0;
  if (mx !== mn) {
    const d = mx - mn;
    s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h *= 60;
  }
  return [h, s, l];
}
function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  h /= 360;
  const hue = (p: number, q: number, t: number) => {
    if (t < 0) t += 1; if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  let r = l, g = l, b = l;
  if (s !== 0) {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
    r = hue(p, q, h + 1 / 3); g = hue(p, q, h); b = hue(p, q, h - 1 / 3);
  }
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}
function parseCycle(csv: string): [number, number, number][] {
  return csv.split(',').map((s) => s.trim()).filter(Boolean).map((c) => { const [r, g, b] = hexToRgb(c); return rgbToHsl(r, g, b); });
}
// The current cycle colour. A theme may set --bg-cycle to its own multi-colour palette
// (e.g. daydream's brand colours); otherwise we derive from the theme's --accent, sweeping
// its hue ±55° so the effect stays in THAT theme's colour family. Interpolated in HSL so
// it never grays. A pinned --bg-effect-color overrides cycling (handled in color()).
function cycleRgb(): [number, number, number] {
  const stops = parseCycle(css('--bg-cycle'));
  if (stops.length >= 2) {                          // theme-supplied palette → cycle through it
    const n = stops.length, phase = performance.now() / 6000;
    const i0 = Math.floor(phase) % n, i1 = (i0 + 1) % n;
    let f = phase - Math.floor(phase); f = f * f * (3 - 2 * f);
    const a = stops[i0], b = stops[i1];
    let dh = b[0] - a[0]; if (dh > 180) dh -= 360; else if (dh < -180) dh += 360;
    return hslToRgb((a[0] + dh * f + 360) % 360, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f);
  }
  // derive from the theme accent: sweep its hue ±55°, keep its saturation/lightness
  const [h0, s, l] = rgbToHsl(...hexToRgb(css('--accent') || '#c6613f'));
  const p = (performance.now() / 12000) % 1;
  const tri = p < 0.5 ? p * 4 - 1 : 3 - p * 4;      // -1 → +1 → -1 triangle sweep
  return hslToRgb((h0 + 55 * tri + 360) % 360, Math.max(s, 0.35), l);
}

export function setEffectColor(c: string) { document.documentElement.style.setProperty('--bg-effect-color', c || ''); }
// A theme's own colour-cycle palette (comma-separated). Empty → effects derive the cycle
// from the theme's accent instead.
export function setCyclePalette(csv: string) { document.documentElement.style.setProperty('--bg-cycle', csv || ''); }
export function setEffectIntensity(v: number) {
  const n = (v == null || isNaN(v)) ? 1 : Math.max(0, Math.min(1, v));
  document.documentElement.style.setProperty('--bg-effect-intensity', String(n));
}
export function setEffectSize(v: number) {
  const n = (v == null || isNaN(v)) ? 1 : Math.max(0.3, Math.min(2.5, v));
  document.documentElement.style.setProperty('--bg-effect-size', String(n));
}
export function applyFrosted(on: boolean) { document.body.classList.toggle('theme-frosted', !!on); }

const ALL = ['dots', 'synapse', 'rain', 'constellations', 'perlin-flow', 'petals', 'sparkles',
  'embers', 'aurora', 'glyph-rain', 'retro-grid', 'fireflies', 'bubbles', 'ripples', 'snow',
  'daydream', 'caustics', 'silk', 'topo'];
const BG_CLASSES = ALL.map((p) => 'bg-pattern-' + p);

export function applyBgPattern(pattern: string) {
  current = pattern || 'none';
  token++;                                          // stop any running loop
  document.querySelectorAll('canvas[data-dwell-bg]').forEach((c) => c.remove());
  document.body.classList.remove(...BG_CLASSES);
  if (current !== 'none') document.body.classList.add('bg-pattern-' + current);
  const fn = CANVAS[current];
  if (fn) fn(token);
}

// Shared 2D canvas setup. `afterResize(dim)` runs on every resize (incl. first) —
// use it for size-dependent (re)seeding; it receives the live dim to avoid TDZ.
function makeCanvas(myToken: number, afterResize?: (d: { W: number; H: number }) => void) {
  const canvas = document.createElement('canvas');
  canvas.dataset.dwellBg = '1';
  canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d')!;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const dim = { W: 0, H: 0 };
  const resize = () => {
    dim.W = window.innerWidth; dim.H = window.innerHeight;
    canvas.width = dim.W * dpr; canvas.height = dim.H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    afterResize?.(dim);
  };
  resize();
  const onResize = () => resize();
  window.addEventListener('resize', onResize);
  const alive = () => myToken === token && document.body.contains(canvas);
  const stop = () => { window.removeEventListener('resize', onResize); canvas.remove(); };
  return { ctx, dim, alive, stop };
}

const noise2 = (x: number, y: number) => { const n = Math.sin(x * 12.9898 + y * 78.233) * 43758.5453; return n - Math.floor(n); };
function smoothNoise(x: number, y: number) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const a = noise2(ix, iy), b = noise2(ix + 1, iy), cc = noise2(ix, iy + 1), d = noise2(ix + 1, iy + 1);
  const ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy);
  return a + (b - a) * ux + (cc - a) * uy + (a - b - cc + d) * ux * uy;
}

type Init = (t: number) => void;

const CANVAS: Record<string, Init> = {
  // Daydream — a mouse-trail "fluid" glow recreated from daydream.live: drifting orbs +
  // a cursor trail of soft additive light, heavily CSS-blurred (its canvas is `blur-2xl`).
  // The whole field is ONE hue at a time, slowly cycling through the brand palette
  // (coral→teal→gold→orange) — so it's never the washed-out white of all-colors-at-once.
  // A SET effect color pins it to that single hue (no cycle). intensity/size apply.
  daydream(myToken) {
    const { ctx, dim, alive, stop } = makeCanvas(myToken);
    ctx.canvas.style.filter = 'blur(36px)';
    const orbs = Array.from({ length: 4 }, () => ({
      x: Math.random() * dim.W, y: Math.random() * dim.H,
      vx: (Math.random() - 0.5) * 0.35, vy: (Math.random() - 0.5) * 0.35,
      r: 180 + Math.random() * 140,
    }));
    const trail: { x: number; y: number; life: number }[] = [];
    let lastX = -1, lastY = -1;
    const onMove = (e: MouseEvent) => {
      const dx = e.clientX - lastX, dy = e.clientY - lastY;
      if (lastX >= 0 && dx * dx + dy * dy < 9) return;   // throttle by distance
      lastX = e.clientX; lastY = e.clientY;
      trail.push({ x: e.clientX, y: e.clientY, life: 1 });
      if (trail.length > 70) trail.shift();
    };
    window.addEventListener('mousemove', onMove, { passive: true });
    const glow = (x: number, y: number, r: number, c: [number, number, number], a: number) => {
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},${a})`);
      g.addColorStop(1, `rgba(${c[0]},${c[1]},${c[2]},0)`);
      ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2);
    };
    (function draw() {
      if (!alive()) { window.removeEventListener('mousemove', onMove); ctx.canvas.style.filter = ''; return stop(); }
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const inten = intensity(), sz = size();
      // one hue at a time: a pinned effect-colour, else the shared palette cycle.
      const ec = css('--bg-effect-color');
      const cur: [number, number, number] = ec ? hexToRgb(ec) : cycleRgb();
      ctx.globalCompositeOperation = 'lighter';         // additive light builds up like the fluid
      for (const o of orbs) {
        o.x += o.vx; o.y += o.vy;
        if (o.x < -o.r) o.x = dim.W + o.r; else if (o.x > dim.W + o.r) o.x = -o.r;
        if (o.y < -o.r) o.y = dim.H + o.r; else if (o.y > dim.H + o.r) o.y = -o.r;
        glow(o.x, o.y, o.r * sz, cur, 0.14 * inten);
      }
      for (let i = trail.length - 1; i >= 0; i--) {
        const t = trail[i]; t.life -= 0.012;
        if (t.life <= 0) { trail.splice(i, 1); continue; }
        glow(t.x, t.y, (90 + 60 * (1 - t.life)) * sz, cur, t.life * t.life * 0.55 * inten);
      }
      ctx.globalCompositeOperation = 'source-over';
    })();
  },

  synapse(myToken) {
    const { ctx, dim, alive, stop } = makeCanvas(myToken);
    const GRID = 24, MAX = 20, SMIN = 2, SMAX = 22, TRAIL = 12;
    const pulses: { x: number; y: number; dx: number; dy: number }[] = [];
    const spawn = () => {
      const sp = SMIN + Math.random() * (SMAX - SMIN);
      if (Math.random() > 0.5) pulses.push({ x: -TRAIL, y: Math.floor(Math.random() * (dim.H / GRID + 1)) * GRID, dx: sp, dy: 0 });
      else pulses.push({ x: Math.floor(Math.random() * (dim.W / GRID + 1)) * GRID, y: -TRAIL, dx: 0, dy: sp });
    };
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color();
      if (pulses.length < MAX && Math.random() < 0.12) spawn();
      for (let i = pulses.length - 1; i >= 0; i--) {
        const p = pulses[i]; p.x += p.dx; p.y += p.dy;
        if (p.x > dim.W + TRAIL || p.y > dim.H + TRAIL) { pulses.splice(i, 1); continue; }
        const tx = p.x - (p.dx > 0 ? TRAIL : 0), ty = p.y - (p.dy > 0 ? TRAIL : 0);
        const g = ctx.createLinearGradient(tx, ty, p.x, p.y);
        g.addColorStop(0, 'transparent'); g.addColorStop(1, c);
        ctx.strokeStyle = g; ctx.globalAlpha = 0.35; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(p.x, p.y); ctx.stroke();
        ctx.globalAlpha = 0.55; ctx.fillStyle = c;
        ctx.beginPath(); ctx.arc(p.x, p.y, 1.2, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
    })();
  },

  rain(myToken) {
    const { ctx, dim, alive, stop } = makeCanvas(myToken);
    const drops: { x: number; y: number; len: number; speed: number; alpha: number }[] = [];
    const MAX = 130;
    const spawn = () => { const len = 20 + Math.random() * 40; drops.push({ x: Math.random() * dim.W, y: -len, len, speed: 4 + Math.random() * 8, alpha: 0.32 + Math.random() * 0.28 }); };
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), inten = intensity(), sz = size(), speedMult = 0.35 + inten * 0.65;
      if (drops.length < MAX * inten && Math.random() < 0.6 * inten) spawn();
      for (let i = drops.length - 1; i >= 0; i--) {
        const d = drops[i]; d.y += d.speed * speedMult;
        if (d.y > dim.H + d.len * sz) { drops.splice(i, 1); continue; }
        const L = d.len * sz;
        const g = ctx.createLinearGradient(d.x, d.y - L, d.x, d.y);
        g.addColorStop(0, 'transparent'); g.addColorStop(1, c);
        ctx.strokeStyle = g; ctx.globalAlpha = d.alpha; ctx.lineWidth = 1.3 * Math.min(2, Math.max(0.6, sz));
        ctx.beginPath(); ctx.moveTo(d.x, d.y - L); ctx.lineTo(d.x, d.y); ctx.stroke();
      }
      ctx.globalAlpha = 1;
    })();
  },

  constellations(myToken) {
    let stars: { x: number; y: number; vx: number; vy: number; r: number; phase: number }[] = [];
    const seed = (d: { W: number; H: number }) => {
      stars = Array.from({ length: 50 }, () => ({ x: Math.random() * d.W, y: Math.random() * d.H, vx: (Math.random() - 0.5) * 0.15, vy: (Math.random() - 0.5) * 0.15, r: 0.8 + Math.random() * 0.8, phase: Math.random() * Math.PI * 2 }));
    };
    const { ctx, dim, alive, stop } = makeCanvas(myToken, seed);
    const DIST = 120; let t = 0;
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      t += 0.01; ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color();
      for (const s of stars) { s.x += s.vx; s.y += s.vy; if (s.x < 0) s.x = dim.W; if (s.x > dim.W) s.x = 0; if (s.y < 0) s.y = dim.H; if (s.y > dim.H) s.y = 0; }
      ctx.strokeStyle = c; ctx.lineWidth = 0.5;
      for (let i = 0; i < stars.length; i++) for (let j = i + 1; j < stars.length; j++) {
        const dx = stars[i].x - stars[j].x, dy = stars[i].y - stars[j].y, dd = Math.hypot(dx, dy);
        if (dd < DIST) { ctx.globalAlpha = (1 - dd / DIST) * 0.15; ctx.beginPath(); ctx.moveTo(stars[i].x, stars[i].y); ctx.lineTo(stars[j].x, stars[j].y); ctx.stroke(); }
      }
      ctx.fillStyle = c;
      for (const s of stars) { const tw = 0.5 + 0.5 * Math.sin(t * 2 + s.phase); ctx.globalAlpha = 0.15 + tw * 0.25; ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.fill(); }
      ctx.globalAlpha = 1;
    })();
  },

  'perlin-flow'(myToken) {
    let parts: { x: number; y: number; life: number }[] = [];
    const seed = (d: { W: number; H: number }) => { if (!parts.length) parts = Array.from({ length: 200 }, () => ({ x: Math.random() * d.W, y: Math.random() * d.H, life: Math.random() })); };
    const { ctx, dim, alive, stop } = makeCanvas(myToken, seed);
    let t = 0;
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.fillStyle = rgba(bgColor(), 0.02); ctx.fillRect(0, 0, dim.W, dim.H);
      const c = color();
      for (const p of parts) {
        const angle = smoothNoise(p.x * 0.004 + t * 0.0008, p.y * 0.004 + 100) * Math.PI * 6;
        const sp = 1 + smoothNoise(p.x * 0.003, p.y * 0.003 + 50) * 1.5;
        p.x += Math.cos(angle) * sp; p.y += Math.sin(angle) * sp; p.life -= 0.001;
        if (p.life <= 0 || p.x < 0 || p.x > dim.W || p.y < 0 || p.y > dim.H) { p.x = Math.random() * dim.W; p.y = Math.random() * dim.H; p.life = 1; }
        ctx.beginPath(); ctx.arc(p.x, p.y, 1, 0, Math.PI * 2); ctx.fillStyle = c; ctx.globalAlpha = p.life * 0.15; ctx.fill();
      }
      ctx.globalAlpha = 1; t++;
    })();
  },

  petals(myToken) {
    let petals: any[] = [];
    const make = (d: { W: number; H: number }) => ({ x: Math.random() * d.W, y: -10 - Math.random() * 40, size: 3 + Math.random() * 5, rot: Math.random() * Math.PI * 2, vr: (Math.random() - 0.5) * 0.03, vy: 0.3 + Math.random() * 0.6, drift: Math.random() * Math.PI * 2, driftSpeed: 0.008 + Math.random() * 0.012, wobble: 0.3 + Math.random() * 0.8 });
    const seed = (d: { W: number; H: number }) => { if (!petals.length) petals = Array.from({ length: 30 }, () => { const p = make(d); p.y = Math.random() * d.H; return p; }); };
    const { ctx, dim, alive, stop } = makeCanvas(myToken, seed);
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), sz = size();
      for (const p of petals) {
        p.y += p.vy; p.rot += p.vr; p.drift += p.driftSpeed; p.x += Math.sin(p.drift) * p.wobble;
        if (p.y > dim.H + 15) Object.assign(p, make(dim));
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillStyle = c;
        ctx.globalAlpha = 0.2; ctx.beginPath(); ctx.ellipse(-p.size * 0.2 * sz, 0, p.size * 0.6 * sz, p.size * 0.3 * sz, 0.3, 0, Math.PI * 2); ctx.fill();
        ctx.globalAlpha = 0.15; ctx.beginPath(); ctx.ellipse(p.size * 0.2 * sz, 0, p.size * 0.6 * sz, p.size * 0.3 * sz, -0.3, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
      }
      ctx.globalAlpha = 1;
    })();
  },

  sparkles(myToken) {
    let sparks: any[] = [];
    const make = (d: { W: number; H: number }) => ({ x: Math.random() * d.W, y: Math.random() * d.H, size: 2 + Math.random() * 5, phase: Math.random() * Math.PI * 2, speed: 0.015 + Math.random() * 0.03, life: 0.5 + Math.random() * 0.5 });
    const seed = (d: { W: number; H: number }) => { if (!sparks.length) sparks = Array.from({ length: 35 }, () => make(d)); };
    const { ctx, dim, alive, stop } = makeCanvas(myToken, seed);
    const star = (x: number, y: number, r: number, c: string, a: number) => {
      ctx.save(); ctx.translate(x, y); ctx.fillStyle = c; ctx.globalAlpha = a; ctx.beginPath();
      ctx.moveTo(0, -r); ctx.quadraticCurveTo(r * 0.15, -r * 0.15, r, 0); ctx.quadraticCurveTo(r * 0.15, r * 0.15, 0, r);
      ctx.quadraticCurveTo(-r * 0.15, r * 0.15, -r, 0); ctx.quadraticCurveTo(-r * 0.15, -r * 0.15, 0, -r); ctx.fill(); ctx.restore();
    };
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), sz = size();
      for (const s of sparks) {
        s.phase += s.speed; const tw = Math.sin(s.phase); const a = Math.max(0, tw) * 0.25 * s.life; const scale = 0.5 + Math.max(0, tw) * 0.5;
        if (a > 0.01) star(s.x, s.y, s.size * scale * sz, c, a);
        if (s.phase > Math.PI * 6) Object.assign(s, make(dim));
      }
      ctx.globalAlpha = 1;
    })();
  },

  embers(myToken) {
    let embers: any[] = [];
    const make = (d: { W: number; H: number }) => ({ x: Math.random() * d.W, y: d.H + Math.random() * 40, vx: (Math.random() - 0.5) * 0.3, vy: -0.3 - Math.random() * 0.8, r: 0.3 + Math.random() * 0.6, life: 0, maxLife: 220 + Math.random() * 220, wobble: Math.random() * Math.PI * 2, spark: false });
    const seed = (d: { W: number; H: number }) => { if (!embers.length) embers = Array.from({ length: 60 }, () => { const e = make(d); e.y = Math.random() * d.H; e.life = Math.random() * e.maxLife; return e; }); };
    const { ctx, dim, alive, stop } = makeCanvas(myToken, seed);
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.globalCompositeOperation = 'destination-out'; ctx.fillStyle = 'rgba(0,0,0,0.18)'; ctx.fillRect(0, 0, dim.W, dim.H);
      ctx.globalCompositeOperation = 'lighter';
      const c = color(), sz = size();
      for (let i = embers.length - 1; i >= 0; i--) {
        const e = embers[i]; e.wobble += 0.03; e.x += e.vx + Math.sin(e.wobble) * 0.5; e.y += e.vy; e.life++;
        if (e.life > e.maxLife || e.y < -20) { embers.splice(i, 1); if (embers.length < 70) embers.push(make(dim)); continue; }
        if (!e.spark && Math.random() < 0.003) e.spark = true;
        const ratio = e.life / e.maxLife, fade = Math.min(1, Math.min(ratio * 4, (1 - ratio) * 3));
        const r = e.r * (e.spark ? 2.4 : 1) * sz, a = (e.spark ? 0.9 : 0.55) * fade;
        const g = ctx.createRadialGradient(e.x, e.y, 0, e.x, e.y, r * 4);
        g.addColorStop(0, rgba(c, a)); g.addColorStop(0.4, rgba(c, a * 0.3)); g.addColorStop(1, rgba(c, 0));
        ctx.fillStyle = g; ctx.fillRect(e.x - r * 4, e.y - r * 4, r * 8, r * 8);
        ctx.fillStyle = rgba('#ffffff', a * 0.6); ctx.beginPath(); ctx.arc(e.x, e.y, r * 0.5, 0, Math.PI * 2); ctx.fill();
        e.spark = false;
      }
      if (Math.random() < 0.015) { const bx = Math.random() * dim.W; for (let i = 0; i < 5; i++) { const e = make(dim); e.x = bx + (Math.random() - 0.5) * 40; e.y = dim.H - 10; e.vy *= 1.5; embers.push(e); } }
      ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1;
    })();
  },

  aurora(myToken) {
    const { ctx, dim, alive, stop } = makeCanvas(myToken);
    const BANDS = [
      { base: 0.22, amp: 0.09, freq: 0.0016, speed: 0.00022, phase: 0.0, h: 0.34 },
      { base: 0.42, amp: 0.12, freq: 0.0011, speed: -0.00015, phase: 2.1, h: 0.42 },
      { base: 0.60, amp: 0.07, freq: 0.0021, speed: 0.00030, phase: 4.4, h: 0.28 },
    ];
    (function draw(now?: number) {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      const t = now || 0; ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), sz = size();
      for (const B of BANDS) {
        const baseY = dim.H * B.base, amp = dim.H * B.amp * sz, bandH = dim.H * B.h * sz;
        const breathe = 0.65 + 0.35 * Math.sin(t * 0.00018 + B.phase * 2), alpha = 0.10 * breathe;
        const g = ctx.createLinearGradient(0, baseY - bandH / 2, 0, baseY + bandH / 2);
        g.addColorStop(0, rgba(c, 0)); g.addColorStop(0.45, rgba(c, alpha)); g.addColorStop(0.6, rgba(c, alpha * 0.55)); g.addColorStop(1, rgba(c, 0));
        ctx.fillStyle = g; ctx.beginPath();
        const STEP = 24;
        for (let x = 0; x <= dim.W + STEP; x += STEP) {
          const y = baseY - bandH / 2 + Math.sin(x * B.freq + t * B.speed + B.phase) * amp + Math.sin(x * B.freq * 2.7 + t * B.speed * 1.6) * amp * 0.3;
          if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        for (let x = dim.W + STEP; x >= 0; x -= STEP) ctx.lineTo(x, baseY + bandH / 2 + Math.sin(x * B.freq + t * B.speed + B.phase + 0.9) * amp * 0.7);
        ctx.closePath(); ctx.fill();
      }
    })();
  },

  'glyph-rain'(myToken) {
    const GLYPHS = 'アイウエオカキクケコサシスセソタチツテトナニヌネノ0123456789ABCDEF<>+-*/=#$';
    const rnd = () => GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
    let cols: any[] = [];
    const build = (d: { W: number; H: number }) => {
      const cell = 14 * size(), n = Math.ceil(d.W / cell); cols = [];
      for (let i = 0; i < n; i++) {
        const col: any = { active: false, head: 0, speed: 0, len: 0, chars: [] };
        if (Math.random() < 0.33) { col.active = true; col.head = Math.random() * (d.H / cell); col.speed = 0.18 + Math.random() * 0.32; col.len = 7 + Math.floor(Math.random() * 11); col.chars = Array.from({ length: col.len }, rnd); }
        cols.push(col);
      }
    };
    const { ctx, dim, alive, stop } = makeCanvas(myToken, build);
    let lastSize = size();
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), inten = intensity(), sz = size();
      if (Math.abs(sz - lastSize) > 0.01) { lastSize = sz; build(dim); }
      const cell = 14 * sz;
      ctx.font = `${Math.max(8, cell * 0.82)}px monospace`; ctx.textAlign = 'center';
      for (let i = 0; i < cols.length; i++) {
        const col = cols[i];
        if (!col.active) { if (Math.random() < 0.012 * inten) { col.active = true; col.head = -Math.random() * 20; col.speed = 0.18 + Math.random() * 0.32; col.len = 7 + Math.floor(Math.random() * 11); col.chars = Array.from({ length: col.len }, rnd); } continue; }
        col.head += col.speed;
        if (Math.random() < 0.06) col.chars[Math.floor(Math.random() * col.len)] = rnd();
        const x = (i + 0.5) * cell;
        for (let k = 0; k < col.len; k++) {
          const cy = (col.head - k) * cell;
          if (cy < -cell || cy > dim.H + cell) continue;
          ctx.globalAlpha = k === 0 ? 0.85 : (1 - k / col.len) * 0.38; ctx.fillStyle = c; ctx.fillText(col.chars[k], x, cy);
        }
        if ((col.head - col.len) * cell > dim.H) col.active = false;
      }
      ctx.globalAlpha = 1;
    })();
  },

  'retro-grid'(myToken) {
    const { ctx, dim, alive, stop } = makeCanvas(myToken);
    let t = 0; const ROWS = 14;
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), inten = intensity(), sz = size(), horizon = dim.H * 0.58;
      t += 0.004 * (0.4 + inten * 0.6);
      const sunR = 90 * sz, sunX = dim.W / 2, sunY = horizon - sunR * 0.25;
      const sg = ctx.createLinearGradient(0, sunY - sunR, 0, sunY + sunR);
      sg.addColorStop(0, rgba(c, 0.30)); sg.addColorStop(1, rgba(c, 0.06));
      ctx.fillStyle = sg; ctx.beginPath(); ctx.arc(sunX, sunY, sunR, 0, Math.PI * 2); ctx.fill();
      for (let i = 0; i < 5; i++) { const gy = sunY + sunR * (0.05 + i * 0.2); ctx.clearRect(sunX - sunR - 2, gy, sunR * 2 + 4, (1.5 + i * 1.6) * sz); }
      ctx.clearRect(0, horizon, dim.W, dim.H - horizon);
      ctx.fillStyle = rgba(c, 0.5); ctx.fillRect(0, horizon - 0.7, dim.W, 1.4);
      const spacing = 70 * sz, n = Math.ceil((dim.W / 2) / spacing) + 2; ctx.lineWidth = 1;
      for (let k = -n; k <= n; k++) {
        const a = Math.max(0, 0.30 - Math.abs(k) * 0.018); if (a < 0.01) continue;
        ctx.strokeStyle = rgba(c, a); ctx.beginPath(); ctx.moveTo(dim.W / 2 + k * spacing * 0.12, horizon); ctx.lineTo(dim.W / 2 + k * spacing * 2.6, dim.H); ctx.stroke();
      }
      for (let i = 0; i < ROWS; i++) {
        const p = ((i + (t % 1)) / ROWS), y = horizon + (dim.H - horizon) * p * p;
        ctx.strokeStyle = rgba(c, 0.06 + 0.32 * p); ctx.lineWidth = 1 + p * 1.2; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(dim.W, y); ctx.stroke();
      }
    })();
  },

  fireflies(myToken) {
    const MAX = 26; let flies: any[] = [];
    const make = (d: { W: number; H: number }) => ({ x: Math.random() * d.W, y: Math.random() * d.H, vx: (Math.random() - 0.5) * 0.4, vy: (Math.random() - 0.5) * 0.4, phase: Math.random() * Math.PI * 2, pulse: 0.008 + Math.random() * 0.018 });
    const seed = (d: { W: number; H: number }) => { if (!flies.length) flies = Array.from({ length: MAX }, () => make(d)); };
    const { ctx, dim, alive, stop } = makeCanvas(myToken, seed);
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), inten = intensity(), sz = size(), visible = Math.max(2, Math.round(MAX * inten));
      for (let i = 0; i < visible; i++) {
        const f = flies[i];
        f.vx += (Math.random() - 0.5) * 0.03; f.vy += (Math.random() - 0.5) * 0.03;
        const sp = Math.hypot(f.vx, f.vy); if (sp > 0.45) { f.vx *= 0.45 / sp; f.vy *= 0.45 / sp; }
        f.x += f.vx; f.y += f.vy;
        if (f.x < -20) f.x = dim.W + 20; if (f.x > dim.W + 20) f.x = -20; if (f.y < -20) f.y = dim.H + 20; if (f.y > dim.H + 20) f.y = -20;
        f.phase += f.pulse;
        const glow = Math.pow(Math.max(0, Math.sin(f.phase)), 1.8); if (glow < 0.02) continue;
        const r = 7 * sz, a = glow * 0.55;
        const g = ctx.createRadialGradient(f.x, f.y, 0, f.x, f.y, r * 3);
        g.addColorStop(0, rgba(c, a)); g.addColorStop(0.35, rgba(c, a * 0.25)); g.addColorStop(1, rgba(c, 0));
        ctx.fillStyle = g; ctx.fillRect(f.x - r * 3, f.y - r * 3, r * 6, r * 6);
        ctx.fillStyle = rgba(c, Math.min(0.9, a * 1.6)); ctx.beginPath(); ctx.arc(f.x, f.y, Math.max(0.8, 1.3 * sz) * (0.6 + glow * 0.4), 0, Math.PI * 2); ctx.fill();
      }
    })();
  },

  bubbles(myToken) {
    const MAX = 34; let bubbles: any[] = [];
    const make = (d: { W: number; H: number }) => { const r = 3 + Math.random() * 11; return { x: Math.random() * d.W, y: d.H + r + Math.random() * 60, r, vy: 0.35 + Math.random() * 0.85 + r * 0.015, wob: Math.random() * Math.PI * 2, wobSpeed: 0.01 + Math.random() * 0.03, wobAmp: 0.3 + Math.random() * 0.9, alpha: 0.16 + Math.random() * 0.18 }; };
    const seed = (d: { W: number; H: number }) => { if (!bubbles.length) for (let i = 0; i < MAX * 0.6; i++) { const b = make(d); b.y = Math.random() * d.H; bubbles.push(b); } };
    const { ctx, dim, alive, stop } = makeCanvas(myToken, seed);
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), inten = intensity(), sz = size();
      if (bubbles.length < MAX * inten && Math.random() < 0.10 * inten) bubbles.push(make(dim));
      for (let i = bubbles.length - 1; i >= 0; i--) {
        const b = bubbles[i]; b.wob += b.wobSpeed; b.x += Math.sin(b.wob) * b.wobAmp; b.y -= b.vy * (0.5 + inten * 0.5);
        const r = b.r * sz; if (b.y < -r * 2) { bubbles.splice(i, 1); continue; }
        const fade = b.y < dim.H * 0.2 ? Math.max(0, b.y / (dim.H * 0.2)) : 1, a = b.alpha * fade;
        if (a < 0.01) { bubbles.splice(i, 1); continue; }
        ctx.strokeStyle = rgba(c, a); ctx.lineWidth = Math.max(0.8, 1.1 * sz); ctx.beginPath(); ctx.arc(b.x, b.y, r, 0, Math.PI * 2); ctx.stroke();
        ctx.strokeStyle = rgba(c, a * 1.8); ctx.lineWidth = Math.max(0.7, 0.9 * sz); ctx.beginPath(); ctx.arc(b.x, b.y, r * 0.62, -2.4, -1.3); ctx.stroke();
      }
    })();
  },

  ripples(myToken) {
    const { ctx, dim, alive, stop } = makeCanvas(myToken);
    const ripples: any[] = [];
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), inten = intensity(), sz = size(), maxR = 130 * sz;
      if (ripples.length < 14 && Math.random() < 0.022 * inten) ripples.push({ x: Math.random() * dim.W, y: Math.random() * dim.H, age: 0 });
      ctx.lineWidth = 1.2;
      for (let i = ripples.length - 1; i >= 0; i--) {
        const rp = ripples[i]; rp.age += 0.9 * (0.5 + inten * 0.5); let live = false;
        for (let k = 0; k < 3; k++) {
          const r = rp.age - k * 16 * sz; if (r <= 0 || r > maxR) continue; live = true;
          const a = (1 - r / maxR) * 0.28 * (1 - k * 0.25);
          ctx.strokeStyle = rgba(c, a); ctx.beginPath(); ctx.arc(rp.x, rp.y, r, 0, Math.PI * 2); ctx.stroke();
        }
        if (!live && rp.age > maxR) ripples.splice(i, 1);
      }
    })();
  },

  snow(myToken) {
    const MAX = 95; let flakes: any[] = [];
    const make = (d: { W: number; H: number }, top: boolean) => { const r = 0.8 + Math.random() * 2.4; return { x: Math.random() * d.W, y: top ? -5 : Math.random() * d.H, r, vy: 0.25 + r * 0.22 + Math.random() * 0.3, sway: Math.random() * Math.PI * 2, swaySpeed: 0.006 + Math.random() * 0.014, swayAmp: 0.25 + Math.random() * 0.6, a: 0.18 + Math.random() * 0.28, soft: Math.random() < 0.25 }; };
    const seed = (d: { W: number; H: number }) => { if (!flakes.length) flakes = Array.from({ length: MAX }, () => make(d, false)); };
    const { ctx, dim, alive, stop } = makeCanvas(myToken, seed);
    let t = 0;
    (function draw() {
      if (!alive()) return stop();
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, dim.W, dim.H);
      const c = color(), inten = intensity(), sz = size(); t += 1;
      const wind = Math.sin(t * 0.0004) * 0.35, visible = Math.max(8, Math.round(MAX * inten));
      for (let i = 0; i < visible; i++) {
        const f = flakes[i]; f.sway += f.swaySpeed; f.x += Math.sin(f.sway) * f.swayAmp + wind; f.y += f.vy * (0.45 + inten * 0.55);
        if (f.y > dim.H + 6) Object.assign(f, make(dim, true));
        if (f.x > dim.W + 6) f.x = -6; if (f.x < -6) f.x = dim.W + 6;
        const r = f.r * sz;
        if (f.soft) { const g = ctx.createRadialGradient(f.x, f.y, 0, f.x, f.y, r * 2.6); g.addColorStop(0, rgba(c, f.a * 0.8)); g.addColorStop(1, rgba(c, 0)); ctx.fillStyle = g; ctx.fillRect(f.x - r * 2.6, f.y - r * 2.6, r * 5.2, r * 5.2); }
        else { ctx.fillStyle = rgba(c, f.a); ctx.beginPath(); ctx.arc(f.x, f.y, r, 0, Math.PI * 2); ctx.fill(); }
      }
    })();
  },

  // GPU shader effects (WebGL2, auto-fallback to a 2D cousin)
  caustics: (t) => initShader('caustics', t),
  silk: (t) => initShader('silk', t),
  topo: (t) => initShader('topo', t),
};

// ── WebGL2 shader runner ───────────────────────────────────────────────────
function glContext(canvas: HTMLCanvasElement): WebGL2RenderingContext | null {
  try {
    return canvas.getContext('webgl2', { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false, powerPreference: 'low-power' }) as WebGL2RenderingContext | null;
  } catch { return null; }
}
function glProgram(gl: WebGL2RenderingContext, vs: string, fs: string): WebGLProgram {
  const sh = (type: number, src: string) => {
    const s = gl.createShader(type)!; gl.shaderSource(s, src.trim()); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { const e = gl.getShaderInfoLog(s); gl.deleteShader(s); throw new Error('shader compile: ' + e); }
    return s;
  };
  const p = gl.createProgram()!;
  gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error('program link: ' + gl.getProgramInfoLog(p));
  return p;
}
const GL_VS = `#version 300 es
in vec2 p; out vec2 v_uv;
void main(){ v_uv = p*0.5+0.5; gl_Position = vec4(p,0.,1.); }`;
function fxColorVec(): [number, number, number] {
  const hex = color().replace('#', '');
  const h = hex.length === 3 ? hex.split('').map((c) => c + c).join('') : hex;
  const n = parseInt(h, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}
function glFallback(_id: string, fallback: string) { applyBgPattern(fallback); }

function initShader(id: string, myToken: number) {
  const cfg = SHADERS[id];
  if (!cfg) return;
  const canvas = document.createElement('canvas');
  canvas.dataset.dwellBg = '1';
  canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:0;';
  document.body.prepend(canvas);
  const gl = glContext(canvas);
  if (!gl) { canvas.remove(); glFallback(id, cfg.fallback); return; }
  let prog: WebGLProgram;
  try { prog = glProgram(gl, GL_VS, cfg.frag); }
  catch { canvas.remove(); glFallback(id, cfg.fallback); return; }
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.useProgram(prog);
  const loc = gl.getAttribLocation(prog, 'p');
  gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const U: Record<string, WebGLUniformLocation | null> = {};
  for (const n of ['u_time', 'u_resolution', 'u_color', 'u_size', 'u_intensity']) U[n] = gl.getUniformLocation(prog, n);
  const scale = cfg.scale || 0.5;
  let W = 2, H = 2;
  const resize = () => { W = Math.max(2, Math.round(window.innerWidth * scale)); H = Math.max(2, Math.round(window.innerHeight * scale)); canvas.width = W; canvas.height = H; gl.viewport(0, 0, W, H); };
  resize();
  const onResize = () => resize();
  window.addEventListener('resize', onResize);
  const t0 = performance.now();
  (function frame() {
    if (myToken !== token || !document.body.contains(canvas)) {
      window.removeEventListener('resize', onResize);
      const ext = gl.getExtension('WEBGL_lose_context'); if (ext) ext.loseContext();
      canvas.remove(); return;
    }
    requestAnimationFrame(frame);
    if (gl.isContextLost()) return;
    const rgb = fxColorVec();
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform1f(U.u_time, (performance.now() - t0) / 1000);
    gl.uniform2f(U.u_resolution, W, H);
    gl.uniform3f(U.u_color, rgb[0], rgb[1], rgb[2]);
    gl.uniform1f(U.u_size, size());
    gl.uniform1f(U.u_intensity, intensity());
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  })();
}

interface Shader { scale: number; fallback: string; frag: string; }
const SHADERS: Record<string, Shader> = {
  caustics: {
    scale: 0.5, fallback: 'ripples', frag: `
#version 300 es
precision mediump float;
in vec2 v_uv;
uniform float u_time; uniform vec2 u_resolution; uniform vec3 u_color; uniform float u_size; uniform float u_intensity;
out vec4 outColor;
highp vec2 hash2(highp vec2 p){ p=mod(p,64.0); highp vec3 q=fract(vec3(p.x,p.y,p.x+p.y)*vec3(0.0973,0.1031,0.0719)); q+=dot(q,q.zxy+7.13); return fract(vec2((q.x+q.y)*q.z,(q.x+q.z)*q.y)); }
highp float voroEdge(highp vec2 p, highp float jt){ highp vec2 n=floor(p); highp vec2 f=p-n; highp float d1=8.0; highp float d2=8.0;
  for(int j=-1;j<=1;j++){ for(int i=-1;i<=1;i++){ highp vec2 g=vec2(float(i),float(j)); highp vec2 h=hash2(n+g); highp vec2 r=g+0.5+0.38*sin(jt+6.2831*h)-f; highp float d=dot(r,r); d2=min(d2,max(d,d1)); d1=min(d1,d); } }
  return sqrt(d2)-sqrt(d1); }
void main(){ float aspect=u_resolution.x/u_resolution.y; highp vec2 p=(v_uv-0.5)*vec2(aspect,1.0);
  float drive=clamp(u_intensity,0.0,1.0); highp float cells=5.5/clamp(u_size,0.2,3.0); highp float t=u_time;
  highp float jig=t*mix(0.25,0.55,drive); highp float dsp=mix(0.6,1.4,drive);
  highp vec2 pa=p*cells+vec2(0.045,0.018)*(dsp*t); highp vec2 pb=p*(cells*1.5)+vec2(-0.052,-0.023)*(dsp*t);
  float eA=voroEdge(pa,jig+1.7); float eB=voroEdge(pb,-jig);
  float w0=mix(0.40,0.62,drive); float k=mix(3.4,1.8,drive);
  float wA=pow(1.0-smoothstep(0.0,w0,eA),k); float wB=pow(1.0-smoothstep(0.0,w0,eB),k);
  float xw=wA*wB; float web=(wA+wB)*0.35+xw*0.85;
  float depth=mix(0.10,1.0,smoothstep(0.02,0.95,v_uv.y)); float shafts=0.85+0.15*sin(p.x*1.7-t*0.06);
  float a=clamp(web,0.0,1.0)*depth*shafts*0.55;
  vec3 tint=mix(u_color,vec3(1.0),0.18*clamp(xw,0.0,1.0)*depth);
  outColor=vec4(tint*a,a); }
` },
  silk: {
    scale: 0.5, fallback: 'aurora', frag: `
#version 300 es
precision mediump float;
in vec2 v_uv;
uniform float u_time; uniform vec2 u_resolution; uniform vec3 u_color; uniform float u_size; uniform float u_intensity;
out vec4 outColor;
float hash21(vec2 p){ p=mod(p,128.0); return fract(sin(dot(p,vec2(27.61,57.78)))*21758.5453); }
float vnoise(vec2 p){ vec2 i=floor(p); vec2 f=fract(p); vec2 u=f*f*(3.0-2.0*f);
  float a=hash21(i); float b=hash21(i+vec2(1.0,0.0)); float c=hash21(i+vec2(0.0,1.0)); float d=hash21(i+vec2(1.0,1.0));
  return mix(mix(a,b,u.x),mix(c,d,u.x),u.y); }
float fbm(vec2 p){ float s=0.0; float amp=0.5; mat2 m=mat2(1.62,1.18,-1.18,1.62);
  for(int i=0;i<3;i++){ s+=amp*vnoise(p); p=m*p+vec2(7.31,2.84); amp*=0.5; } return s; }
void main(){ vec2 p=(v_uv-0.5)*vec2(u_resolution.x/u_resolution.y,1.0); float t=u_time; float con=clamp(u_intensity,0.0,1.0);
  vec2 s=p*(2.2/max(u_size,0.2))+vec2(0.017,-0.011)*t;
  vec2 q=vec2(fbm(s*0.85+vec2(0.020,0.013)*t),fbm(s*0.85+vec2(4.7,2.3)-vec2(0.015,0.021)*t));
  vec2 w=s+(q-0.44)*1.4; float folds=mix(2.4,5.2,con);
  float ph1=dot(w,vec2(0.66,0.75))*folds+t*0.16; float ph2=dot(w,vec2(-0.35,0.94))*folds*0.62-t*0.11+1.9; float ph3=dot(w,vec2(0.91,-0.41))*folds*1.45+t*0.08+4.2;
  float f=sin(ph1)+0.42*sin(2.0*ph1+1.3)+0.55*sin(ph2)+0.26*sin(ph3); f=clamp(f/1.5,-1.0,1.0);
  float body=smoothstep(mix(-0.55,0.0,con),0.95,f); float crest=smoothstep(0.55,0.95,f); crest*=crest;
  float mask=mix(0.25,1.0,smoothstep(0.24,0.62,q.x)); float a=body*(0.32+0.20*crest)*mask;
  vec3 col=u_color*(0.72+0.38*body); col=mix(col,vec3(1.0),0.22*crest); col=min(col,vec3(1.0));
  outColor=vec4(col*a,a); }
` },
  topo: {
    scale: 0.75, fallback: 'dots', frag: `
#version 300 es
precision mediump float;
in vec2 v_uv;
uniform highp float u_time; uniform highp vec2 u_resolution; uniform vec3 u_color; uniform float u_size; uniform float u_intensity;
out vec4 outColor;
float hashLattice(highp ivec3 v){ highp uvec3 w=uvec3(v); highp uint h=(w.x*0x9E3779B1u)^(w.y*0x85EBCA77u)^(w.z*0xC2B2AE3Du); h=(h^(h>>16u))*0x7FEB352Du; h=(h^(h>>15u))*0x846CA68Bu; h=h^(h>>16u); return float(h&0x00FFFFFFu)*(1.0/16777216.0); }
float vnoise(highp vec3 p){ highp vec3 fp=floor(p); vec3 f=vec3(p-fp); vec3 s=f*f*f*(f*(f*6.0-15.0)+10.0); highp ivec3 i=ivec3(fp);
  float n000=hashLattice(i); float n100=hashLattice(i+ivec3(1,0,0)); float n010=hashLattice(i+ivec3(0,1,0)); float n110=hashLattice(i+ivec3(1,1,0));
  float n001=hashLattice(i+ivec3(0,0,1)); float n101=hashLattice(i+ivec3(1,0,1)); float n011=hashLattice(i+ivec3(0,1,1)); float n111=hashLattice(i+ivec3(1,1,1));
  float x00=mix(n000,n100,s.x); float x10=mix(n010,n110,s.x); float x01=mix(n001,n101,s.x); float x11=mix(n011,n111,s.x);
  float y0=mix(x00,x10,s.y); float y1=mix(x01,x11,s.y); return mix(y0,y1,s.z); }
void main(){ vec2 p=(v_uv-0.5)*vec2(u_resolution.x/u_resolution.y,1.0); float sz=clamp(u_size,0.2,3.0); highp vec2 q=p*(2.6/sz);
  highp float t=u_time*0.01; float g0=clamp(u_intensity*3.0,0.15,1.0); float g1=clamp(u_intensity*3.0-1.0,0.0,1.0); float g2=clamp(u_intensity*3.0-2.0,0.0,1.0);
  highp vec3 c0=vec3(q,5.0+t*g0); highp vec3 c1=vec3(q*2.07+vec2(19.3,-7.1),23.0+t*(1.7*g1)); highp vec3 c2=vec3(q*4.31+vec2(-11.7,33.2),51.0+t*(2.9*g2));
  float h=0.5714*vnoise(c0)+0.2857*vnoise(c1)+0.1429*vnoise(c2);
  float L=15.0; float hl=h*L; float fl=fract(hl); float dl=min(fl,1.0-fl); float idx=floor(hl+0.5); float isMaj=1.0-step(0.5,mod(idx,5.0));
  float sw=1.0/sz; float wMin=clamp(0.040*sw,0.018,0.13); float wMaj=clamp(0.066*sw,0.030,0.20);
  float lnMin=1.0-smoothstep(wMin*0.30,wMin,dl); float lnMaj=1.0-smoothstep(wMaj*0.35,wMaj,dl);
  float ceilA=mix(0.3,0.9,clamp(u_intensity,0.0,1.0)); float a=ceilA*mix(0.55*lnMin,lnMaj,isMaj); a*=mix(0.78,1.0,clamp(h,0.0,1.0));
  vec3 col=mix(u_color,vec3(1.0),0.07*isMaj); outColor=vec4(col*a,a); }
` },
};
