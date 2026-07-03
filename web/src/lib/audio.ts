// Audio narration with word-level highlighting. Streams per-sentence WAV clips
// from the server's Kokoro /tts endpoint and plays them GAPLESSLY (each decoded
// buffer scheduled on a running AudioContext playhead). It also builds an ordered
// WORD TIMELINE — each word mapped to its char-offset in the page text and a
// proportional time slice within its clip — and a tick loop fires onWord(cs,ce)
// for the word currently being spoken (for karaoke highlight + auto-scroll), and
// onEnd when the page finishes (so the reader can advance). Falls back to the
// browser SpeechSynthesis API (with real word boundaries) when /tts is down.
import { api } from './api';

function b64ToArrayBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

interface Word { cs: number; ce: number; t0: number; t1: number; }

export class AudioNarrator {
  available = false;
  voices: string[] = [];
  defaultVoice = 'af_heart';

  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private volume = 1;        // master narration volume (0–1), applied to the gain node
  private sources = new Set<AudioBufferSourceNode>();
  private nextTime = 0;
  private abort: AbortController | null = null;
  private epoch = 0;
  private streamDone = false;
  private playing = false;

  // word timeline + scheduling queue
  private fullText = '';
  private timeline: Word[] = [];
  private charCursor = 0;
  private pending: { b64: string; text: string }[] = [];
  private draining = false;
  private rafId = 0;
  private wi = 0;          // current word index in the tick scan
  private didSpeak = false; // did this narration actually produce audio?
  private baseOffset = 0;   // added to word offsets — lets us speak a page *substring* (re-narration)

  onState: (playing: boolean) => void = () => {};
  onWord: (cs: number, ce: number) => void = () => {};   // cs < 0 → clear
  onEnd: () => void = () => {};                           // natural completion only

  async init() {
    try {
      const d = await api.ttsVoices();
      this.available = d.available;
      this.voices = d.voices ?? [];
      this.defaultVoice = d.default || 'af_heart';
    } catch { this.available = false; }
  }

  get isPlaying() { return this.playing; }
  get spoke() { return this.didSpeak; }   // did the last narration actually produce audio?

  /** Master narration volume (0–1). Applies live to the Web Audio gain and to the
   *  SpeechSynthesis fallback's next utterance. */
  setVolume(v: number) {
    this.volume = Math.max(0, Math.min(1, v));
    if (this.gain) this.gain.gain.value = this.volume;
  }
  private setPlaying(p: boolean) { if (p !== this.playing) { this.playing = p; this.onState(p); } }
  // Emit a spoken-word range, shifting by baseOffset so a substring narration maps
  // onto the full page. Never shift the clear sentinel (negative).
  private emitWord(cs: number, ce: number) {
    if (cs < 0) this.onWord(-1, -1);
    else this.onWord(cs + this.baseOffset, ce + this.baseOffset);
  }

  private ensureCtx(): AudioContext {
    if (!this.ctx) {
      const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.ctx = new Ctor();
      this.gain = this.ctx.createGain();
      this.gain.gain.value = this.volume;
      this.gain.connect(this.ctx.destination);
    }
    if (this.ctx.state === 'suspended') void this.ctx.resume();
    return this.ctx;
  }

  /** Stop whatever's playing and narrate `text` from the top. `baseOffset` shifts
   *  reported word offsets (for narrating a page substring after a clarify). */
  async speak(text: string, voice?: string, speed = 1, baseOffset = 0) {
    this.stop();
    if (!text.trim()) return;
    const epoch = ++this.epoch;
    this.baseOffset = baseOffset;
    this.fullText = text;
    this.timeline = [];
    this.charCursor = 0;
    this.wi = 0;
    this.streamDone = false;
    this.didSpeak = false;

    if (!this.available) { this.speakWebSpeech(text, voice, speed, epoch); return; }

    const ctx = this.ensureCtx();
    this.nextTime = ctx.currentTime + 0.12;
    this.abort = new AbortController();
    this.setPlaying(true);
    this.startTick();
    try {
      await api.streamTts({ text, voice, speed }, {
        clip: (p) => { if (epoch === this.epoch) { this.pending.push(p); void this.drain(epoch); } },
        error: () => { if (epoch === this.epoch) { this.streamDone = true; } },
      }, this.abort.signal);
      // /tts has no explicit 'done' event — the stream resolving IS completion.
      // Mark done; the last clip's onended then triggers the natural-end callback.
      if (epoch === this.epoch) { this.streamDone = true; this.maybeFinish(epoch); }
    } catch { /* aborted/network — settles via maybeFinish on the next onended */ }
  }

