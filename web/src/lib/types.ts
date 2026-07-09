// Shared types mirroring the FastAPI server (dwell_server.py) payloads.

export interface VaultInfo {
  path: string; name: string; nodes: number; has_voice: boolean;
  topic?: string;        // one-line blurb for the hero card
  sources?: number;      // immutable source-doc count (a card stat)
  has_cover?: boolean;   // an explicit cover image exists (else gradient card)
  imported?: boolean;    // a registered external vault (outside the library root)
}
export interface MenuItem { key: string; label: string; hint: string; }
// A user-managed LLM endpoint (Models & Keys). The api_key never reaches the client.
export interface Endpoint {
  id: string; name: string; base_url: string; provider: string;
  has_key: boolean; enabled: boolean; models: string[];
}
// A source document listed in the knowledge-base detail view (format variants merged).
// status: learned (in the ingest registry) | pending (awaiting a build: new upload,
// manifest link, research prompt) | untracked (pre-registry legacy file).
export interface VaultSource { name: string; kind: string; exts: string[]; status?: 'learned' | 'pending' | 'untracked' | ''; }
// A quick text look inside one raw source (GET /learn/peek).
export interface SourcePeek { name: string; kind: string; text: string; note: string; truncated: boolean; }

// Learn (vault-builder) intake — the curate-step state of a draft knowledge base.
export interface LearnFile { id: string; name: string; ext: string; size: number; status?: 'new' | 'duplicate'; }
export interface LearnLink { id: string; name: string; }
export interface LearnSources { files: LearnFile[]; links: LearnLink[]; prompt: string; topic: string; }
// A source in the ingest-swarm build worklist.
export interface BuildSource { id: string; name: string; kind: string; status: string; }
export interface VoiceInfo {
  vault_voices: string[]; presets: string[];
  default: string | null; current: string; current_id: string;
}
// A reader-saved highlight: the passage verbatim + a snapshot of the exact page it came
// from, so the back-link returns to that EXACT text (never a regenerated version).
export interface Note {
  id: string;
  text: string;          // the highlighted passage, as-is
  node: string;          // source node id
  title: string;         // source page title (display)
  vault: string;         // which vault it belongs to (notes are scoped per vault)
  ts: number;            // saved-at epoch ms (sort key)
  page: PageView;        // verbatim snapshot of the page (for the precise-instance jump)
}
export interface SessionInfo {
  session_id: string; vault: string; topic: string; mode: string;
  nodes: number; embed_label: string; provider: string; model: string | null;
  dry: boolean; init_error: string | null; menu: MenuItem[]; voices: VoiceInfo;
  level: string; levels: string[];
  form: string; forms: string[];
  language: string; languages: string[];
  notes: string[];
}
export interface Branch {
  plan_id: string; label: string; mode: string;
  node: string; title: string; ready: boolean; leap: boolean;
}
export interface PageStart {
  node: string; title: string; mode: string; steer_bucket: string; diffusing: boolean;
  form?: string;          // output form (article/guided/qa/dialogue) — drives reader styling
}
export interface PageFrame { text: string; }
// A figure resolved for a page (server: dwell_server `_page_images`).
export interface PageFigure {
  url: string;            // /asset?session=…&path=… (proxied to the engine)
  caption: string;
  attribution: string;
  w: number;
  h: number;
  aspect: 'portrait' | 'landscape' | 'square' | 'wide';
}
// A derived TEXT-figure resolved for a page (server: text_figures.choose_text_figure).
// A "figure" generalizes to image-OR-text; this fills a slot on a no-image page.
export interface TextFigureView {
  kind: string;                 // 'pull-quote' | 'drop-cap' | 'stepped-list' | … (TextFigureId subset implemented in the reader)
  slot: string;                 // 'side' (float) | 'body' (CSS, e.g. drop-cap) | 'panel' (inset block) | …
  payload: { text?: string; steps?: string[] };   // pull-quote: the verbatim line; stepped-list: the derived moves; drop-cap: empty
}

// ---- Curated Paths (DWELL_PATHS.md) ----
// A curated Path available for a vault (server: /paths → _meta/paths/*.json).
export interface PathInfo { id: string; title: string; goal: string; gates: number; }
// Live progress while walking a Path's frozen spine (server: _path_block on every page).
export interface PathProgress {
  id: string; title: string; goal?: string;
  gate: number; gates: number; complete: boolean;
  missing?: string[];
}

export interface PageDone {
  text: string; node: string; title: string; mode: string; marker: string;
  recap: string; cost: number; branches: Branch[]; steer_bucket: string;
  sources: string[]; form?: string;
  images: PageFigure[]; layout: LayoutId | null;   // image-aware reading layout
  text_figure?: TextFigureView | null;             // derived text-figure (no-image pages)
  path?: PathProgress;                             // present only while walking a curated Path
  dream?: number;                                  // active creativity dial (0..1) at render time
}
export interface ExpandDone { text: string; cost: number; }
export interface MissedPair { a: string; b: string; sim: number; title_a: string; title_b: string; }

// A timeline event from the enrichment temporal sidecar (server: /timeline → enrich).
export interface TimelineEvent { year: number; text: string; kind: string; page: string; title: string; conf: number; }
export interface TimelineData { available: boolean; count: number; min_conf?: number; topic?: string; note?: string; events: TimelineEvent[]; }

// A retrieval-practice quiz question (mirrors dwell.py Renderer.quiz output).
export interface QuizPair { left: string; right: string; }
export interface QuizQuestion {
  type: 'choice' | 'truefalse' | 'cloze' | 'recall' | 'matching';
  q: string;
  why?: string;
  evidence?: string;    // verbatim quote from the pages that holds the answer (for highlighting)
  options?: string[];   // choice: the answer options
  correct?: number;     // choice: index of the correct option
  tf?: boolean;         // truefalse: the correct answer
  blank?: string;       // cloze: the missing text
  ideal?: string;       // recall: the model answer
  pairs?: QuizPair[];   // matching: term ↔ description pairs
}

