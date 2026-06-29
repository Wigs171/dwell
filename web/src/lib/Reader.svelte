<script lang="ts">
  import { dwell } from './dwell.svelte';
  import type { PageView, QuizQuestion, PageFigure, Mark } from './types';
  import { prepare, layout } from '@chenglou/pretext';
  import VolumeRail from './VolumeRail.svelte';

  const BASE = 17;        // base reading font (px), scaled by fit × zoom
  const FLOOR = 0.58;     // never shrink a page below this; past it, the card scrolls
  const LINE_HEIGHT = 1.62;                                       // mirrors .prose line-height
  const FONT_BASE = `${BASE}px Georgia, "Times New Roman", serif`; // mirrors .prose font-family

  let stage = $state<HTMLDivElement>();
  let deckEl = $state<HTMLDivElement>();
  // Element maps, keyed by page key. The card IS the scroll viewport; the prose is
  // measured/ranged inside it. Kept in sync by the register actions below.
  const cardEls: Record<number, HTMLElement> = {};
  const proseEls: Record<number, HTMLElement> = {};
  const fits: Record<number, number> = {};         // fit-to-page scale per page key
  let pop = $state<{ key: number; start: number; length: number; x: number; y: number; text: string } | null>(null);

  const arrow: Record<string, string> = { open: '◉', dwell: '↻', move: '→' };

  type Card = { key: number; off: number; center: boolean; ghost: boolean; page?: PageView };

  // The visible window: previous · current · next (+ a ghost "compose next" card at
  // the live edge). At the very start there's no previous, so only 2 cards show.
  const cards = $derived.by<Card[]>(() => {
    const out: Card[] = [];
    const n = dwell.pages.length;
    const cur = dwell.cursor;
    for (let i = cur - 1; i <= cur + 1; i++) {
      if (i < 0) continue;
      if (i < n) out.push({ key: dwell.pages[i].key, off: i - cur, center: i === cur, ghost: false, page: dwell.pages[i] });
      else if (i === n && dwell.started) out.push({ key: -1, off: i - cur, center: false, ghost: true });
    }
    return out;
  });

  // ---- register actions: keep the element maps tracking the live cards ----
  function regCard(node: HTMLElement, key: number) {
    cardEls[key] = node;
    return { destroy() { if (cardEls[key] === node) delete cardEls[key]; } };
  }
  function regProse(node: HTMLElement, key: number) {
    proseEls[key] = node;
    queueFit();
    return { destroy() { if (proseEls[key] === node) delete proseEls[key]; } };
  }

  // ---- fit-to-page ---------------------------------------------------------
  // At zoom 1 the whole page must show without scrolling, so we shrink the font
  // until the content fits the card. Measuring at the base size and dividing by
  // the overflow gives the scale; the card only scrolls once zoom pushes past fit.
  // Font-size IS the zoom: `fits[key]` is the scale (≤1) at which the whole page
  // fits the card; the centre card multiplies it by `zoom`. We drive the prose
  // font-size imperatively in a microtask (runs before paint → no flash; unlike
  // rAF it still fires in a backgrounded/headless tab).
  function applyScale(key: number) {
    const prose = proseEls[key];
    if (!prose) return;
    const isCenter = dwell.focused?.key === key;
    prose.style.fontSize = (BASE * (fits[key] ?? 1) * (isCenter ? dwell.zoom : 1)) + 'px';
    // A page that can't fit even at the floor font (a very long page, or a tall
    // figure eating the height) SCROLLS rather than clipping — the documented
    // "past the floor, the card scrolls" behavior, for text and image pages alike.
    const card = cardEls[key];
    if (card) {
      const rt = card.querySelector('.rail-text') as HTMLElement | null;
      if (rt) rt.classList.toggle('scroll', rt.scrollHeight - rt.clientHeight > 2);
      else card.classList.toggle('overflowing', card.scrollHeight - card.clientHeight > 2);
    }
  }

  // chenglou/pretext: compute the fit WITHOUT touching the DOM. `prepare()` runs
  // once per page text; `layout()` is pure arithmetic. Char advances scale linearly
  // with font size, so laying out at font `BASE*s` in width `W` == laying out the
  // BASE-prepared text in width `W/s` — we binary-search the largest s≤1 that fits.
  const preparedCache = new Map<number, { text: string; prepared: ReturnType<typeof prepare> }>();
  let usePretext = true;          // flips to the DOM fallback if pretext ever throws
  let geomW = 0, geomH = 0;       // prose wrap width + available height (font-independent; cached)

  function refreshGeom() {
    const key = dwell.focused?.key;
    const card = key != null ? cardEls[key] : undefined;
    const prose = key != null ? proseEls[key] : undefined;
    if (!card || !prose) return;
    const pad = prose.parentElement as HTMLElement | null;
    // Reserve the scrollbar width: fitting to the *narrower* width means the
    // prediction holds whether or not a scrollbar appears, killing the
    // overflow→scrollbar→narrower→taller→overflow feedback at fit.
    geomW = Math.max(80, prose.clientWidth - 12);
    const maxH = parseFloat(getComputedStyle(card).maxHeight);   // calc(100% - 22px) → used px
    const cap = isFinite(maxH) ? maxH : (card.offsetParent as HTMLElement)?.clientHeight ?? card.clientHeight;
    let chrome = 60;                                             // .pad padding fallback
    if (pad) { const cs = getComputedStyle(pad); chrome = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom); }
    const meta = pad?.querySelector('.meta') as HTMLElement | null;
    chrome += meta ? meta.offsetHeight + parseFloat(getComputedStyle(meta).marginTop || '0') : 40;  // reserve ~2 meta lines if not shown yet
    geomH = Math.max(40, cap - chrome - 16);                     // safety so no sliver scrollbar
  }

  function pretextFit(key: number, text: string) {
    if (!geomW || !geomH) { applyScale(key); return; }   // geom refreshed once per fit pass (queueFit)
    let entry = preparedCache.get(key);
    if (!entry || entry.text !== text) {
      entry = { text, prepared: prepare(text || ' ', FONT_BASE, { whiteSpace: 'pre-wrap' }) };
      preparedCache.set(key, entry);
    }
    const heightAt = (s: number) => layout(entry!.prepared, geomW / s, BASE * LINE_HEIGHT).lineCount * (BASE * s * LINE_HEIGHT);
    let s = 1;
    if (heightAt(1) > geomH) {
      let lo = FLOOR, hi = 1;
      for (let i = 0; i < 9; i++) { const mid = (lo + hi) / 2; if (heightAt(mid) <= geomH) lo = mid; else hi = mid; }
      s = lo;
    }
    fits[key] = s;
    applyScale(key);
  }

  // Fallback (only if pretext throws): the old DOM measure — set base font, read
  // the reflowed height, divide, one correction pass for the fixed padding/meta.
  function domFit(key: number) {
    const card = cardEls[key], prose = proseEls[key];
    if (!card || !prose) return;
    prose.style.fontSize = BASE + 'px';
    const avail = card.clientHeight, content = card.scrollHeight;
    if (!avail || !content) { applyScale(key); return; }
    let f = Math.max(FLOOR, Math.min(1, avail / content));
    fits[key] = f; applyScale(key);
    const enlarging = dwell.focused?.key === key && dwell.zoom > 1;
    if (!enlarging && f > FLOOR && card.scrollHeight - card.clientHeight > 2) {
      fits[key] = Math.max(FLOOR, f * (card.clientHeight / card.scrollHeight));
      applyScale(key);
    }
  }

  // Image pages: figures/floats/grids DON'T scale with the prose font, so the
  // analytic single-rectangle fit doesn't hold. Binary-search the prose font (at
  // zoom 1) until nothing overflows — correct for any layout. Figure boxes are
  // CSS aspect-ratio frames, so this needs no image-load wait. The prose stays a
  // single text node, so karaoke/clarify keep working untouched.
  function domFitImage(key: number) {
    const card = cardEls[key], prose = proseEls[key];
    if (!card || !prose) return;
    const over = () => {
      let o = card.scrollHeight - card.clientHeight;
      const rt = card.querySelector('.rail-text') as HTMLElement | null;   // rail clips its column
      if (rt) o = Math.max(o, rt.scrollHeight - rt.clientHeight);
      return o;
    };
    const test = (s: number) => { prose.style.fontSize = BASE * s + 'px'; return over() <= 1; };
    let s = 1;
    if (!test(1)) {
      let lo = FLOOR, hi = 1;
      for (let i = 0; i < 9; i++) { const mid = (lo + hi) / 2; if (test(mid)) { s = lo = mid; } else hi = mid; }
      s = lo;
    }
    fits[key] = s;
    applyScale(key);                  // re-applies BASE·s·zoom for the focused card
  }
  const figClass: Record<string, string> = { top: 'fig-top', side: 'fig-side', inset: 'fig-inset', bottom: 'fig-bottom' };
  // Multi-image layouts place a figure mid-flow → the prose renders as <p>s with
  // figures interspersed (the offset map keeps karaoke/clarify aligned).
  const MULTI = new Set(['magazine', 'diagonal', 'mosaic']);
  const paras = (t: string): string[] => t.split(/\n{2,}/).filter(Boolean);
  // Detect a structured FORM page from its text so the reader can style it. Block-level
  // (whole lines/paragraphs), so the karaoke/clarify/quiz offset-map is untouched. Split
  // on ANY run of newlines so it works whether turns/beats are single-\n or blank-line
  // separated. Dialogue = em-dash turns; FAQ = recurring ?-terminated questions.
  // Split clean text into blocks (non-empty lines), tracking each block's start offset
  // in the full text so emphasis marks (global char offsets) map to block-local spans.
  function splitBlocks(text: string): { text: string; start: number }[] {
    const out: { text: string; start: number }[] = [];
    const re = /[^\n]+/g; let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      const lead = m[0].length - m[0].trimStart().length;
      const t = m[0].trim();
      if (t) out.push({ text: t, start: m.index + lead });
    }
    return out;
  }
  type Seg = { text: string; kind?: 'strong' | 'em' };
  // Inline emphasis (strong/em) within a block → a sequence of plain/wrapped runs. Adds
  // ELEMENTS, not characters, so the rendered text content still equals page.text.
  function inlineSegs(blk: { text: string; start: number }, marks: Mark[]): Seg[] {
    const bs = blk.start, be = bs + blk.text.length;
    const inl = marks.filter((m) => (m.kind === 'strong' || m.kind === 'em') && m.end > bs && m.start < be)
      .map((m) => ({ s: Math.max(0, m.start - bs), e: Math.min(blk.text.length, m.end - bs), kind: m.kind }))
      .sort((a, b) => a.s - b.s);
    const out: Seg[] = []; let pos = 0;
    for (const m of inl) {
      if (m.s > pos) out.push({ text: blk.text.slice(pos, m.s) });
      if (m.e > m.s) out.push({ text: blk.text.slice(m.s, m.e), kind: m.kind as 'strong' | 'em' });
      pos = Math.max(pos, m.e);
    }
    if (pos < blk.text.length) out.push({ text: blk.text.slice(pos) });
    return out;
  }
  // A block is a heading when an h-mark spans it.
  function headingKind(blk: { text: string; start: number }, marks: Mark[]): 'h1' | 'h2' | null {
    const bs = blk.start, be = bs + blk.text.length;
    const h = marks.find((m) => (m.kind === 'h1' || m.kind === 'h2') && m.start <= bs && m.end >= be);
    return h ? (h.kind as 'h1' | 'h2') : null;
  }
  const hasMarks = (p?: PageView): boolean => !!p && p.marks?.length > 0;
  // Forms the reader styles structurally. Driven by the page's STABLE `form` (not text
  // sniffing), so the layout never flips mid-stream. Block-level (whole lines), so the
  // karaoke/clarify/quiz offset-map is untouched.
  const isFormStyled = (form?: string): boolean => form === 'dialogue' || form === 'qa';
  const lineClass = (kind: string, p: string, i: number): string =>
    kind === 'dialogue' ? (i % 2 === 0 ? 'turn-a' : 'turn-b')
    : kind === 'qa' ? (/\?$/.test(p) ? 'q' : 'a')
    : '';
  const midSpan = (n: number) => Math.max(1, Math.round(n / 2));   // magazine band drop point
  const midAt = (n: number) => Math.min(2, Math.max(0, n - 1));    // diagonal/mosaic 2nd-image drop point

  let fitQueued = false;
  function queueFit() {
    if (fitQueued) return;
    fitQueued = true;
    queueMicrotask(() => {
      fitQueued = false;
      refreshGeom();          // keep geom current (one cheap read per pass — never stale)
      const byKey = new Map(dwell.pages.map((p) => [p.key, p]));
      for (const k of Object.keys(proseEls)) {
        const key = Number(k);
        const page = byKey.get(key);
        if (page && page.images.length) { domFitImage(key); continue; }   // image layout → DOM fit
        if (isFormStyled(page?.form) || hasMarks(page) || page?.textFigure) { domFit(key); continue; }  // form/emphasis/text-figure is non-uniform → DOM measure
        const text = page?.text ?? '';
        if (usePretext) { try { pretextFit(key, text); } catch { usePretext = false; domFit(key); } }
        else domFit(key);
      }
    });
  }
  // Re-fit/zoom as the focused page streams in, as cards change, on zoom, on resize.
  $effect(() => {
    void dwell.pages.length; void dwell.cursor; void dwell.focused?.text; void dwell.zoom;
    void dwell.focused?.layout; void dwell.focused?.images.length;   // re-fit when images/layout change (done, unpin)
    void dwell.focused?.form; void dwell.focused?.marks?.length;     // …and when form/emphasis changes
    queueFit();
  });
  // Refresh geometry whenever the deck's box changes — viewport resize, the
  // branches/transport bars appearing, sidebar toggling, etc. A plain 'resize'
  // listener misses layout-driven changes; ResizeObserver catches them (and fires
  // in a headless tab). Per-frame streaming reuses the cached geom (no reflow).
  $effect(() => {
    if (!deckEl) return;
    const ro = new ResizeObserver(() => queueFit());   // queueFit refreshes geom itself
    ro.observe(deckEl);
    return () => ro.disconnect();
  });

  // Dev-only: confirm pretext is the live fit path + inspect the cached geometry.
  if (import.meta.env?.DEV) {
    (globalThis as Record<string, unknown>).__dwellFit = () =>
      ({ usePretext, geomW, geomH, fits: { ...fits } });
  }

  // ---- karaoke: highlight the spoken word + auto-scroll the focused card -----
  const spokenHL: Highlight | null =
    (typeof Highlight !== 'undefined' && (CSS as unknown as { highlights?: HighlightRegistry }).highlights)
      ? new Highlight() : null;
  if (spokenHL) (CSS as unknown as { highlights: HighlightRegistry }).highlights.set('dwell-spoken', spokenHL);

  // Quiz "open-book" highlighter — while a quiz is up, highlight each question's
  // evidence span in the rendered pages so the reader can flip back and find it.
  const quizHL: Highlight | null =
    (typeof Highlight !== 'undefined' && (CSS as unknown as { highlights?: HighlightRegistry }).highlights)
      ? new Highlight() : null;
  if (quizHL) (CSS as unknown as { highlights: HighlightRegistry }).highlights.set('dwell-quiz', quizHL);

  // Note highlighter — the saved passage a note points to, painted when you jump back to it.
  const noteHL: Highlight | null =
    (typeof Highlight !== 'undefined' && (CSS as unknown as { highlights?: HighlightRegistry }).highlights)
      ? new Highlight() : null;
  if (noteHL) (CSS as unknown as { highlights: HighlightRegistry }).highlights.set('dwell-note', noteHL);

  // ---- prose offset map -----------------------------------------------------
  // Layouts that place a figure MID-FLOW (magazine/diagonal/mosaic) split the
  // prose into several text nodes, and the rendered <p>s drop the \n\n paragraph
  // separators that page.text carries. This maps the rendered text nodes back to
  // offsets in page.text, so karaoke / quiz-highlight / select-to-clarify behave
  // the same whether the prose is ONE text node (top/side/rail) or many.
  type ProseSeg = { node: Text; gStart: number; gEnd: number };
  function proseSegs(container: HTMLElement, pageText: string): ProseSeg[] {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement?.closest('figcaption, figure') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    const segs: ProseSeg[] = [];
    let cursor = 0;
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n.textContent ?? '';
      if (!t.trim()) continue;                            // skip whitespace-only nodes (template indentation between <p>s)
      const idx = pageText.indexOf(t, cursor);           // align each node to page.text (skips the \n\n gaps)
      const gStart = idx >= 0 ? idx : cursor;
      segs.push({ node: n as Text, gStart, gEnd: gStart + t.length });
      cursor = gStart + t.length;
    }
    return segs;
  }
  function posAt(segs: ProseSeg[], g: number): { node: Text; offset: number } | null {
    for (const s of segs) if (g <= s.gEnd) return { node: s.node, offset: Math.max(0, Math.min(g, s.gEnd) - s.gStart) };
    const last = segs[segs.length - 1];
    return last ? { node: last.node, offset: last.gEnd - last.gStart } : null;
  }
  function rangeFor(segs: ProseSeg[], cs: number, ce: number): Range | null {
    const a = posAt(segs, cs), b = posAt(segs, ce);
    if (!a || !b) return null;
    const r = document.createRange();
    try { r.setStart(a.node, a.offset); r.setEnd(b.node, b.offset); } catch { return null; }
    return r;
  }
  function globalOffset(segs: ProseSeg[], node: Node, offset: number): number | null {
    for (const s of segs) if (s.node === node) return s.gStart + offset;
    return null;     // boundary not in the reading prose (e.g. a caption) → caller ignores
  }
  const pageTextOf = (key: number) => dwell.pages.find((p) => p.key === key)?.text ?? '';

  function findSpan(hay: string, needle: string): [number, number] | null {
    const n = needle.trim();
    if (n.length < 4) return null;
    const tryMatch = (s: string): [number, number] | null => {
      const i = hay.indexOf(s);
      if (i >= 0) return [i, i + s.length];
      const esc = s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');   // whitespace-tolerant
      try { const m = new RegExp(esc).exec(hay); if (m) return [m.index, m.index + m[0].length]; } catch { /* ignore */ }
      return null;
    };
    const full = tryMatch(n);
    if (full) return full;
    // near-verbatim fallback (the model sometimes paraphrases the tail/head of the
    // quote): match the longest leading or trailing run of ≥4 words that is present.
    const w = n.split(/\s+/);
    for (let k = w.length - 1; k >= 4; k--) {
      const hit = tryMatch(w.slice(0, k).join(' ')) ?? tryMatch(w.slice(w.length - k).join(' '));
      if (hit) return hit;
    }
    return null;
  }
  function applyQuizHL(open: boolean, quiz: QuizQuestion[] | null) {
    quizHL?.clear();
    if (!open || !quiz || !quizHL) return;
    const ev = quiz.map((q) => q.evidence ?? '').filter((e) => e.trim().length >= 4);
    if (!ev.length) return;
    for (const k of Object.keys(proseEls)) {
      const key = Number(k);
      const prose = proseEls[key];
      const text = pageTextOf(key);
      if (!prose || !text) continue;
      const segs = proseSegs(prose, text);
      if (!segs.length) continue;
      for (const e of ev) {
        const span = findSpan(text, e);                  // search the full page text
        if (!span) continue;
        const r = rangeFor(segs, span[0], span[1]);      // → a Range (may cross paragraphs)
        if (r) quizHL.add(r);
      }
    }
  }
  $effect(() => {
    const open = dwell.quizOpen, quiz = dwell.quiz;
    void dwell.cursor; void dwell.pages.length;        // re-highlight as the visible cards change
    queueMicrotask(() => applyQuizHL(open, quiz));      // after the flipped-to prose has mounted
  });

  // Note-jump: paint the saved passage on its page (keyed by page → only that page) and
  // scroll it into view. Re-applies as the visible cards change; clears when off-screen.
  $effect(() => {
    const nh = dwell.noteHighlight;
    void dwell.cursor; void dwell.pages.length;
    queueMicrotask(() => {
      noteHL?.clear();
      if (!nh || !noteHL) return;
      const prose = proseEls[nh.key], text = pageTextOf(nh.key);
      if (!prose || !text) return;
      const segs = proseSegs(prose, text);
      const span = segs.length ? findSpan(text, nh.text) : null;
      if (!span) return;
      const r = rangeFor(segs, span[0], span[1]);
      if (!r) return;
      noteHL.add(r);
      const card = cardEls[nh.key];
      if (card && nh.key === dwell.focused?.key) {
        const wr = r.getBoundingClientRect(), cr = card.getBoundingClientRect();
        if (wr.height && (wr.bottom > cr.bottom - 40 || wr.top < cr.top + 20)) {
          card.scrollBy({ top: wr.top - (cr.top + cr.height * 0.4), behavior: 'smooth' });
        }
      }
    });
  });

  $effect(() => {
    const sp = dwell.spoken;
    spokenHL?.clear();
    if (!sp || !spokenHL) return;
    const card = cardEls[sp.key], prose = proseEls[sp.key];
    if (!card || !prose) return;
    const segs = proseSegs(prose, pageTextOf(sp.key));
    if (!segs.length) return;
    const total = segs[segs.length - 1].gEnd;
    const cs = Math.max(0, Math.min(sp.cs, total));
    const ce = Math.max(cs, Math.min(sp.ce, total));
    if (ce <= cs) return;
    const r = rangeFor(segs, cs, ce);
    if (!r) return;
    spokenHL.add(r);
    // Only the focused card auto-scrolls (and only when zoomed enough to overflow).
    if (sp.key !== dwell.focused?.key) return;
    const wr = r.getBoundingClientRect();
    const cr = card.getBoundingClientRect();
    if (wr.height && (wr.bottom > cr.bottom - 40 || wr.top < cr.top + 20)) {
      card.scrollBy({ top: wr.top - (cr.top + cr.height * 0.4), behavior: 'smooth' });
    }
  });

  // ---- navigation: keyboard / wheel / touch ---------------------------------
  function onKey(e: KeyboardEvent) {
    if (!dwell.started) return;
    const t = e.target as HTMLElement | null;
    if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
    if (e.key === 'ArrowLeft') dwell.goPrev();
    else if (e.key === 'ArrowRight') dwell.goNext();
    else if (e.key === '+' || e.key === '=') dwell.zoomIn();
    else if (e.key === '-' || e.key === '_') dwell.zoomOut();
    else if (e.key === '0') dwell.resetZoom();
    else return;
    e.preventDefault();
  }

  let flipAt = 0;
  function flip(dir: number) {
    const now = performance.now();
    if (now - flipAt < 380) return;             // debounce trackpad inertia
    flipAt = now;
    if (dir > 0) dwell.goNext(); else dwell.goPrev();
  }
  function onWheel(e: WheelEvent) {
    if (!dwell.started) return;
    if (e.ctrlKey) {                            // ctrl/trackpad-pinch → zoom
      e.preventDefault();
      dwell.setZoom(dwell.zoom * (e.deltaY < 0 ? 1.12 : 0.89));
      return;
    }
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && Math.abs(e.deltaX) > 24) {
      e.preventDefault();                        // horizontal intent → flip pages
      flip(e.deltaX > 0 ? 1 : -1);
    }
    // otherwise let the card scroll natively (only possible when zoomed)
  }

  // touch: 1-finger horizontal swipe flips; 2-finger pinch zooms. Vertical drags
  // fall through to the card's native scroll (touch-action: pan-y).
  let tx = 0, ty = 0, swiping = false;
  let pinchBase = 0, pinchZoom0 = 1, pinching = false;
  const span = (t: TouchList) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  function onTouchStart(e: TouchEvent) {
    if (e.touches.length === 2) { pinching = true; swiping = false; pinchBase = span(e.touches); pinchZoom0 = dwell.zoom; }
    else if (e.touches.length === 1) { swiping = true; pinching = false; tx = e.touches[0].clientX; ty = e.touches[0].clientY; }
  }
  function onTouchMove(e: TouchEvent) {
    if (pinching && e.touches.length === 2) {
      e.preventDefault();
      dwell.setZoom(pinchZoom0 * (span(e.touches) / (pinchBase || 1)));
    }
  }
  function onTouchEnd(e: TouchEvent) {
    if (pinching) { if (e.touches.length < 2) pinching = false; return; }
    if (!swiping) return;
    swiping = false;
    const t = e.changedTouches[0];
    if (!t) return;
    const dx = t.clientX - tx, dy = t.clientY - ty;
    if (Math.abs(dx) > 56 && Math.abs(dx) > Math.abs(dy) * 1.4) { if (dx > 0) dwell.goPrev(); else dwell.goNext(); }
  }

  function navigateTo(c: Card) {
    if (c.ghost) dwell.goNext();
    else dwell.goTo(dwell.cursor + c.off);
  }

  // ---- select-to-expand (centre card only) ----------------------------------
  function onMouseUp() {
    setTimeout(() => {
      // Show the popover for ANY valid selection on a page — notes don't need a live
      // engine; the Simplify/Expound buttons are gated on `canExpand` in the markup.
      // Selecting does NOT pause narration — only requesting a rework (expand) stops it.
      if (!dwell.started) { pop = null; return; }
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) { pop = null; return; }
      const text = sel.toString();
      if (text.trim().length < 8) { pop = null; return; }
      const anchor = sel.anchorNode;
      const el = anchor instanceof Element ? anchor : anchor?.parentElement ?? null;
      const prose = el?.closest('.prose') as HTMLElement | null;
      const article = prose?.closest('.card') as HTMLElement | null;
      if (!prose || !article) { pop = null; return; }
      const key = Number(article.dataset.key);
      const segs = proseSegs(prose, pageTextOf(key));
      const r = sel.getRangeAt(0);
      const startG = globalOffset(segs, r.startContainer, r.startOffset);
      const endG = globalOffset(segs, r.endContainer, r.endOffset);
      if (startG == null || endG == null || endG <= startG) { pop = null; return; }
      const rect = r.getBoundingClientRect();
      pop = {
        key, start: startG, length: endG - startG,   // global span (carries the \n\n separators)
        x: Math.min(rect.left, window.innerWidth - 230), y: rect.bottom + 6, text,
      };
    }, 0);
  }
  function doExpand(mode: string) {
    if (!pop) return;
    const { key, start, length } = pop;
    pop = null;
    void dwell.expand(key, start, length, mode);
  }
  function doNote() {
    if (!pop) return;
    dwell.noteFromSelection(pop.text, pop.key);   // capture the passage as-is + its node
    pop = null;                                   // narration is untouched — keep reading
  }
  function onDocDown(e: MouseEvent) {
    if (pop && !(e.target as HTMLElement)?.closest?.('.pop')) pop = null;
  }