  /** Serial decode+schedule so audio AND the word timeline stay in clip order
   *  regardless of decodeAudioData resolution timing. */
  private async drain(epoch: number) {
    if (this.draining) return;
    this.draining = true;
    const ctx = this.ctx!;
    while (this.pending.length) {
      if (epoch !== this.epoch) break;
      const c = this.pending.shift()!;
      let buf: AudioBuffer;
      try { buf = await ctx.decodeAudioData(b64ToArrayBuffer(c.b64)); }
      catch { continue; }
      if (epoch !== this.epoch) break;
      const start = Math.max(this.nextTime, ctx.currentTime + 0.02);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.gain!);
      src.start(start);
      this.sources.add(src);
      this.didSpeak = true;
      src.onended = () => { this.sources.delete(src); this.maybeFinish(epoch); };
      this.addClipWords(c.text, start, buf.duration);
      this.nextTime = start + buf.duration;
    }
    this.draining = false;
  }

  /** Map a sentence's words to char-offsets in fullText + proportional times. */
  private addClipWords(sentence: string, start: number, dur: number) {
    const tokens = sentence.match(/\S+/g);
    if (!tokens || !tokens.length || dur <= 0) return;
    const weights = tokens.map((t) => t.length + 1);
    const total = weights.reduce((a, b) => a + b, 0) || 1;
    let acc = 0;
    for (let i = 0; i < tokens.length; i++) {
      const tok = tokens[i];
      let cs = this.fullText.indexOf(tok, this.charCursor);
      if (cs < 0) cs = this.fullText.indexOf(tok);       // fallback: anywhere
      const t0 = start + (acc / total) * dur;
      acc += weights[i];
      const t1 = start + (acc / total) * dur;
      if (cs >= 0) { this.timeline.push({ cs, ce: cs + tok.length, t0, t1 }); this.charCursor = cs + tok.length; }
    }
  }

  private startTick() {
    cancelAnimationFrame(this.rafId);
    const tick = () => {
      if (!this.ctx || !this.playing) return;
      const now = this.ctx.currentTime;
      while (this.wi < this.timeline.length && now >= this.timeline[this.wi].t1) this.wi++;
      const w = this.timeline[this.wi];
      if (w && now >= w.t0) this.emitWord(w.cs, w.ce);
      this.rafId = requestAnimationFrame(tick);
    };
    this.rafId = requestAnimationFrame(tick);
  }

  /** Natural end: stream finished AND every clip has played out. */
  private maybeFinish(epoch: number) {
    if (epoch !== this.epoch) return;
    if (this.streamDone && this.pending.length === 0 && this.sources.size === 0) {
      cancelAnimationFrame(this.rafId);
      this.emitWord(-1, -1);
      this.setPlaying(false);
      this.onEnd();
    }
  }

  private speakWebSpeech(text: string, voice: string | undefined, speed: number, epoch: number) {
    const synth = window.speechSynthesis;
    if (!synth) return;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = Math.max(0.5, Math.min(2, speed));
    u.volume = this.volume;
    if (voice) { const v = synth.getVoices().find((x) => x.name === voice); if (v) u.voice = v; }
    u.onboundary = (e) => {
      if (epoch !== this.epoch || e.name === 'sentence') return;
      const cs = e.charIndex;
      const len = e.charLength || (text.slice(cs).match(/^\S+/)?.[0].length ?? 0);
      if (len > 0) { this.didSpeak = true; this.emitWord(cs, cs + len); }
    };
    u.onend = () => { if (epoch === this.epoch) { this.onWord(-1, -1); this.setPlaying(false); this.onEnd(); } };
    this.setPlaying(true);
    synth.speak(u);
  }

  stop() {
    this.epoch++;                                    // invalidate everything in flight
    this.streamDone = true;
    this.pending = [];
    this.abort?.abort(); this.abort = null;
    cancelAnimationFrame(this.rafId);
    this.sources.forEach((s) => { try { s.stop(); } catch { /* already stopped */ } });
    this.sources.clear();
    if (this.ctx) this.nextTime = this.ctx.currentTime;
    window.speechSynthesis?.cancel();
    this.emitWord(-1, -1);
    this.setPlaying(false);
  }

  pause() { void this.ctx?.suspend(); window.speechSynthesis?.pause(); cancelAnimationFrame(this.rafId); this.setPlaying(false); }
  resume() {
    void this.ctx?.resume(); window.speechSynthesis?.resume();
    if (this.sources.size > 0 || window.speechSynthesis?.speaking) { this.setPlaying(true); this.startTick(); }
  }
}
