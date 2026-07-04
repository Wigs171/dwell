// The Dwell app store — Svelte 5 runes. A single shared instance holds all state
// and orchestrates the engine through the API. Components read/write `dwell.*`.
//
// It mirrors the tkinter driver: a session loads the Brain; `begin()` opens a
// reading thread; `advance()` streams one page (first | flow | a chosen branch),
// appending it to the endless scroll; `steer()` bends the next page; `expand()`
// reworks a passage in place. Branches + cost arrive bundled in each page's done.
import { api } from './api';
import { writeTheme, themeByName, isLightColor, DEFAULT_THEME, THEMES, THEME_BG, THEME_CYCLE, type Theme } from './themes';
import { applyBgPattern, setEffectColor, setEffectIntensity, setEffectSize, applyFrosted, setCyclePalette } from './background';
import { AudioNarrator } from './audio';
import type { Branch, BuildSource, LearnSources, Mark, MissedPair, Note, PageView, PathInfo, PathProgress, QuizQuestion, SessionInfo, VaultInfo, VaultSource, VoiceInfo } from './types';
import { parseMarks } from './marks';

const ls = {
  get: (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};
const num = (s: string | null, fallback: number): number => { const n = parseFloat(s ?? ''); return isNaN(n) ? fallback : n; };

// Char offset of the sentence containing `frac` of the way through `text` — used to
// resume narration near the reader's place after a page is re-pitched to a new level.
function sentenceStartNear(text: string, frac: number): number {
  const target = Math.floor(frac * text.length);
  if (target <= 0) return 0;
  const re = /[.!?]["')\]]?\s+/g;
  let start = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const boundary = m.index + m[0].length;
    if (boundary > target) break;
    start = boundary;
  }
  return start;
}

const ZOOM_MIN = 1, ZOOM_MAX = 4;   // 1 = fit the whole page; >1 enlarges + scrolls

export const QUIZ_TYPE_KEYS = ['choice', 'truefalse', 'cloze', 'recall', 'matching'] as const;

// Reading level — one vault, re-pitched to the reader (a separate axis from voice).
export const READING_LEVELS: { value: string; label: string }[] = [
  { value: 'general', label: 'Default' },
  { value: 'elementary', label: 'Elementary' },
  { value: 'middle', label: 'Middle school' },
  { value: 'high', label: 'High school' },
  { value: 'college', label: 'College' },
  { value: 'scholar', label: 'Scholar' },
];

// Output form — the rhetorical SHAPE of the page (same vault, re-pitched in place).
// A separate axis from level + voice; "tutorial" is just `steps`.
export const OUTPUT_FORMS: { value: string; label: string }[] = [
  { value: 'article', label: 'Article' },
  { value: 'guided', label: 'Guided tour' },
  { value: 'qa', label: 'Q&A' },
  { value: 'dialogue', label: 'Dialogue' },
  { value: 'story', label: 'Story' },
  { value: 'tutorial', label: 'Tutorial' },
  { value: 'brief', label: 'Brief' },
  { value: 'case', label: 'Case study' },
  { value: 'interview', label: 'Interview' },
  { value: 'debate', label: 'Debate' },
  { value: 'epistolary', label: 'Letters' },
  { value: 'chronicle', label: 'Chronicle' },
];

// Output language — the page's MEDIUM (a separate axis from voice/form/level). The same
// vault is rendered in the target language, re-pitched in place. 'source' = no translation.
// Free text also works server-side, so any language the model knows is reachable.
export const LANGUAGES: { value: string; label: string }[] = [
  { value: 'source', label: 'English' },
  { value: 'spanish', label: 'Spanish' },
  { value: 'french', label: 'French' },
  { value: 'german', label: 'German' },
  { value: 'italian', label: 'Italian' },
  { value: 'portuguese', label: 'Portuguese' },
  { value: 'mandarin', label: 'Mandarin' },
  { value: 'japanese', label: 'Japanese' },
  { value: 'korean', label: 'Korean' },
  { value: 'arabic', label: 'Arabic' },
  { value: 'hindi', label: 'Hindi' },
  { value: 'russian', label: 'Russian' },
];

// Claude models selectable per ingest role (Learn settings). '' = pipeline default
// (Sonnet 4.6). Ingest runs on the Anthropic API; this picks the model, not the provider.
export const MODELS: { value: string; label: string }[] = [
  { value: '', label: 'Default (Sonnet 4.6)' },
  { value: 'claude-opus-4-8', label: 'Opus 4.8 — best, priciest' },
  { value: 'claude-sonnet-4-6', label: 'Sonnet 4.6 — balanced' },
  { value: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5 — cheapest, fastest' },
];
// Compact label for the build panel ('' = the pipeline default, Sonnet 4.6).
const MODEL_SHORT_MAP: Record<string, string> = {
  '': 'Sonnet 4.6', 'claude-opus-4-8': 'Opus 4.8',
  'claude-sonnet-4-6': 'Sonnet 4.6', 'claude-haiku-4-5-20251001': 'Haiku 4.5',
};
export const modelShort = (v: string) => MODEL_SHORT_MAP[v] ?? v ?? 'Sonnet 4.6';

type NodeRow = { id: string; title: string; centrality: number; seen: number };
type AdvanceOpts = { action: string; plan_id?: string; start?: string; seed?: string; path_id?: string };
// A backed-out vault's live reading state, kept in memory so re-entering is instant:
// the server session stays alive (its Navigator is right where you left it) and the
// rendered pages/cursor/branches are restored verbatim — no brain reload.
type VaultStash = {
  session: SessionInfo; voices: VoiceInfo | null; voice: string; level: string; form: string; language: string;
  started: boolean; pages: PageView[]; cursor: number; branches: Branch[];
  cost: number; popular: NodeRow[]; allNodes: NodeRow[];
};

class DwellStore {
  // --- chrome / form (pre-load) ---
  vaults = $state<VaultInfo[]>([]);
  vaultStash = $state<Record<string, VaultStash>>({});   // backed-out vaults kept warm for instant re-entry
  vaultRoot = $state('');
  vaultPath = $state('');
  engine = $state(''); // '' (server default) | 'mercury' | 'anthropic'
  dry = $state(false);

  // --- session ---
  session = $state<SessionInfo | null>(null);
  voices = $state<VoiceInfo | null>(null);
  voice = $state('clean');
  level = $state('general');         // reading/scholarly level (see READING_LEVELS)
  form = $state('article');          // output form / rhetorical shape (see OUTPUT_FORMS)
  language = $state('source');       // output language / medium (see LANGUAGES)
  loading = $state(false);

  // --- theme + sidebar (Odysseus-style chrome) ---
  theme = $state(DEFAULT_THEME);
  customThemes = $state<Theme[]>([]);
  density = $state('comfortable');
  sidebarOpen = $state(true);
  collapsed = $state<Record<string, boolean>>({});
  settingsOpen = $state(false);
  page = $state<'home' | 'learn' | 'read'>('home');   // top-level view (Home / Learn / Read)
  pendingAxes = $state(false);       // an axis (voice/level/form/language) changed but the
                                     // page isn't re-rendered yet — batched, see applyAxes()

  // --- notes (reader-saved highlights; a movable window, persisted per vault) ---
  notes = $state<Note[]>([]);
  notesOpen = $state(false);
  // The passage to highlight after a note jump: {page key, the saved excerpt}. The Reader
  // locates the span and paints it (CSS Custom Highlight). Keyed by page → only that page.
  noteHighlight = $state<{ key: number; text: string } | null>(null);

  // --- animated background (Odysseus) ---
  bgPattern = $state('none');
  bgIntensity = $state(1);
  bgSize = $state(1);
  bgEffectColor = $state('');
  frosted = $state(false);

  // --- audio narration ---
  narrator = new AudioNarrator();
  narrate = $state(false);            // auto-read each page as it finishes
  narrating = $state(false);          // audio currently playing
  ttsAvailable = $state(false);
  ttsVoices = $state<string[]>([]);
  ttsVoice = $state('af_heart');
  ttsSpeed = $state(1);
  ttsVolume = $state(1);              // narration volume 0–1
  spoken = $state<{ key: number; cs: number; ce: number } | null>(null);  // word being read
  narratingKey: number | null = null;                                     // page being read
  queued = $state<{ kind: 'advance'; opts: AdvanceOpts } | { kind: 'begin'; seed: string } | null>(null);
  popular = $state<NodeRow[]>([]);
  allNodes = $state<NodeRow[]>([]);   // full list, for search
  paths = $state<PathInfo[]>([]);     // curated Paths for this vault (sidebar)
  pathProgress = $state<PathProgress | null>(null);   // set while walking a Path, else null
  dream = $state(0);                  // creativity dial 0..1 (active render value; server-authoritative)
  query = $state('');

  get allThemes(): Theme[] { return [...THEMES, ...this.customThemes]; }
  get filteredNodes(): NodeRow[] {
    const q = this.query.trim().toLowerCase();
    if (!q) return [];
    return this.allNodes.filter((n) => n.title.toLowerCase().includes(q)).slice(0, 40);
  }

  // --- reading knobs ---
  wander = $state(0.4);
  diffuse = $state(true);
  autoflow = $state(false); // recliner: after the voice finishes a page, read the next

  // --- reading state ---
  started = $state(false);
  busy = $state(false);
  pages = $state<PageView[]>([]);
  cursor = $state(0);                 // index of the focused page card (the deck's centre)
  zoom = $state(1);                   // centre-card zoom; 1 = fit the whole page to the card
  branches = $state<Branch[]>([]);
  cost = $state(0);
  status = $state('');   // empty until a knowledge base is chosen, then actions fill it
  statusErr = $state(false);
  missed = $state<{ embed_label: string; pairs: MissedPair[] } | null>(null);
  timeline = $state<import('./types').TimelineData | null>(null);

  // --- quizzes (retrieval practice — a quiz every `quizEvery` pages) ---
  quizzesOn = $state(true);
  quizEvery = $state(5);              // a quiz every N pages
  quizCount = $state(5);             // how many questions per quiz (3–25)
  quizTypes = $state<Record<string, boolean>>({ choice: true, truefalse: true, cloze: true, recall: true, matching: true });
  quizDueAt = $state(5);              // checkpoint: quiz when pages.length reaches this
  quizOpen = $state(false);
  quizLoading = $state(false);
  quiz = $state<QuizQuestion[] | null>(null);
  private pendingAdvance: AdvanceOpts | null = null;   // the move a quiz checkpoint interrupted

  #key = 0;

  get sid(): string | null { return this.session?.session_id ?? null; }
  get canExpand(): boolean { return this.started && !this.busy && !(this.session?.dry ?? true); }

  setStatus(msg: string, err = false) { this.status = msg; this.statusErr = err; }

  // Set a page's body from the renderer's RAW markdown: store the CLEAN text (what TTS
  // narrates + what the karaoke offset-map aligns to) plus the parsed emphasis marks.
  private applyText(v: { text: string; marks: Mark[] }, raw: string) {
    const { text, marks } = parseMarks(raw);
    v.text = text; v.marks = marks;
  }

  // --- card-deck navigation (PDF-reader page flipping) ---
  // The deck shows previous · current · next. Flipping back/forward through pages
  // already read is instant; flipping forward past the live edge composes the next
  // page (TTS-gated, like the recliner). The focused card is `pages[cursor]`.
  get atEdge(): boolean { return this.cursor >= this.pages.length - 1; }
  get focused(): PageView | undefined { return this.pages[this.cursor]; }
  get quizDue(): boolean {
    return this.quizzesOn && this.started && !this.quizOpen && this.pages.length >= this.quizDueAt;
  }
  get enabledQuizTypes(): string[] { return QUIZ_TYPE_KEYS.filter((t) => this.quizTypes[t]); }
  goTo(i: number) { this.cursor = Math.max(0, Math.min(i, this.pages.length - 1)); }
  goPrev() { if (this.cursor > 0) this.cursor -= 1; }
  goNext() {
    if (this.cursor < this.pages.length - 1) { this.cursor += 1; return; }
    if (!this.started) return;                  // a note preview has no live edge to compose
    this.requestAdvance({ action: 'auto' });   // at the live edge → compose next (advance() runs the quiz gate)
  }

  // --- zoom (fit-to-page ↔ enlarge) ---
  setZoom(z: number) {
    const v = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(z * 100) / 100));
    this.zoom = v;
    ls.set('dwell-zoom', String(v));
  }
  zoomIn() { this.setZoom(this.zoom + 0.2); }
  zoomOut() { this.setZoom(this.zoom - 0.2); }
  resetZoom() { this.setZoom(1); }

  setTheme(name: string, resetBg = true) {
    const t = themeByName(name, this.customThemes);
    writeTheme(t);
    this.theme = t.name;
    ls.set('dwell-theme', t.name);
    setCyclePalette((THEME_CYCLE[t.name] || []).join(','));   // effects cycle this theme's palette

    if (resetBg) {                       // adopt this theme's signature background
      const d = THEME_BG[t.name] ?? { pattern: 'none' };
      this.bgPattern = d.pattern;
      this.bgIntensity = d.intensity ?? 1;
      this.bgEffectColor = d.color ?? '';
      this.frosted = d.frosted ?? false;
      this.applyBg();
      this.persistBg();
    }
  }
  previewTheme(t: Theme) { writeTheme(t); }                 // live, not persisted
  revertTheme() { this.setTheme(this.theme, false); }       // re-apply colors only

  saveCustomTheme(t: Theme) {
    const theme: Theme = { ...t, custom: true, light: isLightColor(t.bg) };
    this.customThemes = [...this.customThemes.filter((x) => x.name !== theme.name), theme];
    this.persistCustom();
    this.setTheme(theme.name, false);    // keep the current background
  }

  // --- background controls ---
  applyBg() {
    setEffectColor(this.bgEffectColor);
    setEffectIntensity(this.bgIntensity);
    setEffectSize(this.bgSize);
    applyBgPattern(this.bgPattern);
    applyFrosted(this.frosted);
  }
  persistBg() {
    ls.set('dwell-bg-pattern', this.bgPattern);
    ls.set('dwell-bg-intensity', String(this.bgIntensity));
    ls.set('dwell-bg-size', String(this.bgSize));
    ls.set('dwell-bg-color', this.bgEffectColor);
    ls.set('dwell-frosted', this.frosted ? '1' : '0');
  }
  setBgPattern(p: string) { this.bgPattern = p; applyBgPattern(p); this.persistBg(); }
  setBgIntensity(v: number) { this.bgIntensity = v; setEffectIntensity(v); this.persistBg(); }
  setBgSize(v: number) { this.bgSize = v; setEffectSize(v); this.persistBg(); }
  setBgEffectColor(c: string) { this.bgEffectColor = c; setEffectColor(c); this.persistBg(); }
  setFrosted(on: boolean) { this.frosted = on; applyFrosted(on); this.persistBg(); }

  // --- audio narration ---
  async initNarration() {
    this.narrator.onState = (p) => { this.narrating = p; };
    this.narrator.onWord = (cs, ce) => { this.spoken = cs < 0 ? null : { key: this.narratingKey ?? -1, cs, ce }; };
    this.narrator.onEnd = () => this.onNarrationEnd();
    // early-narration seam: remainder queued while the opening plays

    await this.narrator.init();
    this.ttsAvailable = this.narrator.available;
    this.ttsVoices = this.narrator.voices.length ? this.narrator.voices : [this.narrator.defaultVoice];
    this.narrate = ls.get('dwell-narrate') === '1';
    this.autoflow = ls.get('dwell-autoflow') === '1';
    // one-time upgrade: the old default (bm_george, grade C) was retired for the
    // grade-A blend; move anyone still on it unless they re-pick it themselves.
    if (ls.get('dwell-tts-voice') === 'bm_george' && !ls.get('dwell-tts-v2')) {
      ls.set('dwell-tts-v2', '1'); ls.set('dwell-tts-voice', this.narrator.defaultVoice);
    }
    this.ttsVoice = ls.get('dwell-tts-voice') || this.narrator.defaultVoice;
    this.ttsSpeed = num(ls.get('dwell-tts-speed'), 1);
    this.ttsVolume = num(ls.get('dwell-tts-volume'), 1);
    this.narrator.setVolume(this.ttsVolume);
  }
  toggleNarrate() {
    this.narrate = !this.narrate;
    ls.set('dwell-narrate', this.narrate ? '1' : '0');
    if (this.narrate) this.playCurrent(); else this.narrator.stop();
  }
  setTtsVoice(v: string) { this.ttsVoice = v; ls.set('dwell-tts-voice', v); }
  setTtsSpeed(v: number) { this.ttsSpeed = v; ls.set('dwell-tts-speed', String(v)); }
  setTtsVolume(v: number) { this.ttsVolume = v; ls.set('dwell-tts-volume', String(v)); this.narrator.setVolume(v); }
  playCurrent() {
    const p = this.pages[this.cursor] ?? this.pages[this.pages.length - 1];
    if (!p?.text) return;
    this.narratingKey = p.key;
    void this.narrator.speak(p.text, this.ttsVoice, this.ttsSpeed);
  }
  // The single Play/Pause control = the recliner. Play reads the current page
  // aloud and, when it finishes, continues to the next (a queued direction if you
  // picked one while it read, otherwise the default flow path). Pause pauses audio.
  togglePlay() {
    if (this.narrating) { this.narrator.pause(); return; }
    if (!this.narrate) { this.narrate = true; ls.set('dwell-narrate', '1'); }
    if (!this.autoflow) { this.autoflow = true; ls.set('dwell-autoflow', '1'); }
    this.narrator.resume();                   // resume if paused
    if (!this.narrating) this.playCurrent();  // else start the current page
  }

  // Select-to-clarify: opening the action popover pauses narration (a natural
  // "wait — this bit"); dismissing it without acting resumes. Idempotent + safe.
  // TTS-gated navigation: while a page is actively being read, queue the move and
  // run it when narration finishes; otherwise act immediately.
  private narrationActive(): boolean { return this.narrate && this.narrating; }

  // ---- StreamDiffusion-V2 borrowings: early narration + SLO ----
  #earlyKey: number | null = null;   // page key narrated early (opening only)
  #earlyLen = 0;                     // chars of the opening already spoken
  #earlyRemainder: { key: number; text: string; offset: number } | null = null;
  #stablePrev = '';
  #stableCount = 0;

  // While a page is still diffusing, watch its OPENING: once the first ~260 chars
  // are byte-stable across 3 consecutive frames, start narrating them (time-to-
  // first-audio ~= V2's time-to-first-frame). The remainder is spoken from the
  // same offsets when the page lands, so karaoke stays aligned.
  private maybeEarlyNarrate(page: { key: number; text: string }, raw: string) {
    if (!this.narrate || !this.narrator.available || this.narrating) return;
    if (this.#earlyKey === page.key) return;
    const cut = raw.slice(0, 320).lastIndexOf('. ');
    if (cut < 120) { this.#stablePrev = ''; this.#stableCount = 0; return; }
    const opening = raw.slice(0, cut + 1);
    if (opening === this.#stablePrev) this.#stableCount++;
    else { this.#stablePrev = opening; this.#stableCount = 1; }
    if (this.#stableCount < 3) return;
    this.#earlyKey = page.key;
    this.#earlyLen = opening.length;
    this.narratingKey = page.key;
    void this.narrator.speak(opening, this.ttsVoice, this.ttsSpeed);
  }

  // Page landed: if we spoke the opening early, queue (or speak) the remainder at
  // the right character offset. Diffusion may have revised the opening after we
  // started — accept the seam; the remainder follows the FINAL text.
  // Returns TRUE if early narration owns this page's audio → the caller must NOT also
  // playCurrent() (that would re-read the whole page from the top — the jump-back bug).
  private finishEarlyNarration(page: { key: number; text: string }): boolean {
    this.#stablePrev = ''; this.#stableCount = 0;
    if (this.#earlyKey !== page.key) return false;   // we never narrated this page early
    this.#earlyKey = null;
    const rem = page.text.slice(this.#earlyLen);
    if (rem.trim()) {
      const item = { key: page.key, text: rem, offset: this.#earlyLen };
      if (this.narrator.isPlaying) this.#earlyRemainder = item;
      else void this.narrator.speak(item.text, this.ttsVoice, this.ttsSpeed, item.offset);
    }
    return true;                                       // early narration handled it
  }

  requestAdvance(opts: AdvanceOpts) {
    if (this.narrationActive()) { this.queued = { kind: 'advance', opts }; this.setStatus('⏳ continues when narration finishes'); }
    else void this.advance(opts);
  }
  requestBeginAt(seed: string) {
    if (this.narrationActive()) { this.queued = { kind: 'begin', seed }; this.setStatus('⏳ jumps when narration finishes'); }
    else this.beginAt(seed);
  }
  private onNarrationEnd() {
    if (this.#earlyRemainder) {            // finish the page before any advance
      const r = this.#earlyRemainder; this.#earlyRemainder = null;
      void this.narrator.speak(r.text, this.ttsVoice, this.ttsSpeed, r.offset);
      return;
    }
    const q = this.queued;
    this.queued = null;
    this.spoken = null;
    if (q) { if (q.kind === 'advance') void this.advance(q.opts); else this.beginAt(q.seed); return; }
    // Recliner: advance only if Auto is on, the voice is on, and the page actually
    // read (didSpeak) — so a failed/empty narration can't cascade into a runaway.
    if (this.narrate && this.autoflow && this.started && !this.busy && this.narrator.spoke) {
      void this.advance({ action: 'auto' });   // advance() runs the quiz gate
    }
  }
  deleteCustomTheme(name: string) {
    this.customThemes = this.customThemes.filter((x) => x.name !== name);
    this.persistCustom();
    if (this.theme === name) this.setTheme(DEFAULT_THEME);
  }
  private persistCustom() {
    try { localStorage.setItem('dwell-custom-themes', JSON.stringify(this.customThemes)); } catch { /* ignore */ }
  }

  setDensity(d: string) {
    this.density = d;
    document.documentElement.dataset.density = d;
    try { localStorage.setItem('dwell-density', d); } catch { /* ignore */ }
  }

  toggleSidebar() { this.sidebarOpen = !this.sidebarOpen; }
  toggleSection(key: string) { this.collapsed = { ...this.collapsed, [key]: !this.collapsed[key] }; }

  // ---- knowledge-base detail window (opens on a card click, before loading) ----
  vaultDetail = $state<VaultInfo | null>(null);
  vaultDetailSources = $state<VaultSource[]>([]);
  vaultDetailSourcesLoading = $state(false);

  async openVaultDetail(v: VaultInfo) {
    this.vaultDetail = v;
    this.vaultDetailSources = [];
    this.vaultDetailSourcesLoading = true;
    try { this.vaultDetailSources = (await api.vaultSources(v.path)).sources; }
    catch { this.vaultDetailSources = []; }
    finally { this.vaultDetailSourcesLoading = false; }
  }
  closeVaultDetail() { this.vaultDetail = null; }
  // "Open" from the detail window → load the knowledge base (or resume a warm session).
  enterVaultDetail() {
    const v = this.vaultDetail;
    if (!v) return;
    this.vaultDetail = null;
    void this.pickVault(v.path);
  }

  // ---- Learn (vault builder) — draft intake state ----
  learnDraft = $state<{ vault: string; name: string } | null>(null);
  learnSources = $state<LearnSources | null>(null);
  learnMode = $state<'new' | 'expand'>('new');   // building a new vault vs expanding an existing one
  learnHasCover = $state(false);                  // does the draft/target vault have a cover yet
  learnBusy = $state(false);

  async learnCreate(name: string, topic: string) {
    this.learnBusy = true;
    try {
      const r = await api.learnCreate(name, topic);
      this.learnDraft = { vault: r.vault, name: r.name }; this.learnExcluded = []; this.learnIncluded = [];
      this.learnSources = r.sources;
      this.learnMode = 'new';
      this.learnHasCover = false;
      this.setStatus(`Draft “${r.name}” created — add material below.`);
    } catch (e) { this.setStatus('Create failed: ' + msg(e), true); }
    finally { this.learnBusy = false; }
  }
  // Expand an EXISTING knowledge base — reuse the intake flow against its own vault.
  async expandVault(v: VaultInfo) {
    this.learnBusy = true;
    try {
      const r = await api.learnOpen(v.path);
      this.learnDraft = { vault: r.vault, name: r.name }; this.learnExcluded = []; this.learnIncluded = [];
      this.learnSources = r.sources;
      this.learnMode = 'expand';
      this.learnHasCover = !!v.has_cover;
      this.vaultDetail = null;
      this.page = 'learn';
      this.setStatus(`Expanding “${r.name}” — add material below.`);
    } catch (e) { this.setStatus('Expand failed: ' + msg(e), true); }
    finally { this.learnBusy = false; }
  }
  async learnUpload(files: File[]) {
    if (!this.learnDraft || !files.length) return;
    this.learnBusy = true;
    try {
      const r = await api.learnUpload(this.learnDraft.vault, files);
      this.learnSources = r.sources;
      this.setStatus(`Added ${r.saved} file${r.saved === 1 ? '' : 's'}${r.skipped ? `, skipped ${r.skipped} (unsupported)` : ''}.`);
    } catch (e) { this.setStatus('Upload failed: ' + msg(e), true); }
    finally { this.learnBusy = false; }
  }
  async learnSaveMeta(prompt: string, links: string[]) {
    if (!this.learnDraft) return;
    try { this.learnSources = (await api.learnMeta(this.learnDraft.vault, prompt, links)).sources; this.setStatus('Saved.'); }
    catch (e) { this.setStatus('Save failed: ' + msg(e), true); }
  }
  async learnRemoveSource(id: string) {
    if (!this.learnDraft) return;
    try { this.learnSources = (await api.learnRemoveSource(this.learnDraft.vault, id)).sources; }
    catch (e) { this.setStatus('Remove failed: ' + msg(e), true); }
  }
  learnDiscard() { this.learnDraft = null; this.learnSources = null; this.learnMode = 'new'; this.learnHasCover = false; this.learnExcluded = []; this.learnIncluded = []; }

  // ---- the ingest swarm (build) ----
  buildActive = $state(false);
  buildStatus = $state<string | null>(null);     // running | done | cancelled | error
  buildSources = $state<BuildSource[]>([]);
  buildLog = $state<string[]>([]);
  buildCost = $state(0);                          // running USD total across the build
  buildActivity = $state('');                     // current orchestrator activity line
  buildDry = $state(false);                        // remember dry-ness so Resume matches
  buildNotice = $state<string | null>(null);      // terminal status to surface in the sidebar when away from Learn
  buildNoticeMsg = $state('');                    // one-line reason (e.g. cap-hit message)
  buildModels = $state<{ orchestrator: string; writer: string }>({ orchestrator: 'Sonnet 4.6', writer: 'Sonnet 4.6' });

  // ---- Models & Keys (multi-provider endpoints) ----
  endpoints = $state<import('./types').Endpoint[]>([]);
  endpointsBusy = $state(false);
  // Mercury (the reading engine) key — its own spot in Settings → Read, separate from
  // ingest endpoints. Server holds the key; client only knows whether one is set.
  mercuryHasKey = $state(false);
  async loadMercuryKey() { try { this.mercuryHasKey = (await api.mercuryKey()).has_key; } catch { /* ignore */ } }
  async setMercuryKey(key: string) {
    try { this.mercuryHasKey = (await api.setMercuryKey(key)).has_key; }
    catch (e) { this.setStatus('Save Mercury key failed: ' + msg(e), true); }
  }
  async clearMercuryKey() { try { await api.clearMercuryKey(); this.mercuryHasKey = false; } catch { /* ignore */ } }

  // Web search provider (Tavily / Brave) — powers research-prompt builds.
  searchProvider = $state('');
  searchHasKey = $state(false);
  searchAvailable = $state(false);    // a stored OR .env provider is usable
  searchProviders = $state<string[]>(['tavily', 'brave']);
  async loadSearch() {
    try { const c = await api.searchConfig(); this.searchProvider = c.provider; this.searchHasKey = c.has_key; this.searchAvailable = c.available; this.searchProviders = c.providers; }
    catch { /* ignore */ }
  }
  async setSearchKey(provider: string, key: string) {
    try { const c = await api.setSearch(provider, key); this.searchProvider = c.provider; this.searchHasKey = c.has_key; this.searchAvailable = c.available; }
    catch (e) { this.setStatus('Save search key failed: ' + msg(e), true); throw e; }
  }
  async clearSearchKey() {
    try { const c = await api.clearSearch(); this.searchProvider = c.provider; this.searchHasKey = c.has_key; this.searchAvailable = c.available; }
    catch { /* ignore */ }
  }
  async loadEndpoints() {
    try { this.endpoints = (await api.endpoints()).endpoints; } catch { /* ignore */ }
  }
  async addEndpoint(name: string, baseUrl: string, key: string) {
    this.endpointsBusy = true;
    try { await api.endpointAdd(name, baseUrl, key); await this.loadEndpoints(); }
    catch (e) { this.setStatus('Add endpoint failed: ' + msg(e), true); throw e; }
    finally { this.endpointsBusy = false; }
  }
  async removeEndpoint(id: string) {
    try { await api.endpointDelete(id); await this.loadEndpoints(); }
    catch (e) { this.setStatus('Remove failed: ' + msg(e), true); }
  }
  async toggleEndpoint(e: import('./types').Endpoint) {
    try { await api.endpointUpdate(e.id, { name: e.name, base_url: e.base_url, enabled: !e.enabled }); await this.loadEndpoints(); }
    catch (err) { this.setStatus('Update failed: ' + msg(err), true); }
  }
  async reprobeEndpoint(id: string) {
    try { await api.endpointReprobe(id); await this.loadEndpoints(); }
    catch (e) { this.setStatus('Re-probe failed: ' + msg(e), true); }
  }

  // ---- Learn ingest settings (global defaults, persisted) ----
  learnEndpointId = $state('');                    // '' = default (Anthropic / .env)
  learnMaxCost = $state(5);                        // per-source cap (USD); 0 = unlimited
  learnTotalCap = $state(0);                       // whole-build cap (USD); 0 = unlimited
  learnModelOrchestrator = $state('');            // '' = endpoint/pipeline default
  learnModelWriter = $state('');
  learnModelMechanical = $state('');
  learnAutoExplore = $state(true);
  learnMaxPages = $state(0);                        // 0 = pipeline default (25)

  get selectedLearnEndpoint(): import('./types').Endpoint | null {
    return this.endpoints.find((e) => e.id === this.learnEndpointId) ?? null;
  }

  loadLearnSettings() {
    try {
      const s = JSON.parse(ls.get('dwell-learn-settings') || '{}');
      if (typeof s.endpointId === 'string') this.learnEndpointId = s.endpointId;
      if (typeof s.maxCost === 'number') this.learnMaxCost = s.maxCost;
      if (typeof s.totalCap === 'number') this.learnTotalCap = s.totalCap;
      if (typeof s.modelOrchestrator === 'string') this.learnModelOrchestrator = s.modelOrchestrator;
      if (typeof s.modelWriter === 'string') this.learnModelWriter = s.modelWriter;
      if (typeof s.modelMechanical === 'string') this.learnModelMechanical = s.modelMechanical;
      if (typeof s.autoExplore === 'boolean') this.learnAutoExplore = s.autoExplore;
      if (typeof s.maxPages === 'number') this.learnMaxPages = s.maxPages;
    } catch { /* ignore */ }
  }
  private persistLearnSettings() {
    ls.set('dwell-learn-settings', JSON.stringify({
      endpointId: this.learnEndpointId,
      maxCost: this.learnMaxCost, totalCap: this.learnTotalCap,
      modelOrchestrator: this.learnModelOrchestrator, modelWriter: this.learnModelWriter,
      modelMechanical: this.learnModelMechanical, autoExplore: this.learnAutoExplore,
      maxPages: this.learnMaxPages,
    }));
  }
  setLearnSetting<K extends 'learnMaxCost' | 'learnTotalCap' | 'learnModelOrchestrator' | 'learnModelWriter' | 'learnModelMechanical' | 'learnAutoExplore' | 'learnMaxPages'>(key: K, val: this[K]) {
    this[key] = val;
    this.persistLearnSettings();
  }
  // Switching endpoint invalidates the per-role model picks (they belong to the old
  // provider) — reset them to the new endpoint's default.
  setLearnEndpoint(id: string) {
    this.learnEndpointId = id;
    this.learnModelOrchestrator = '';
    this.learnModelWriter = '';
    this.learnModelMechanical = '';
    this.persistLearnSettings();
  }
  // Pending-source selection: ids UNCHECKED in the Learn queue sit the next build out
  // ('skipped') but stay saved in the draft for a later one.
  learnExcluded = $state<string[]>([]);
  learnToggleSource(id: string) {
    this.learnExcluded = this.learnExcluded.includes(id)
      ? this.learnExcluded.filter((x) => x !== id)
      : [...this.learnExcluded, id];
  }
  // Vault-pending raw files (in raw/ but never ingested) are OPT-IN per build.
  learnIncluded = $state<string[]>([]);
  learnToggleInclude(id: string) {
    this.learnIncluded = this.learnIncluded.includes(id)
      ? this.learnIncluded.filter((x) => x !== id)
      : [...this.learnIncluded, id];
  }

  private buildOpts() {
    const ep = this.selectedLearnEndpoint;
    // With a non-Anthropic endpoint, an unset role must resolve to that endpoint's
    // first model — the Anthropic default model name wouldn't exist there.
    const mdl = (pick: string) => pick || (ep ? (ep.models[0] || null) : null);
    return {
      exclude: this.learnExcluded,
      include: this.learnIncluded,
      endpoint_id: this.learnEndpointId || null,
      max_cost: this.learnMaxCost > 0 ? this.learnMaxCost : null,
      total_cap: this.learnTotalCap > 0 ? this.learnTotalCap : null,
      model_orchestrator: mdl(this.learnModelOrchestrator),
      model_writer: mdl(this.learnModelWriter),
      model_mechanical: mdl(this.learnModelMechanical),
      auto_explore: this.learnAutoExplore,
      max_pages: this.learnMaxPages > 0 ? this.learnMaxPages : null,
    };
  }

  async startBuild(dry = false, resume = false) {
    const draft = this.learnDraft;
    if (!draft) return;
    this.buildActive = true;
    this.buildStatus = 'running';
    this.buildDry = dry;
    this.buildNotice = null;
    // snapshot which model each role uses for THIS run (settings at build start)
    {
      const ep = this.selectedLearnEndpoint;
      const label = (pick: string) => pick ? modelShort(pick) : (ep ? (ep.models[0] ? modelShort(ep.models[0]) : 'default') : 'Sonnet 4.6');
      this.buildModels = { orchestrator: label(this.learnModelOrchestrator), writer: label(this.learnModelWriter) };
    }
    if (!resume) {                                 // a fresh build clears prior state; resume keeps it
      this.buildSources = [];
      this.buildLog = [];
      this.buildCost = 0;
    }
    this.buildActivity = resume ? 'Resuming…' : '';
    try {
      await api.learnBuild(draft.vault, dry, {
        preparing: () => { this.buildActivity = 'Preparing sources…'; this.buildLog = [...this.buildLog, 'Preparing sources…']; },
        split: (p) => { this.buildLog = [...this.buildLog, `Split “${p.name}” into ${p.into} chapter${p.into === 1 ? '' : 's'}`]; },
        'build-start': (p) => { this.buildSources = p.sources; },
        source: (p) => {
          const s = this.buildSources.find((x) => x.id === p.id);
          if (s) { s.status = p.status; this.buildSources = [...this.buildSources]; }
        },
        progress: (p) => {
          if (p.activity) { this.buildActivity = p.activity; this.buildLog = [...this.buildLog.slice(-60), p.activity]; }
        },
        cost: (p) => { if (typeof p.total === 'number') this.buildCost = p.total; },
        log: (p) => { this.buildLog = [...this.buildLog.slice(-60), p.line]; },
        'build-done': (p) => {
          this.buildStatus = p.status;
          if (typeof p.cost === 'number') this.buildCost = p.cost;
          this.buildActivity = '';
          // surface the outcome in the sidebar if the user wandered off to read
          if (p.status !== 'cancelled' && this.page !== 'learn') this.buildNotice = p.status;
        },
        error: (p) => {
          // 'capped' rides in as a build-done after this; mark the reason now
          this.buildNoticeMsg = p.message ?? '';
          if (this.page !== 'learn') this.buildNotice = 'error';
          this.setStatus('Ingest: ' + (p.message ?? 'error'), true);
        },
      }, undefined, resume, this.buildOpts());
    } catch (e) {
      this.buildStatus = 'error';
      this.buildNoticeMsg = msg(e);
      if (this.page !== 'learn') this.buildNotice = 'error';
      this.setStatus((resume ? 'Resume failed: ' : 'Build failed: ') + msg(e), true);
    }
    await this.refreshVaults();      // the vault may now have pages → graduates into Read
  }
  // Open Learn and clear any pending build notice (the user is now looking at it).
  openLearn() { this.page = 'learn'; this.buildNotice = null; }
  resumeBuild() { void this.startBuild(this.buildDry, true); }
  async stopBuild() {
    if (!this.learnDraft) return;
    try { await api.learnBuildStop(this.learnDraft.vault); }
    catch (e) { this.setStatus('Stop failed: ' + msg(e), true); }
  }
  buildClose() { this.buildActive = false; this.buildStatus = null; this.buildSources = []; this.buildLog = []; this.buildCost = 0; this.buildActivity = ''; this.buildNotice = null; this.buildNoticeMsg = ''; }

  // ---- add / remove existing vaults ----
  coverVersion = $state(0);              // bumped on cover change → cache-busts the <img>
  async refreshVaults() {
    try { this.vaults = (await api.vaults()).vaults; } catch { /* ignore */ }
  }
  // Cover set/remove works on a target vault path (defaults to the open detail window).
  // Used from both the detail window and the Learn draft.
  async setVaultCover(file: File, vaultPath?: string) {
    const path = vaultPath ?? this.vaultDetail?.path;
    if (!path) return;
    try {
      await api.vaultSetCover(path, file);
      this.coverVersion += 1;
      if (this.vaultDetail?.path === path) this.vaultDetail.has_cover = true;
      if (this.learnDraft?.vault === path) this.learnHasCover = true;
      await this.refreshVaults();
      this.setStatus('Cover image set.');
    } catch (e) { this.setStatus('Cover failed: ' + msg(e), true); }
  }
  async removeVaultCover(vaultPath?: string) {
    const path = vaultPath ?? this.vaultDetail?.path;
    if (!path) return;
    try {
      await api.vaultRemoveCover(path);
      this.coverVersion += 1;
      if (this.vaultDetail?.path === path) this.vaultDetail.has_cover = false;
      if (this.learnDraft?.vault === path) this.learnHasCover = false;
      await this.refreshVaults();
      this.setStatus('Cover removed.');
    } catch (e) { this.setStatus('Cover failed: ' + msg(e), true); }
  }
  async importVault(path: string): Promise<boolean> {
    if (!path.trim()) return false;
    try {
      await api.vaultImport(path.trim());
      await this.refreshVaults();
      this.setStatus('Knowledge base added.');
      return true;
    } catch (e) { this.setStatus('Import failed: ' + msg(e), true); return false; }
  }
  // purge=false forgets a registered external (files kept); purge=true deletes from disk.
  async removeVault(v: VaultInfo, purge: boolean) {
    try {
      await api.vaultDelete(v.path, purge);
      this.dropStash(v.path);
      if (this.vaultDetail?.path === v.path) this.vaultDetail = null;
      await this.refreshVaults();
      this.setStatus(purge ? `Deleted “${v.name}”.` : `Removed “${v.name}” from the library.`);
    } catch (e) { this.setStatus('Remove failed: ' + msg(e), true); }
  }

  // ---- vault selection (hero-card picker) ----
  // Pick a vault from the gallery. If we stashed a live session for it when backing
  // out, RESTORE that — instant, no brain reload, exact place. Else load it fresh.
  // (If the stashed session was evicted server-side, fall back to a fresh load.)
  async pickVault(path: string) {
    if (this.loading) return;
    const st = this.vaultStash[path];
    if (st) {
      if (await this.sessionAlive(st.session.session_id)) { this.restoreStash(path, st); return; }
      this.dropStash(path);                 // server session expired — reload from scratch
    }
    this.vaultPath = path;
    void this.loadSession();
  }

  // Back out to the gallery WITHOUT tearing down the session: stash the live reading
  // state per-vault (the server keeps the Brain loaded, so re-entry skips the load).
  backToGallery() {
    if (this.busy) return;
    this.narrator.stop();
    if (this.session) {
      this.vaultStash = { ...this.vaultStash, [this.vaultPath]: {
        session: this.session, voices: this.voices, voice: this.voice, level: this.level, form: this.form, language: this.language,
        started: this.started, pages: this.pages, cursor: this.cursor, branches: this.branches,
        cost: this.cost, popular: this.popular, allNodes: this.allNodes,
      } };
    }
    this.session = null; this.started = false; this.pages = []; this.branches = [];
    this.cursor = 0; this.cost = 0; this.query = ''; this.popular = []; this.allNodes = [];
    this.resetQuiz();
    this.setStatus('Choose a knowledge base to begin.');
  }

  private async sessionAlive(sid: string): Promise<boolean> {
    try { await api.state(sid); return true; } catch { return false; }
  }
  private restoreStash(path: string, st: VaultStash) {
    this.narrator.stop();
    this.vaultPath = path;
    this.loadNotes();                 // per-vault saved highlights
    this.session = st.session; this.voices = st.voices; this.voice = st.voice; this.level = st.level; this.form = st.form; this.language = st.language;
    this.pages = st.pages; this.cursor = st.cursor; this.branches = st.branches; this.cost = st.cost;
    this.popular = st.popular; this.allNodes = st.allNodes; this.started = st.started;
    this.query = ''; this.resetQuiz();
    this.dropStash(path);                   // now the live view again; re-stashed on next back-out
    this.setStatus(`Resumed ${st.session.topic || path}.`);
  }
  private dropStash(path: string) {
    if (!(path in this.vaultStash)) return;
    const next = { ...this.vaultStash }; delete next[path]; this.vaultStash = next;
  }

  async loadNodes() {
    const sid = this.sid;
    if (!sid) return;
    try { this.popular = (await api.nodes(sid, 14)).nodes; } catch { /* ignore */ }
  }
  async loadAllNodes() {
    const sid = this.sid;
    if (!sid) return;
    try { this.allNodes = (await api.nodes(sid, 0)).nodes; } catch { /* ignore */ }
  }
  async loadPaths() {
    const sid = this.sid;
    if (!sid) return;
    try { this.paths = (await api.paths(sid)).paths; } catch { /* ignore */ }
  }

  // Generate a fresh, diverse-but-coherent path (wander→narrativize) and walk it.
  // Different every call — the diversity is in the stochastic graph walk.
  async generatePath() {
    const sid = this.sid;
    if (!sid || this.busy) return;
    this.setStatus('✨ Wandering the vault to compose a path…');
    try {
      await api.generatePath(sid);      // stashes the spine on the session
      this.startPath('__generated__');  // walk it (title/goal arrive with the first page)
    } catch (e) {
      this.setStatus('Generate failed: ' + msg(e), true);
    }
  }

  // Walk a curated Path's frozen spine (its lens is applied server-side on start).
  startPath(id: string) {
    if (!this.sid) return;
    this.started = true;
    this.pages = [];
    this.cursor = 0;
    this.branches = [];
    this.pathProgress = null;
    this.resetQuiz();
    void this.advance({ action: 'first', path_id: id });
  }

  beginAt(seed: string) {
    if (!this.sid) return;
    this.started = true;
    this.pages = [];
    this.cursor = 0;
    this.branches = [];
    this.pathProgress = null;        // seeding a free-wander thread leaves any Path
    this.resetQuiz();
    void this.advance({ action: 'first', seed });
  }

  async init() {
    try { this.customThemes = JSON.parse(ls.get('dwell-custom-themes') || '[]'); } catch { /* ignore */ }
    const saved = ls.get('dwell-theme') || DEFAULT_THEME;
    this.setTheme(saved, false);                       // colors only; bg restored next
    const d = THEME_BG[saved] ?? { pattern: 'none' };  // fall back to the theme's vibe
    this.bgPattern = ls.get('dwell-bg-pattern') ?? d.pattern;
    this.bgIntensity = num(ls.get('dwell-bg-intensity'), d.intensity ?? 1);
    this.bgSize = num(ls.get('dwell-bg-size'), 1);
    this.bgEffectColor = ls.get('dwell-bg-color') ?? d.color ?? '';
    this.frosted = ls.get('dwell-frosted') !== null ? ls.get('dwell-frosted') === '1' : (d.frosted ?? false);
    this.applyBg();
    this.setDensity(ls.get('dwell-density') || 'comfortable');
    this.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, num(ls.get('dwell-zoom'), 1)));
    this.level = ls.get('dwell-level') || 'general';
    this.form = ls.get('dwell-form') || 'article';
    this.language = ls.get('dwell-language') || 'source';
    this.loadLearnSettings();
    void this.loadEndpoints();
    void this.loadMercuryKey();
    void this.loadSearch();
    this.quizzesOn = ls.get('dwell-quizzes') !== '0';
    this.quizEvery = Math.max(2, Math.min(20, num(ls.get('dwell-quiz-every'), 5)));
    this.quizCount = Math.max(3, Math.min(25, num(ls.get('dwell-quiz-count'), 5)));
    try {
      const saved = JSON.parse(ls.get('dwell-quiz-types') || 'null');
      if (saved && typeof saved === 'object') {
        const next: Record<string, boolean> = {};
        for (const t of QUIZ_TYPE_KEYS) next[t] = saved[t] !== false;   // default on unless saved off
        if (QUIZ_TYPE_KEYS.some((t) => next[t])) this.quizTypes = next;
      }
    } catch { /* ignore */ }
    void this.initNarration();
    try {
      const { vaults, root } = await api.vaults();
      this.vaults = vaults;
      this.vaultRoot = root;
      // No pre-selected knowledge base — vaultPath stays empty until the user picks one.
      this.setStatus(vaults.length ? '' : `No knowledge bases under ${root}.`);
    } catch (e) {
      this.setStatus('Could not reach the server: ' + msg(e), true);
    }
  }

  async loadSession() {
    if (!this.vaultPath || this.loading) return;
    this.loadNotes();                 // per-vault saved highlights
    this.loading = true;
    this.setStatus('Loading brain (embedding pages — first load ~6s)…');
    try {
      const info = await api.session({
        vault: this.vaultPath, engine: this.engine || null, level: this.level, form: this.form,
        language: this.language, dry: this.dry,
      });
      this.session = info;
      this.voices = info.voices;
      this.voice = info.voices.current;
      this.level = info.level || 'general';
      this.form = info.form || 'article';
      this.language = info.language || 'source';
      this.started = false;
      this.pages = [];
      this.branches = [];
      this.cost = 0;
      this.query = '';
      void this.loadNodes();          // populate the sidebar's popular-nodes list
      void this.loadAllNodes();       // full list for search
      void this.loadPaths();          // curated Paths for this vault
      this.pathProgress = null;
      const warn = info.init_error ? ` — engine fell back (${info.init_error})` : '';
      this.setStatus(
        `Loaded ${info.nodes} nodes · ${info.embed_label} · ${info.provider}` +
        `${info.model ? ':' + info.model : ''} · ${info.dry ? 'free' : 'live'} · mode=${info.mode}` +
        `${warn}. Choose where to begin ↑`,
      );
    } catch (e) {
      this.setStatus('Load failed: ' + msg(e), true);
    } finally {
      this.loading = false;
    }
  }

  begin(start: string) {
    if (!this.sid) return;
    this.started = true;
    this.pages = [];
    this.cursor = 0;
    this.branches = [];
    this.pathProgress = null;        // launch-menu threads are free-wander, not a Path
    this.resetQuiz();
    void this.advance({ action: 'first', start });
  }

  newThread() { this.begin('surprise'); }

  async advance(opts: AdvanceOpts) {
    // Quiz checkpoint: before composing ANY new page — flow, a branch, a leap, or a
    // steer (wander however you like) — if a quiz is due, hold this move and run the
    // quiz first. closeQuiz() then resumes exactly this advance, so you never lose the
    // branch you picked.
    if (opts.action !== 'first') {
      if (this.quizOpen) return;                 // a quiz is open — hold new pages (flip back freely)
      if (this.quizDue) { this.pendingAdvance = opts; void this.openQuiz(); return; }
    }
    const sid = this.sid;
    if (!sid || this.busy || (!this.started && opts.action !== 'first')) return;
    this.busy = true;
    this.branches = [];
    this.queued = null;               // any direct navigation clears a pending queue
    this.narrator.stop();             // silence the previous page when a new one begins
    this.setStatus('…composing…');
    let idx = -1;
    try {
      await api.streamPage(
        {
          session: sid, action: opts.action, plan_id: opts.plan_id ?? null,
          start: opts.start ?? 'new', seed: opts.seed ?? null, path_id: opts.path_id ?? null,
          wander: this.wander, diffusing: this.diffuse,
        },
        {
          start: (p) => {
            this.pages.push({
              key: this.#key++, text: '', node: p.node, title: p.title,
              mode: p.mode, marker: 'live', steer_bucket: p.steer_bucket, sources: [],
              images: [], layout: null, form: p.form ?? 'article', marks: [], textFigure: null, live: true,
            });
            idx = this.pages.length - 1;
            this.cursor = idx;          // deck follows to the freshly-composed page
          },
          frame: (p) => { if (idx >= 0) { this.applyText(this.pages[idx], p.text); this.maybeEarlyNarrate(this.pages[idx], p.text); } },
          done: (p) => {
            let earlyOwned = false;
            if (idx >= 0) {
              const v = this.pages[idx];
              this.applyText(v, p.text); v.live = false; v.marker = p.marker;
              earlyOwned = this.finishEarlyNarration(v);
              v.mode = p.mode; v.node = p.node; v.title = p.title; v.sources = p.sources ?? [];
              v.images = p.images ?? []; v.layout = p.layout ?? null; v.form = p.form ?? v.form;
              v.textFigure = p.text_figure ?? null;
            }
            this.branches = p.branches;
            this.cost = p.cost;
            if (p.path) this.pathProgress = p.path;   // curated-Path progress chip
            if (typeof p.dream === 'number') this.dream = p.dream;   // reflect the active creativity dial
            const pmsg = p.path
              ? `${p.path.title} · step ${p.path.gate}/${p.path.gates}${p.path.complete ? ' · complete ✓' : ''}`
              : `${p.mode} · ${p.recap || ''} — choose a direction below ↓`;
            this.setStatus(pmsg);
            // read the new page aloud — UNLESS early narration already owns it (it read
            // the opening and speaks the remainder itself; playCurrent would restart it
            // from the top, jumping back to the beginning).
            if (this.narrate && !earlyOwned) this.playCurrent();
          },
          path_done: (p) => {                        // walked past the last gate
            this.pathProgress = p;
            this.setStatus(`${p.title} — complete ✓ (${p.gates}/${p.gates})`);
          },
          error: (p) => { this.setStatus(p.message, true); },
        },
      );
    } catch (e) {
      this.setStatus('Page failed: ' + msg(e), true);
    } finally {
      this.busy = false;
    }
  }

  async steer(text: string) {
    const sid = this.sid;
    if (!sid || this.busy || !text.trim()) return;
    this.setStatus('Steering → ' + text);
    try {
      await api.steer(sid, text);
      await this.advance({ action: 'auto' });
    } catch (e) {
      this.setStatus('Steer failed: ' + msg(e), true);
    }
  }

  // Clarify a passage in place: rework the SELECTED text (simpler | more) seamlessly
  // between its before/after, then — crucially — treat it as a RE-NARRATION event so
  // the voice and karaoke don't desync. We stop the stale audio (its word-timeline was
  // built from the old text), splice the new prose, then read from the start of the
  // changed passage to the end of the page, mapping karaoke back onto the full page.
  async expand(pageKey: number, start: number, length: number, mode: string) {
    const sid = this.sid;
    if (!sid || this.busy) return;
    const page = this.pages.find((p) => p.key === pageKey);
    if (!page) return;
    const full = page.text;
    const head = full.slice(0, start);
    const selected = full.slice(start, start + length);
    const tail = full.slice(start + length);
    if (selected.trim().length < 8) return;
    this.narrator.stop();                     // requesting a rework stops narration (text changes)
    this.busy = true;
    this.setStatus(`…${mode === 'simplify' ? 'making it simpler' : 'expanding'}…`);
    try {
      await api.streamExpand(
        { session: sid, selected, before: head, after: tail, mode },
        {
          frame: (p) => { this.applyText(page, head + p.text + tail); },
          done: (p) => {
            this.applyText(page, head + p.text + tail);   // head/tail already clean → only the new middle is parsed
            this.cost = p.cost;
            if (this.narrate) {               // read the clarified passage back, then flow on
              this.narratingKey = page.key;
              this.setStatus('Clarified — reading it back ↓');
              void this.narrator.speak(page.text.slice(start), this.ttsVoice, this.ttsSpeed, start);
            } else {
              this.setStatus('Clarified — select more, or continue ↓');
            }
          },
          error: (p) => { this.setStatus(p.message, true); },
        },
      );
    } catch (e) {
      this.setStatus('Clarify failed: ' + msg(e), true);
    } finally {
      this.busy = false;
    }
  }

  // Narrator voice (a separate axis from level + form). Same vault, re-pitched into the
  // new voice IN PLACE — like form/level, changing it live re-renders the focused page and
  // resumes narration from ~where it left off (see repitch()).
  async setVoice(name: string) {
    if (name === this.voice && this.started) return;
    this.voice = name;
    const sid = this.sid;
    if (!sid) return;
    try {
      await api.setVoice(sid, name);
      this.queueAxisChange(`Voice → ${name}`);
    } catch (e) {
      this.setStatus('Voice failed: ' + msg(e), true);
    }
  }

  // Reading level (a separate axis from voice). Each level caches its own pages.
  // Changing it re-pitches the page you're looking at IN PLACE (instant before/after);
  // persisted across sessions.
  async setLevel(name: string) {
    if (name === this.level && this.started) return;
    this.level = name;
    ls.set('dwell-level', name);
    const sid = this.sid;
    if (!sid) return;
    try {
      await api.setLevel(sid, name);
      this.queueAxisChange(`Reading level → ${name}`);
    } catch (e) {
      this.setStatus('Level failed: ' + msg(e), true);
    }
  }

  // Output form (rhetorical shape — a separate axis from level + voice). Same vault,
  // re-pitched into article / steps / Q&A / dialogue IN PLACE; persisted across sessions.
  async setForm(name: string) {
    if (name === this.form && this.started) return;
    this.form = name;
    ls.set('dwell-form', name);
    const sid = this.sid;
    if (!sid) return;
    try {
      await api.setForm(sid, name);
      this.queueAxisChange(`Form → ${name}`);
    } catch (e) {
      this.setStatus('Form failed: ' + msg(e), true);
    }
  }

  // Output language (the page's medium — a separate axis from voice/form/level). Same
  // vault, re-rendered in the target language IN PLACE; persisted across sessions.
  // NB: commit the client state only AFTER the server confirms — otherwise a failed call
  // (e.g. the proxy 404 we hit) strands `language` out of sync, and the idempotency guard
  // then blocks the retry.
  async setLanguage(name: string) {
    if (name === this.language && this.started) return;
    const sid = this.sid;
    if (!sid) { this.language = name; ls.set('dwell-language', name); return; }
    try {
      await api.setLanguage(sid, name);
      this.language = name;
      ls.set('dwell-language', name);
      this.queueAxisChange(`Language → ${name}`);
    } catch (e) {
      this.setStatus('Language failed: ' + msg(e), true);
    }
  }

  // Creativity / 'dream' dial (0..1) — a separate axis: how much inventive license the
  // render has (0 faithful … 1 dramatize). Paths default to a modest 0.35 so they read as
  // narrative; this lets the reader push it either way. Re-pitched in place like the others.
  async setDream(value: number) {
    const v = Math.max(0, Math.min(1, value));
    this.dream = v;
    const sid = this.sid;
    if (!sid) return;
    try {
      await api.setDream(sid, v);
      this.queueAxisChange(`Creativity → ${Math.round(v * 100)}%`);
    } catch (e) {
      this.setStatus('Creativity failed: ' + msg(e), true);
    }
  }

  // Axis changes (voice / level / form / language) update the renderer server-side
  // immediately — that's FREE. The paid re-render is DEFERRED and BATCHED: mark the page
  // dirty here, and run a single repitch() when the reader clicks Apply or closes Settings.
  // So changing four axes costs one render, not four.
  private queueAxisChange(label: string) {
    if (this.started && this.pages[this.cursor]) {
      this.pendingAxes = true;
      this.setStatus(`${label} — Apply to update the page.`);
    } else {
      this.setStatus(`${label}. Flow forward to feel it.`);   // nothing on screen to re-pitch
    }
  }

  // Run the single deferred re-render, if anything changed. Cheap no-op otherwise.
  async applyAxes() {
    if (!this.pendingAxes) return;
    this.pendingAxes = false;
    if (this.started && this.pages[this.cursor]) await this.repitch();
  }

  // Closing Settings flushes any pending axis changes (one render for all of them).
  closeSettings() {
    this.settingsOpen = false;
    void this.applyAxes();
  }

  // ---- notes (reader-saved highlights, persisted per vault) ----------------
  private notesKey() { return 'dwell-notes:' + (this.vaultPath || '_'); }
  private loadNotes() {
    try { this.notes = JSON.parse(ls.get(this.notesKey()) || '[]'); } catch { this.notes = []; }
  }
  private saveNotes() { ls.set(this.notesKey(), JSON.stringify(this.notes)); }

  // Save a highlighted passage (verbatim) from page `key`, plus a snapshot of the exact
  // page so the back-link returns to THIS text, never a regenerated version. The snapshot
  // drops session-scoped image URLs (they'd 404 in a later session); the text is the point.
  noteFromSelection(text: string, key: number) {
    const page = this.pages.find((p) => p.key === key);
    const body = (text || '').trim();
    if (!page || !body) return;
    const snap: PageView = { ...$state.snapshot(page), images: [], layout: null, live: false };
    const id = Math.random().toString(36).slice(2, 10);
    this.notes = [{ id, text: body, node: page.node, title: page.title, vault: this.vaultPath, ts: Date.now(), page: snap }, ...this.notes];
    this.saveNotes();
    this.setStatus(`Noted from “${page.title}”.`);
  }
  removeNote(id: string) { this.notes = this.notes.filter((n) => n.id !== id); this.saveNotes(); }
  clearNotes() { this.notes = []; this.saveNotes(); }
  // Click a note → return to the EXACT saved page. Prefer a copy still live in the deck
  // (match by verbatim text + node); otherwise restore the saved snapshot into the deck.
  // Either way the reader lands on the precise text that was highlighted — no re-render.
  gotoNote(n: Note) {
    this.notesOpen = false;
    let i = this.pages.findIndex((p) => p.node === n.page.node && p.text === n.page.text);
    if (i < 0) {
      this.narrator.stop();
      // Insert at the BACK of the deck (index 0), NOT the forward edge — so the live
      // frontier (where the ghost composes next and new pages generate) stays undisturbed.
      // We jump the cursor to it; the reader swipes right to return toward their frontier.
      this.pages = [{ ...n.page, key: this.#key++, live: false }, ...this.pages];
      // Deliberately do NOT set `started`. From the launch screen this keeps it a PREVIEW:
      // the Begin menu (Resume / Somewhere new / Surprise) stays visible so the reader can
      // still choose where to go — the deck shows whenever pages exist (see Reader).
      i = 0;
    }
    this.noteHighlight = { key: this.pages[i].key, text: n.text };   // paint the saved span
    this.goTo(i);
  }

  // Re-render the focused page at the renderer's CURRENT axes (level / voice / form /
  // language) in place — the page's plan + context are kept server-side, a clean re-pitch.
  async repitch() {
    const sid = this.sid;
    const index = this.cursor;
    const page = this.pages[index];
    if (!sid || this.busy || !page) return;
    // Capture where narration is BEFORE we stop it. The re-rendered text no longer
    // matches the old audio, so we must restart — but resume near the same place
    // (the sentence at the same fraction through the page), not lose the reader's spot.
    const wasNarrating = this.narrate && this.narrating;
    const frac = wasNarrating && this.spoken ? Math.min(1, Math.max(0, this.spoken.cs / (page.text.length || 1))) : 0;
    this.busy = true;
    this.narrator.stop();
    this.setStatus('…re-pitching this page…');
    try {
      await api.streamRepage(
        { session: sid, index },
        {
          frame: (p) => { this.applyText(page, p.text); },
          done: (p) => {
            this.applyText(page, p.text); page.marker = p.marker; page.sources = p.sources ?? [];
            page.images = p.images ?? []; page.layout = p.layout ?? null; page.form = p.form ?? page.form;
            page.textFigure = p.text_figure ?? null;
            if (typeof p.dream === 'number') this.dream = p.dream;
            this.cost = p.cost;
            this.setStatus('Re-pitched — read on ↓');
            if (wasNarrating) {                          // resume from ~the same sentence
              const start = frac > 0.02 ? sentenceStartNear(page.text, frac) : 0;
              this.narratingKey = page.key;
              void this.narrator.speak(page.text.slice(start), this.ttsVoice, this.ttsSpeed, start);
            }
          },
          error: (p) => { this.setStatus(p.message, true); },
        },
      );
    } catch (e) {
      this.setStatus('Re-pitch failed: ' + msg(e), true);
    } finally {
      this.busy = false;
    }
  }

  setWander(v: number) {
    this.wander = v;
    if (this.sid) void api.wander(this.sid, v).catch(() => {});
  }

  async showMissed() {
    const sid = this.sid;
    if (!sid) return;
    this.setStatus('Finding missed connections…');
    try {
      this.missed = await api.missed(sid, 25);
      this.setStatus('Missed connections — close but never wikilinked.');
    } catch (e) {
      this.setStatus('Missed failed: ' + msg(e), true);
    }
  }
  hideMissed() { this.missed = null; }

  // --- timeline (Tier-2 view over the enrichment temporal sidecar) ---
  async showTimeline() {
    const sid = this.sid;
    if (!sid) return;
    this.setStatus('Building timeline…');
    try {
      this.timeline = await api.timeline(sid, 0.7);
      this.setStatus(this.timeline.available
        ? `Timeline · ${this.timeline.count} dated events — click one to jump.`
        : 'No enrichment yet — run `cli.py enrich` on this vault.');
    } catch (e) {
      this.setStatus('Timeline failed: ' + msg(e), true);
    }
  }
  hideTimeline() { this.timeline = null; }
  jumpToEvent(page: string) { this.hideTimeline(); this.requestBeginAt(page); }

  // --- quizzes (retrieval practice) ---
  private resetQuiz() { this.quizDueAt = this.quizEvery; this.quizOpen = false; this.quizLoading = false; this.quiz = null; this.pendingAdvance = null; }

  async openQuiz() {
    const sid = this.sid;
    if (!sid || this.quizOpen) return;
    const end = this.pages.length;
    const texts = this.pages.slice(Math.max(0, end - this.quizEvery), end).map((p) => p.text).filter(Boolean);
    if (!texts.length) { this.closeQuiz(); return; }
    this.narrator.stop();                 // hold narration while the quiz is up
    this.quizOpen = true;
    this.quizLoading = true;
    this.quiz = null;
    this.setStatus('✎ Quick check on the last few pages…');
    try {
      const r = await api.quiz(sid, texts, this.quizCount, this.enabledQuizTypes);
      this.cost = r.cost;
      this.quiz = r.questions;
      if (!r.questions.length) { this.closeQuiz(); return; }   // nothing generated → carry on
      this.setStatus('✎ Quick check — answer, then continue ↓');
    } catch (e) {
      this.setStatus('Quiz failed: ' + msg(e), true);
      this.closeQuiz();
    } finally {
      this.quizLoading = false;
    }
  }

  // Dismiss the quiz and resume reading (composes the next page; the recliner picks up).
  closeQuiz() {
    this.quizOpen = false;
    this.quiz = null;
    this.quizLoading = false;
    this.quizDueAt = this.pages.length + this.quizEvery;   // schedule the next checkpoint
    const opts = this.pendingAdvance ?? { action: 'auto' };
    this.pendingAdvance = null;
    void this.advance(opts);                               // resume exactly the move the quiz interrupted
  }

  disableQuizzes() { this.setQuizzes(false); this.closeQuiz(); }
  setQuizzes(on: boolean) { this.quizzesOn = on; ls.set('dwell-quizzes', on ? '1' : '0'); }
  setQuizEvery(n: number) { this.quizEvery = Math.max(2, Math.min(20, Math.round(n))); ls.set('dwell-quiz-every', String(this.quizEvery)); }
  setQuizCount(n: number) { this.quizCount = Math.max(3, Math.min(25, Math.round(n))); ls.set('dwell-quiz-count', String(this.quizCount)); }
  setQuizType(type: string, on: boolean) {
    const next = { ...this.quizTypes, [type]: on };
    if (!QUIZ_TYPE_KEYS.some((t) => next[t])) return;   // keep at least one type enabled
    this.quizTypes = next;
    ls.set('dwell-quiz-types', JSON.stringify(next));
  }
}

function msg(e: unknown): string { return e instanceof Error ? e.message : String(e); }

export const dwell = new DwellStore();

// Dev-only debug handle (lets you poke state from the console).
if (import.meta.env?.DEV) (globalThis as Record<string, unknown>).dwell = dwell;