</script>

<svelte:window onmousedown={onDocDown} onkeydown={onKey} />

{#snippet figEl(im: PageFigure, cls: string, natural = false)}
  <!-- `natural` (single-image auto layouts) gives the frame the image's TRUE aspect
       ratio so a vertical image is never forced into a horizontal frame; width:100%
       + max-height (from CSS) keep it width-anchored (no left-align) and capped.
       Multi-image pinned compositions keep their fixed compositional frames. -->
  <figure class={cls}>
    <div class="figframe" style={natural && im.w && im.h ? `aspect-ratio:${im.w}/${im.h}` : undefined}><img src={im.url} alt={im.caption} /></div>
    {#if im.caption}<figcaption>{im.caption}</figcaption>{/if}
  </figure>
{/snippet}

{#snippet metaLine(c: Card)}
  {#if !c.page!.live}
    <div class="meta">
      {arrow[c.page!.mode] ?? '·'} {c.page!.node}{c.page!.steer_bucket && c.page!.steer_bucket !== 'none' ? '  ↳' + c.page!.steer_bucket : ''}{c.page!.marker === 'coast' ? '  · Dwell' : ''}
    </div>
  {/if}
{/snippet}

<!-- a derived text-figure that FLOATS in the prose (pull-quote): a <figure> so it stays
     out of the karaoke/clarify offset walk (proseSegs rejects figure). drop-cap is not a
     node — it's a .dropcap class driving ::first-letter. Both only on no-image pages. -->
{#snippet pullQuote(c: Card)}{#if c.page!.textFigure?.kind === 'pull-quote'}<figure class="tf-pullquote" data-narration="skip" aria-hidden="true">{c.page!.textFigure.payload.text}</figure>{/if}{/snippet}

{#snippet pageBody(c: Card)}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div class="prose" class:dropcap={c.page!.textFigure?.kind === 'drop-cap'} lang="en" use:regProse={c.key} role="document"
       onmouseup={c.center ? onMouseUp : undefined}>{@render pullQuote(c)}{c.page!.text}</div>
  {@render metaLine(c)}
{/snippet}

<!-- multi-image layouts: prose as <p>s with figures inserted mid-flow -->
{#snippet multiBody(c: Card)}
  {@const ps = paras(c.page!.text)}
  {@const L = c.page!.layout}
  {@const imgs = c.page!.images}
  {#if L === 'mosaic' && imgs[0]}{@render figEl(imgs[0], 'banner')}{/if}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div class="prose {L === 'magazine' ? 'mag-cols' : L === 'mosaic' ? 'mbody' : 'diag'}" lang="en"
       use:regProse={c.key} role="document" onmouseup={c.center ? onMouseUp : undefined}>
    {#if L === 'diagonal' && imgs[0]}{@render figEl(imgs[0], 'fig-a')}{/if}
    {#if L === 'mosaic' && imgs[1]}{@render figEl(imgs[1], 'fig-2')}{/if}
    {#each ps as p, i}
      {#if L === 'magazine' && imgs[0] && i === midSpan(ps.length)}{@render figEl(imgs[0], 'mag-mid')}{/if}
      {#if L === 'diagonal' && imgs[1] && i === midAt(ps.length)}{@render figEl(imgs[1], 'fig-b')}{/if}
      {#if L === 'mosaic' && imgs[2] && i === midAt(ps.length)}{@render figEl(imgs[2], 'fig-3')}{/if}
      <p>{p}</p>
    {/each}
  </div>
  {@render metaLine(c)}
{/snippet}

<!-- form pages (dialogue / FAQ): prose as classed <p>s the reader styles by structure.
     One .prose text node per paragraph keeps the offset map (karaoke/clarify) aligned. -->
{#snippet inlineRender(segs: Seg[])}{#each segs as s}{#if s.kind === 'strong'}<strong>{s.text}</strong>{:else if s.kind === 'em'}<em>{s.text}</em>{:else}{s.text}{/if}{/each}{/snippet}

<!-- structured/emphasised prose: blocks rendered with form classes, headings, and inline
     bold/italic — all block-level wrapping, so the karaoke/clarify offset-map stays aligned. -->
{#snippet proseBody(c: Card)}
  {@const marks = c.page!.marks ?? []}
  {@const form = c.page!.form}
  {@const formCls = form === 'dialogue' ? 'form-dialogue' : form === 'qa' ? 'form-qa' : ''}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div class="prose {formCls}" class:dropcap={c.page!.textFigure?.kind === 'drop-cap'} lang="en" use:regProse={c.key} role="document"
       onmouseup={c.center ? onMouseUp : undefined}>{@render pullQuote(c)}{#each splitBlocks(c.page!.text) as blk, i}{@const h = headingKind(blk, marks)}{#if h}<div class="rich-{h}">{@render inlineRender(inlineSegs(blk, marks))}</div>{:else}<p class={lineClass(form, blk.text, i)}>{@render inlineRender(inlineSegs(blk, marks))}</p>{/if}{/each}</div>
  {@render metaLine(c)}
{/snippet}

<!-- the page body: rich (form-styled and/or emphasised) or plain prose — with or without images -->
{#snippet body(c: Card)}{#if isFormStyled(c.page!.form) || hasMarks(c.page)}{@render proseBody(c)}{:else}{@render pageBody(c)}{/if}{/snippet}

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div class="stage" bind:this={stage} role="document"
     onwheel={onWheel} ontouchstart={onTouchStart} ontouchmove={onTouchMove} ontouchend={onTouchEnd}>
  {#if !dwell.started && !dwell.pages.length}
    <div class="empty">
      {dwell.session ? 'Choose where to begin above.' : 'Choose a knowledge base to begin.'}
    </div>
  {:else}
    <div class="deck" bind:this={deckEl}>
      {#each cards as c (c.key)}
        <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
        <article
          class="page card"
          class:center={c.center}
          class:ghost={c.ghost}
          class:zoomed={c.center && dwell.zoom > 1}
          class:railpage={!c.ghost && (c.page?.images?.length ?? 0) > 0 && c.page?.layout === 'rail'}
          data-key={c.key}
          style="--off:{c.off}; --depth:{c.center ? 1 : 0.84}; --op:{c.center ? 1 : 0.4}; z-index:{c.center ? 6 : 4};"
          onclick={c.center ? undefined : () => navigateTo(c)}
          use:regCard={c.key}
        >
          {#if c.ghost}
            <div class="ghostbody">
              <div class="ghosticon">→</div>
              <div>Compose the next page</div>
              <div class="ghosthint">swipe · → · or click</div>
            </div>
          {:else if c.page}
            {#if c.page.images.length && c.page.layout === 'rail'}
              <div class="imglayout l-rail">
                {@render figEl(c.page.images[0], 'rail-fig')}
                <div class="rail-text">{@render body(c)}</div>
              </div>
            {:else if c.page.images.length && c.page.layout && MULTI.has(c.page.layout)}
              <div class="pad imglayout l-{c.page.layout}">{@render multiBody(c)}</div>
            {:else if c.page.images.length && c.page.layout}
              <div class="pad imglayout l-{c.page.layout}">
                {#if c.page.layout !== 'bottom'}{@render figEl(c.page.images[0], figClass[c.page.layout] ?? 'fig-top', true)}{/if}
                {@render body(c)}
                {#if c.page.layout === 'bottom'}{@render figEl(c.page.images[0], 'fig-bottom', true)}{/if}
              </div>
            {:else}
              <div class="pad">{@render body(c)}</div>
            {/if}
          {/if}
        </article>
      {/each}
    </div>

    <button class="nav left" aria-label="Previous page" disabled={dwell.cursor === 0} onclick={() => dwell.goPrev()}>‹</button>
    <button class="nav right" aria-label="Next page" disabled={!dwell.started && dwell.cursor >= dwell.pages.length - 1} onclick={() => dwell.goNext()}>›</button>

    <div class="pagebar">{dwell.cursor + 1} / {dwell.pages.length}</div>

    <div class="zoombar">
      <button aria-label="Zoom out" disabled={dwell.zoom <= 1} onclick={() => dwell.zoomOut()}>−</button>
      <button class="z" title="Reset to fit" onclick={() => dwell.resetZoom()}>{Math.round(dwell.zoom * 100)}%</button>
      <button aria-label="Zoom in" disabled={dwell.zoom >= 4} onclick={() => dwell.zoomIn()}>+</button>
    </div>

    <VolumeRail />
  {/if}
</div>

{#if pop}
  <div class="pop" style="left:{pop.x}px; top:{pop.y}px;">
    {#if dwell.canExpand}
      <button onclick={() => doExpand('simplify')}>Simplify</button>
      <button onclick={() => doExpand('more')}>✦ Expound</button>
    {/if}
    <button onclick={doNote} title="save this passage to your notes">✎ Note</button>
  </div>
{/if}

<style>
  .stage { position: relative; flex: 1 1 auto; overflow: hidden; min-height: 0; }
  .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    color: var(--meta); font-style: italic; }

  /* --peek = how much of each neighbour card shows beside the centre one (a thin
     sliver, ~0.4–0.9in, responsive). The centre card is sized to the *stage* (not
     the viewport) with room reserved for two peeks, so neighbours always show. */
  .deck { position: absolute; inset: 0; overflow: hidden; --peek: clamp(40px, 8vw, 88px); }

  /* Each card is its own scroll viewport. At zoom 1 the font is fit so nothing
     overflows; zooming in enlarges the font and the card scrolls vertically. The
     side cards are pushed out, scaled down and softly blurred — "out of focus". */
  .card {
    position: absolute; left: 50%; top: 50%;
    width: min(680px, calc(100% - 2 * var(--peek) - 24px));
    max-height: calc(100% - 22px);
    overflow: hidden;                 /* at fit the whole page shows — never scrolls */
    background: var(--pane); color: var(--ink);
    border-radius: 14px; box-shadow: 0 12px 50px #0006;
    /* Neighbours tuck flush beside the centre card so exactly --peek of each shows on
       ANY width: offset = 8% (= half-width · (1−0.84), re-aligns the scaled box to the
       centre card's edge) + --peek (the visible sliver). */
    transform: translate(calc(-50% + var(--off, 0) * (8% + var(--peek))), -50%) scale(var(--depth, 1));
    transform-origin: center center;
    opacity: var(--op, 1);
    touch-action: pan-y;
    transition: transform .4s cubic-bezier(.22,.61,.36,1), opacity .4s ease, filter .4s ease;
    will-change: transform, opacity;
  }
  .card.center { cursor: default; }
  .card.zoomed { overflow-y: auto; }    /* zoomed in → the page scrolls (and narration follows) */
  .card.overflowing { overflow-y: auto; } /* too long to fit at the floor font → scroll, don't clip */
  .card:not(.center) { filter: blur(1.4px) saturate(.85); cursor: pointer; }
  .card:not(.center) .prose { pointer-events: none; user-select: none; }
  .card::-webkit-scrollbar { width: 9px; }

  .pad { padding: 30px 38px; }
  .prose { font-family: Georgia, "Times New Roman", serif; font-size: 17px; line-height: 1.62;
    white-space: pre-wrap; min-height: 1.6em;
    text-align: justify; -webkit-hyphens: auto; hyphens: auto; }  /* book feel; size set by fit-to-page */
  .meta { margin-top: 14px; color: var(--meta); font-family: Consolas, monospace; font-size: 11px; }

  /* ---- form-aware structural styling (block-level → karaoke/clarify offset-map intact) ----
     Each paragraph is its own <p>; styling is per-paragraph, never inline, so the rendered
     text content still matches page.text char-for-char. */
  :global(.prose.form-dialogue), :global(.prose.form-qa) { white-space: normal; text-align: left; }
  /* dialogue — hanging em-dash, the two voices in distinct ink (A) vs accent (B) */
  :global(.prose.form-dialogue > p) { margin: 0 0 0.72em; padding-left: 1.15em; text-indent: -1.15em; }
  :global(.prose.form-dialogue > p:last-child) { margin-bottom: 0; }
  :global(.prose.form-dialogue .turn-b) { color: color-mix(in srgb, var(--accent) 70%, var(--ink));
    font-style: italic; }   /* second voice: accent tint + italic — distinct on any theme */
  /* FAQ — questions are prominent headings above tight answers */
  :global(.prose.form-qa > p) { margin: 0 0 0.5em; }
  :global(.prose.form-qa > p.q) { font-weight: 500; font-size: 1.06em; line-height: 1.4;
    color: color-mix(in srgb, var(--accent) 88%, var(--ink)); margin: 1.05em 0 0.3em; }
  :global(.prose.form-qa > p.q:first-child) { margin-top: 0; }
  :global(.prose.form-qa > p.a) { color: color-mix(in srgb, var(--ink) 90%, transparent); }

  /* ---- inline emphasis + headings (parsed from the model's markdown into `marks`,
     rendered as real elements; the canonical page.text stays markup-free) ---- */
  :global(.prose strong) { font-weight: 650; }
  :global(.prose em) { font-style: italic; }
  :global(.prose .rich-h1) { font-size: 1.5em; font-weight: 600; line-height: 1.2; margin: 0.1em 0 0.5em; text-align: left; }
  :global(.prose .rich-h2) { font-size: 1.22em; font-weight: 600; line-height: 1.25; margin: 1em 0 0.4em; text-align: left;
    color: color-mix(in srgb, var(--accent) 80%, var(--ink)); }
  :global(.prose .rich-h1:first-child), :global(.prose .rich-h2:first-child) { margin-top: 0; }

  /* ---- derived text-figures (no-image pages; DWELL_TEXT_FIGURES_PLAN.md) ----
     A pull-quote FLOATS right and the body wraps around it (the image `side`
     slot); it's a <figure data-narration="skip" aria-hidden> (duplicates body) →
     out of the karaoke walk + no screen-reader double-read. A drop-cap is the
     `.dropcap` class driving ::first-letter (NEVER a node extracted from text, so
     the offset map is untouched). */
  :global(.prose .tf-pullquote) {
    float: right; clear: right; width: min(44%, 17em);
    margin: 0.25em 0 0.7em 1.35em;
    font-size: 1.3em; line-height: 1.3; font-style: italic;
    color: color-mix(in srgb, var(--ink) 86%, var(--accent));
    border-top: 2px solid var(--accent); padding-top: 0.5em;
  }
  /* pageBody: .prose holds a raw text node; proseBody: .prose holds <p> blocks. */
  :global(.prose.dropcap)::first-letter,
  :global(.prose.dropcap > p:first-of-type)::first-letter {
    -webkit-initial-letter: 3; initial-letter: 3;
    font-weight: 700; color: var(--accent); margin-right: 0.08em; font-family: Georgia, serif;
  }
  @supports not (initial-letter: 3) {
    :global(.prose.dropcap)::first-letter,
    :global(.prose.dropcap > p:first-of-type)::first-letter {
      float: left; font-size: 3.1em; line-height: 0.78; padding: 0.04em 0.09em 0 0;
    }
  }

  /* ---- image layouts (figure-aware reading pages) -------------------------
     The body stays a single `.prose` text node (so karaoke/clarify/narration
     are untouched); figures are siblings around it. `:global` because the
     `l-{layout}` modifier is set dynamically. domFitImage() does the fit. */
  .card.railpage { height: calc(100% - 22px); }   /* rail needs a definite height for the full-height image */
  :global(.imglayout) { font-size: 16px; }         /* stable base for figure `em` sizes; .prose overrides its own */
  :global(.imglayout figure) { margin: 0; }
  :global(.imglayout .figframe) { overflow: hidden; border-radius: 4px; width: 100%;
    background: color-mix(in srgb, var(--ink) 9%, transparent); }   /* fill the width; max-height crops via object-fit (no aspect-ratio width-shrink → no left-align) */
  :global(.imglayout .figframe img) { display: block; width: 100%; height: 100%; object-fit: cover; }
  :global(.imglayout figcaption) { font-size: 0.76em; font-style: italic; color: var(--meta);
    line-height: 1.34; margin-top: 0.34em; }

  :global(.l-top .fig-top) { margin: 0 0 0.7em; }
  :global(.l-top .fig-top .figframe) { aspect-ratio: 16 / 7; max-height: 14em; }
  :global(.l-bottom .fig-bottom) { margin: 0.75em 0 0; }
  :global(.l-bottom .fig-bottom .figframe) { aspect-ratio: 16 / 7; max-height: 14em; }

  :global(.l-side .fig-side) { float: right; width: 42%; margin: 0.15em 0 0.55em 1.1em; }
  :global(.l-side .fig-side .figframe) { aspect-ratio: 3 / 4; max-height: 22em; }
  :global(.l-inset .fig-inset) { float: left; width: 30%; margin: 0.2em 1.1em 0.5em 0; }
  :global(.l-inset .fig-inset .figframe) { aspect-ratio: 1 / 1; }

  :global(.imglayout.l-rail) { display: grid; grid-template-columns: 38% 1fr; height: 100%; }
  :global(.l-rail .rail-fig) { position: relative; height: 100%; }
  :global(.l-rail .rail-fig .figframe) { height: 100%; border-radius: 0; }
  :global(.l-rail .rail-fig figcaption) { position: absolute; left: 0; right: 0; bottom: 0;
    margin: 0; padding: 0.5em 0.7em; font-size: 0.72em; font-style: normal; color: #fff;
    background: linear-gradient(transparent, rgba(0, 0, 0, 0.62)); }
  :global(.l-rail .rail-text) { padding: 30px 38px; height: 100%; box-sizing: border-box; overflow: hidden; }
  :global(.l-rail .rail-text.scroll) { overflow-y: auto; }   /* tall text in the rail column scrolls */

  /* ---- multi-image layouts (prose = <p>s, figures interspersed) ---- */
  :global(.imglayout .prose > p) { margin: 0 0 0.7em; }
  :global(.imglayout .prose > p:last-child) { margin-bottom: 0; }
  /* magazine — 3-column body + a centred feature band spanning the columns */
  :global(.imglayout.l-magazine .prose.mag-cols) { columns: 3; column-gap: 20px; }
  :global(.l-magazine .mag-cols > p) { margin: 0 0 0.62em; }
  :global(.l-magazine .mag-mid) { margin: 0.2em 0 0.7em; break-inside: avoid; }  /* a column-WIDTH block in the flow, not a full-width band */
  :global(.l-magazine .mag-mid .figframe) { aspect-ratio: 3 / 4; }                /* thin portrait — fits one column */
  /* diagonal — image top-right + a second partway-down-left */
  :global(.l-diagonal .fig-a) { float: right; width: 42%; margin: 0.1em 0 0.5em 1.1em; }
  :global(.l-diagonal .fig-a .figframe) { aspect-ratio: 3 / 2; max-height: 13em; }
  :global(.l-diagonal .fig-b) { float: left; clear: left; width: 44%; margin: 0.45em 1.1em 0.55em 0; }
  :global(.l-diagonal .fig-b .figframe) { aspect-ratio: 1 / 1; max-height: 14em; }
  /* mosaic — wide banner + two floated details */
  :global(.l-mosaic .banner) { margin: 0 0 0.65em; }
  :global(.l-mosaic .banner .figframe) { aspect-ratio: 21 / 6; max-height: 9em; }
  :global(.l-mosaic .mbody) { overflow: hidden; }
  :global(.l-mosaic .fig-2) { float: right; width: 35%; margin: 0.1em 0 0.5em 1em; }
  :global(.l-mosaic .fig-2 .figframe) { aspect-ratio: 4 / 3; }
  :global(.l-mosaic .fig-3) { float: left; clear: left; width: 30%; margin: 0.35em 1em 0.5em 0; }
  :global(.l-mosaic .fig-3 .figframe) { aspect-ratio: 1 / 1; }

  .card.ghost {
    display: flex; align-items: center; justify-content: center;
    min-height: 300px;
    background: color-mix(in srgb, var(--pane) 55%, transparent);
    border: 1.5px dashed var(--border); box-shadow: none;
  }
  .ghostbody { text-align: center; color: var(--meta); font-family: Georgia, serif; }
  .ghosticon { font-size: 36px; opacity: .65; }
  .ghosthint { font-size: 11px; margin-top: 8px; opacity: .7; font-family: Consolas, monospace; }

  /* Edge arrows — fade in on hover (and stay faintly visible on touch devices). */
  .nav {
    position: absolute; top: 50%; transform: translateY(-50%); z-index: 20;
    width: 42px; height: 66px; padding: 0; font-size: 26px; line-height: 1;
    display: flex; align-items: center; justify-content: center;
    background: color-mix(in srgb, var(--panel) 78%, transparent);
    color: var(--fg); border: 1px solid var(--border); border-radius: 11px;
    opacity: 0; transition: opacity .18s ease;
  }
  .nav.left { left: 12px; }
  .nav.right { right: 12px; }
  .stage:hover .nav { opacity: .9; }
  .nav:disabled { opacity: 0 !important; }
  @media (hover: none) { .nav { opacity: .55; } }

  .pagebar, .zoombar {
    position: absolute; bottom: 12px; z-index: 20;
    background: color-mix(in srgb, var(--panel) 82%, transparent);
    border: 1px solid var(--border); border-radius: 10px;
    box-shadow: 0 4px 16px #0006; color: var(--meta);
  }
  .pagebar { left: 14px; padding: 6px 10px; font-family: Consolas, monospace; font-size: 12px;
    font-variant-numeric: tabular-nums; }
  .zoombar { right: 14px; display: flex; gap: 2px; padding: 3px; }
  .zoombar button { padding: 5px 9px; background: transparent; color: var(--fg); }
  .zoombar .z { min-width: 48px; font-variant-numeric: tabular-nums; }

  .pop { position: fixed; z-index: 50; display: flex; gap: 2px; background: var(--accent);
    padding: 2px; border-radius: 8px; box-shadow: 0 4px 18px #0008; }
  .pop button { font-size: 12px; padding: 5px 9px; }

  @media (max-width: 560px) {
    .deck { --peek: clamp(26px, 7vw, 48px); }   /* smaller sliver so the centre card keeps width */
    .card { max-height: calc(100% - 12px); }
    .pad { padding: 22px 20px; }
    .nav { width: 36px; height: 56px; }
  }
</style>
