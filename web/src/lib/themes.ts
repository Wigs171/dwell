// Themes ported from the Odysseus design system. Each theme is the same 5-color
// model Odysseus uses — bg, fg (text), panel (surfaces: sidebar + reading page),
// border, accent — and Dwell derives its secondary colors from these via color-mix
// (see app.css). `light` flips color-scheme so form controls/scrollbars match.
// Users can author their own (see the store's custom-theme handling).
export interface Theme {
  name: string;
  bg: string;
  fg: string;
  panel: string;
  border: string;
  accent: string;
  light?: boolean;
  custom?: boolean;
}

export const THEMES: Theme[] = [
  { name: 'light',     bg: '#f0ebe3', fg: '#5a5248', panel: '#faf6f0', border: '#d4cdc2', accent: '#c47d5a', light: true },
  { name: 'dark',      bg: '#282c34', fg: '#ecfbf4', panel: '#111111', border: '#355a66', accent: '#52a98c' },
  { name: 'claude',    bg: '#262624', fg: '#f5f4f0', panel: '#30302e', border: '#4a4a47', accent: '#c6613f' },
  { name: 'midnight',  bg: '#0d1117', fg: '#c9d1d9', panel: '#161b22', border: '#30363d', accent: '#f85149' },
  { name: 'ocean',     bg: '#0b1a2c', fg: '#64d2ff', panel: '#091422', border: '#1e5074', accent: '#4facfe' },
  { name: 'gpt',       bg: '#212121', fg: '#ececec', panel: '#171717', border: '#424242', accent: '#949494' },
  { name: 'vapor',     bg: '#0a0a0f', fg: '#0ff0fc', panel: '#12101a', border: '#9b30ff', accent: '#e040fb' },
  { name: 'retrowave', bg: '#1a1a2e', fg: '#e94560', panel: '#16213e', border: '#533483', accent: '#e94560' },
  { name: 'terminal',  bg: '#000000', fg: '#00ff41', panel: '#0a0a0a', border: '#003b00', accent: '#00ff41' },
  { name: 'daydream',  bg: '#0a0a0a', fg: '#ededed', panel: '#18181b', border: '#262626', accent: '#e84f3d' },
  { name: 'paper',     bg: '#faf8f5', fg: '#3b3836', panel: '#ffffff', border: '#d5d0c8', accent: '#c5ac4a', light: true },
  { name: 'lavender',  bg: '#f3eef8', fg: '#3d3551', panel: '#faf7ff', border: '#cec3de', accent: '#9b6dcc', light: true },
  { name: 'cute',      bg: '#fff0f5', fg: '#d4608a', panel: '#fff8fa', border: '#f0c0d0', accent: '#ff6b9d', light: true },
];

export const DEFAULT_THEME = 'light';

// Each built-in theme ships a signature background animation + tuning (ported from
// Odysseus). Switching theme applies these; the settings window can override them.
export const THEME_BG: Record<string, { pattern: string; intensity?: number; color?: string; frosted?: boolean }> = {
  light:     { pattern: 'none' },
  dark:      { pattern: 'none' },
  claude:    { pattern: 'constellations' },   // a gentle default so animations show out of the box
  paper:     { pattern: 'none' },
  midnight:  { pattern: 'silk' },
  ocean:     { pattern: 'caustics' },
  gpt:       { pattern: 'none' },
  vapor:     { pattern: 'retro-grid' },
  retrowave: { pattern: 'synapse' },
  terminal:  { pattern: 'glyph-rain' },
  lavender:  { pattern: 'petals', frosted: true },
  cute:      { pattern: 'bubbles', color: '#ff8cb8' },
  daydream:  { pattern: 'daydream' },         // the mouse-trail fluid glow, copied from the site
};

// Per-theme colour-cycle palettes. A theme here cycles effects through these exact colours;
// every other theme derives its cycle from its accent. (daydream = its brand palette so its
// effects recreate the site; ordered by hue for smooth transitions.)
export const THEME_CYCLE: Record<string, string[]> = {
  daydream: ['#e84f3d', '#f08a48', '#c7b566', '#3db6be'],
};

export const BG_PATTERNS = [
  'none', 'dots', 'synapse', 'rain', 'constellations', 'perlin-flow',
  'petals', 'sparkles', 'embers', 'aurora', 'glyph-rain', 'retro-grid',
  'fireflies', 'bubbles', 'ripples', 'snow', 'daydream',
  'caustics', 'silk', 'topo',     // WebGL2 (auto-fallback to a 2D cousin)
];

export function themeByName(name: string, customs: Theme[] = []): Theme {
  return customs.find((t) => t.name === name)
    ?? THEMES.find((t) => t.name === name)
    ?? THEMES[1];
}

// Relative luminance of a #rrggbb color → used to auto-set the `light` flag for
// user-authored themes (so color-scheme matches a light background).
export function isLightColor(hex: string): boolean {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return false;
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.6;
}

// Write a theme's 5 base vars onto :root. Everything else in app.css derives
// from these. The reading page, sidebar, chips — all re-theme from one call.
export function writeTheme(t: Theme): void {
  const r = document.documentElement;
  r.style.setProperty('--bg', t.bg);
  r.style.setProperty('--fg', t.fg);
  r.style.setProperty('--panel', t.panel);
  r.style.setProperty('--border', t.border);
  r.style.setProperty('--accent', t.accent);
  const light = t.light ?? isLightColor(t.bg);
  r.style.setProperty('color-scheme', light ? 'light' : 'dark');
  r.classList.toggle('light', light);
}