// ---- Image layouts (the figure-aware reading page) ----
// A named, pre-composed arrangement of body text + 1–3 vault figures inside a
// fixed page card. Implemented in layouts.css; rendered by PageLayout.svelte.
export type LayoutId =
  | 'top'       // full-width figure flush to the top, text below (safe default)
  | 'bottom'    // mirror of top — figure pinned to the card bottom
  | 'side'      // figure floated to one side near the top, text wraps
  | 'inset'     // small corner figure, text-dominant page
  | 'diagonal'  // 2 images: top-right + partway-down-left (diagonal eye-path)
  | 'magazine'  // image banner + body text in 3 balanced columns (CSS multicol)
  | 'rail'      // full-height image rail beside a text column (absorbs tall images)
  | 'mosaic'    // top banner + two floated detail images (3 images)
  | 'hero';     // full-bleed image with text overlaid in a scrim panel

// Emphasis span over the CLEAN page text (char range). Parsed from the renderer's
// light markdown (see marks.ts) and rendered as real <strong>/<em>/heading elements;
// never embedded in page.text, so TTS + the karaoke offset-map stay markup-free.
export interface Mark { start: number; end: number; kind: 'strong' | 'em' | 'h1' | 'h2'; }

// ---- Text-figures (the figure-aware reading page, text edition) ----
// Derived TEXT-figures that fill the SAME slots as images (DWELL_TEXT_FIGURES_PLAN.md):
// a pull-quote, key-takeaways box, callout, stepped list, etc. Each renders as a
// `<figure data-narration="skip">` so it stays out of the karaoke/clarify offset walk
// (same invariant as images + marks). Tier-1 = derivable from one page (build first);
// Tier-2 = gated on the universal-ingest enrichment (typed edges / claims / terms / dates).
export type TextFigureId =
  // Tier 1 — derivable from a single page now
  | 'kicker'         // eyebrow/teaser label above the title
  | 'headline-stack' // kicker + display title + deck, segmented
  | 'deck'           // standfirst — 1–2-sentence framing under the title
  | 'tldr'           // one-sentence ultra-compression (the length axis, visible)
  | 'key-takeaways'  // ⭐ the page's 2–5 major points (strongest UX evidence)
  | 'callout'        // typed admonition box (note·tip·key-insight·question·caution·quote)
  | 'pull-quote'     // the single most striking body line, lifted into the margin
  | 'block-quote'    // a verbatim passage, in-flow + attributed
  | 'stepped-list'   // enumerable/sequential content as a numbered panel
  | 'comparison'     // two juxtaposed items (A vs B)
  | 'accordion'      // a deep-dive behind a toggle
  | 'read-time'      // est. time + progress (driven by narration time)
  | 'sidenote'       // a gloss keyed to one line, in the margin (flow-safe)
  | 'drop-cap'       // raised/dropped initial via ::first-letter (no node extraction)
  | 'raised-initial' // alternative opening device
  // Tier 2 — needs the ingest enrichment
  | 'big-number'     // a claim's number + referent, as an F-pattern landmark
  | 'see-also'       // related nodes from the wikilink graph + centrality
  | 'source-strip'   // synthesized-from-N-sources + grounded trust strip
  | 'glossary'       // the page's key terms + glosses
  | 'definition'     // an inline term with a focusable tooltip gloss
  | 'timeline';      // a chronology strip (really a Tier-2 *view*)

export type CalloutKind = 'note' | 'tip' | 'key-insight' | 'question' | 'caution' | 'quote';

// The derived payload a text-figure draws on. The engine will fill the relevant
// field(s) per page; the lab hand-authors them. All optional — a figure reads only
// the field(s) it needs.
export interface TextFigureData {
  title?: string;
  kicker?: string;
  deck?: string;
  tldr?: string;
  takeaways?: string[];
  callout?: { kind: CalloutKind; label?: string; text: string };
  quote?: { text: string; cite?: string };        // pull-quote / block-quote
  steps?: string[];
  comparison?: { aTitle: string; a: string; bTitle: string; b: string };
  accordion?: { summary: string; detail: string };
  readTime?: { mins: number; progress: number };   // progress 0..1, from narration time
  sidenote?: { afterPara: number; marker: string; text: string };
  bigNumber?: { value: string; label: string };
  seeAlso?: { title: string; note?: string }[];
  sources?: { count: number; grounded: boolean };
  glossary?: { term: string; def: string }[];
  definition?: { term: string; def: string; afterPara: number };
  timeline?: { when: string; what: string }[];
}

export interface PageImage {
  src: string;          // resolved URL the browser can load
  alt: string;
  caption?: string;
  aspect?: 'portrait' | 'landscape' | 'square' | 'wide';
  w?: number;
  h?: number;
}

// A rendered page as the UI tracks it (the deck unit).
export interface PageView {
  key: number;
  text: string;
  node: string;
  title: string;
  mode: string;          // open | dwell | move
  marker: string;        // live | coast
  steer_bucket: string;
  sources: string[];     // source docs the node was built from
  images: PageFigure[];  // resolved figures for this page (may be empty)
  layout: LayoutId | null; // chosen reading layout when images present
  form: string;          // output form rendered (article/guided/qa/dialogue) — drives form styling
  marks: Mark[];         // emphasis spans (bold/italic/headings) over `text` — parsed from markdown
  textFigure: TextFigureView | null;  // derived text-figure for a no-image page (pull-quote/drop-cap/…)
  live: boolean;         // currently streaming
}
