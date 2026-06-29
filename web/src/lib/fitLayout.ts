// Image-aware fit-to-page via pretext (no DOM reflow).
//
// The real reader fits a page by shrinking the font until the content fits the
// card. With images that means: predict the total content height at a given
// font size, accounting for image blocks (fixed height) + text (reflows, and
// may wrap around floated images). pretext gives us that height analytically.
//
// Two measurement primitives mirror pretext's two modes:
//   • measureParasRect    — text in a rectangle           → layout()
//   • measureParasWrapped — text flowing around a float    → layoutNextLineRange()
//
// Geometry constants below MIRROR layouts.css. If you change a template's CSS
// (padding, float width, aspect frame, margins), update it here too — the
// prediction is only as good as its mirror. The lab validates the match.

import {
  prepare, prepareWithSegments, layout, layoutNextLineRange,
  type PreparedText, type PreparedTextWithSegments, type LayoutCursor,
} from '@chenglou/pretext';
import type { LayoutId } from './types';

const FAMILY = 'Georgia, "Times New Roman", serif';
const LH = 1.6;                 // .page-layout line-height
const P_GAP = 0.72;             // p margin-bottom (em)
const CAP_FS = 0.76;            // figcaption font-size (em)
const CAP_LH = 1.34;            // figcaption line-height
const CAP_MT = 0.34;            // figcaption margin-top (em)
const PAD_V = 26, PAD_H = 32;   // padded-template padding (px)

const font = (px: number) => `${px}px ${FAMILY}`;

// prepare() is the costly step; cache by (mode, fontPx, text).
const cache = new Map<string, PreparedText | PreparedTextWithSegments>();
function prep(text: string, fontPx: number, seg = false): PreparedText {
  const key = `${seg ? 'S' : 'N'}|${fontPx}|${text}`;
  let p = cache.get(key);
  if (!p) { p = seg ? prepareWithSegments(text || ' ', font(fontPx)) : prepare(text || ' ', font(fontPx)); cache.set(key, p); }
  return p as PreparedText;
}

// Height of a stack of paragraphs in a fixed-width rectangle (each <p> is its
// own block with a margin between — mirrors the DOM, where pretext-per-paragraph
// matches the browser's per-block line breaking).
function measureParasRect(paras: string[], widthPx: number, fontPx: number): number {
  const lh = fontPx * LH;
  let h = 0;
  for (let i = 0; i < paras.length; i++) {
    h += Math.max(1, layout(prep(paras[i], fontPx), widthPx, lh).lineCount) * lh;
    if (i < paras.length - 1) h += P_GAP * fontPx;
  }
  return h;
}

type Exclusion = { side: 'left' | 'right'; w: number; top: number; bottom: number };

// Height of paragraphs flowing around floated image(s): for each line band we
// shrink the available width by any exclusion overlapping that band, then ask
// pretext how far the text gets one line at a time. This is the editorial-engine
// technique, reduced to height-only (no positioning).
function measureParasWrapped(paras: string[], regionW: number, fontPx: number, exclusions: Exclusion[]): number {
  const lh = fontPx * LH;
  let y = 0;
  for (let pi = 0; pi < paras.length; pi++) {
    const prepared = prep(paras[pi], fontPx, true) as PreparedTextWithSegments;
    let cursor: LayoutCursor = { segmentIndex: 0, graphemeIndex: 0 };
    for (;;) {
      const top = y, bottom = y + lh;
      let left = 0, right = regionW;
      for (const ex of exclusions) {
        if (bottom <= ex.top || top >= ex.bottom) continue;
        if (ex.side === 'left') left = Math.max(left, ex.w);
        else right = Math.min(right, regionW - ex.w);
      }
      const w = Math.max(24, right - left);
      const range = layoutNextLineRange(prepared, cursor, w);
      if (range === null) break;
      cursor = range.end;
      y += lh;
    }
    if (pi < paras.length - 1) y += P_GAP * fontPx;
  }
  return y;
}

// figcaption height (text height + its top margin), measured at the figure width.
function captionH(text: string | undefined, widthPx: number, fontPx: number): number {
  if (!text) return 0;
  const capFs = CAP_FS * fontPx, capLh = capFs * CAP_LH;
  const lines = Math.max(1, layout(prep(text, capFs), widthPx, capLh).lineCount);
  return CAP_MT * fontPx + lines * capLh;
}

export type FitContent = { paras: string[]; caption?: string };

// Predicted TOTAL content height (incl. padding) for a template at a font size.
// Compare against the card's clientHeight to decide fit; binary-search fontPx
// for the largest that fits (fitFont below).
export function predictContentHeight(
  layoutId: LayoutId, cardW: number, cardH: number, fontPx: number, content: FitContent,
): number {
  const { paras, caption } = content;
  const cw = cardW - 2 * PAD_H;     // padded content width
  const pad = 2 * PAD_V;

  switch (layoutId) {
    case 'top':
    case 'bottom': {
      const frameH = Math.min(cw * 7 / 16, 15 * fontPx);            // aspect 16/7, max-height 15em
      const imgBlock = frameH + captionH(caption, cw, fontPx) + 0.7 * fontPx;  // + figure margin
      return pad + imgBlock + measureParasRect(paras, cw, fontPx);
    }
    case 'side':
    case 'inset': {
      const isInset = layoutId === 'inset';
      const frac = isInset ? 0.30 : 0.42;
      const floatW = frac * cw;
      const frameH = isInset ? floatW : Math.min(floatW * 4 / 3, 23 * fontPx);  // inset 1/1, side 3/4
      const mTop = isInset ? 0.2 : 0.15, mBot = isInset ? 0.5 : 0.55, mInner = 1.1;
      const floatBoxH = (mTop + mBot) * fontPx + frameH + captionH(caption, floatW, fontPx);
      const ex: Exclusion = { side: isInset ? 'left' : 'right', w: floatW + mInner * fontPx, top: 0, bottom: floatBoxH };
      const textH = measureParasWrapped(paras, cw, fontPx, [ex]);
      return pad + Math.max(textH, floatBoxH);
    }
    case 'magazine': {
      const cwM = cardW - 2 * 30;                                   // magazine padding is 30 horizontal
      const colGap = 20, ncols = 3;
      const colW = (cwM - (ncols - 1) * colGap) / ncols;
      // The image is now a COLUMN-WIDTH block IN the flow (a thin portrait), so it
      // joins the text and the whole lot balances into 3 columns ≈ total/3.
      const imgBlockH = colW * 4 / 3 + captionH(caption, colW, fontPx) + 0.9 * fontPx;
      const colsH = (measureParasRect(paras, colW, fontPx) + imgBlockH) / ncols + 1 * fontPx * LH;
      return pad + colsH;
    }
    case 'rail': {
      // No outer padding; text lives in the 62% column with its own 26×30 padding.
      const textW = cardW * 0.62 - 60;
      return 2 * PAD_V + measureParasRect(paras, textW, fontPx);
    }
    case 'diagonal': {
      const figaW = 0.42 * cw, figbW = 0.44 * cw;
      const figaH = Math.min(figaW * 2 / 3, 13 * fontPx);           // aspect 3/2, max 13em
      const figbH = Math.min(figbW, 14 * fontPx);                   // aspect 1/1, max 14em
      const aBox = 0.6 * fontPx + figaH + captionH(caption, figaW, fontPx);
      // fig-b drops after ~2 paragraphs; approximate its top as the height of 2 paras beside fig-a.
      const bTop = measureParasWrapped(paras.slice(0, 2), cw, fontPx,
        [{ side: 'right', w: figaW + 1.1 * fontPx, top: 0, bottom: aBox }]);
      const bBox = 0.9 * fontPx + figbH + captionH(caption, figbW, fontPx);
      const ex: Exclusion[] = [
        { side: 'right', w: figaW + 1.1 * fontPx, top: 0, bottom: aBox },
        { side: 'left', w: figbW + 1.1 * fontPx, top: bTop, bottom: bTop + bBox },
      ];
      return pad + measureParasWrapped(paras, cw, fontPx, ex);
    }
    case 'mosaic': {
      const bannerH = Math.min(cw * 6 / 21, 9 * fontPx) + 0.65 * fontPx;  // banner aspect 21/6, max 9em
      const fig2W = 0.35 * cw, fig3W = 0.30 * cw;
      const fig2Box = 0.6 * fontPx + fig2W * 3 / 4 + captionH(caption, fig2W, fontPx);   // 4/3 → H=W·0.75
      const bTop = measureParasWrapped(paras.slice(0, 2), cw, fontPx,
        [{ side: 'right', w: fig2W + 1 * fontPx, top: 0, bottom: fig2Box }]);
      const fig3Box = 0.85 * fontPx + fig3W + captionH(caption, fig3W, fontPx);
      const ex: Exclusion[] = [
        { side: 'right', w: fig2W + 1 * fontPx, top: 0, bottom: fig2Box },
        { side: 'left', w: fig3W + 1 * fontPx, top: bTop, bottom: bTop + fig3Box },
      ];
      return pad + bannerH + measureParasWrapped(paras, cw, fontPx, ex);
    }
    case 'hero': {
      // Text is a limited overlay in the scrim; the image is fixed full-bleed.
      // The card always "fits" — but report the scrim text height for completeness.
      const cw2 = cardW - 2 * PAD_H;
      return measureParasRect(paras.slice(0, 3), cw2, fontPx);
    }
  }
  return 0;
}

// Largest font ≤ maxFont whose predicted content fits the card height.
export function fitFont(layoutId: LayoutId, cardW: number, cardH: number, content: FitContent, maxFont = 17, floor = 9): number {
  if (predictContentHeight(layoutId, cardW, cardH, maxFont, content) <= cardH) return maxFont;
  let lo = floor, hi = maxFont, best = floor;
  for (let i = 0; i < 9; i++) {
    const mid = (lo + hi) / 2;
    if (predictContentHeight(layoutId, cardW, cardH, mid, content) <= cardH) { best = mid; lo = mid; } else hi = mid;
  }
  return best;
}
