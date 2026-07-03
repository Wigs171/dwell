"""
dwell_tts.py — optional audio narration for Dwell, via Kokoro (kokoro-onnx).

Runs in-process on ONNX Runtime (no torch, so it can't disturb Dwell's
sentence-transformers embeddings). The model is lazy-loaded; a page is spoken
sentence-by-sentence on a background thread so playback starts within a second
and can be stopped instantly. Volume is applied live in the audio callback, so
the slider responds immediately.

Everything degrades gracefully: if kokoro-onnx / sounddevice / the model files /
an output device are missing, Narrator.ensure_ready() returns False with a
reason in `.error`, and Dwell stays a silent reader.

Model files (~340 MB, downloaded once to ~/.cache/kokoro-onnx):
  kokoro-v1.0.onnx, voices-v1.0.bin   (kokoro-onnx release model-files-v1.0)
"""

from __future__ import annotations

import os
import queue
import re
import threading
from pathlib import Path

import numpy as np

MODELS_DIR = Path.home() / ".cache" / "kokoro-onnx"
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"
_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_URLS = {MODEL_FILE: f"{_BASE}/{MODEL_FILE}", VOICES_FILE: f"{_BASE}/{VOICES_FILE}"}

# A curated subset of Kokoro's 54 voices — good long-form narrators, QUALITY-FIRST.
# Kokoro grades its voices A–D (model card VOICES.md); the C/D voices are the ones
# that read as robotic, so the defaults lead with the A/B tier. A name may also be a
# BLEND — "af_heart+af_bella" (50/50) or "af_heart*0.7+af_bella*0.3" — mixed from the
# style vectors at synthesis time (free: no model change, smooths prosody/warmth).
NARRATOR_VOICES = [
    "af_heart",                    # A — the best narrator in the pack
    "af_bella",                    # A-
    "af_heart+af_bella",           # blend: warm + expressive
    "af_nicole", "bf_emma",        # B-
    "af_heart+bf_emma",            # blend: US warmth, UK measure
    "am_fenrir", "am_michael",     # C+ — best of the males
    "am_fenrir+am_michael",        # blend: steadiest male narrator
    "bm_george", "bm_fable",       # C — kept for continuity (former default)
    "af_sarah",
]
DEFAULT_NARRATOR_VOICE = "af_heart"
DEFAULT_SINGLE_VOICE = "af_heart"   # a real single voice, for blend-resolution fallback

# One blend term: "af_heart" or "af_heart*0.7". Names are Kokoro's <lang><sex>_<name>.
_BLEND_TERM = re.compile(r"^([a-z]{2}_[a-z]+)(?:\*(\d*\.?\d+))?$")


def _resolve_voice(k, voice):
    """A voice is either a Kokoro voice name (passed through) or a BLEND spec
    "a+b" / "a*0.7+b*0.3" — averaged style vectors (Kokoro's own default `af` is a
    blend, so this is the intended mechanism). Returns a name or a float32 ndarray;
    on any parse/lookup failure, falls back to the first term or the default name."""
    if "+" not in voice:
        return voice
    terms = []
    for part in voice.split("+"):
        m = _BLEND_TERM.match(part.strip())
        if not m:
            continue
        terms.append((m.group(1), float(m.group(2)) if m.group(2) else 1.0))
    if not terms:
        return DEFAULT_SINGLE_VOICE
    try:
        total = sum(w for _, w in terms) or 1.0
        style = None
        for name, w in terms:
            s = k.get_voice_style(name) * (w / total)
            style = s if style is None else style + s
        return style
    except Exception:                     # unknown voice etc. — degrade to a real name
        return terms[0][0]


# --- G2P: misaki (preferred) vs kokoro's built-in espeak (fallback) --------------------
# Kokoro's "robotic" flatness is largely a PHONEMIZER artifact: kokoro-onnx defaults to
# espeak-ng, but Kokoro's natural prosody comes from misaki (its purpose-built G2P with
# real pronunciation dictionaries + stress marking; espeak is meant only as the OOV
# fallback). When misaki is installed we phonemize with it — same model, same speed, less
# robotic — and feed the phonemes to Kokoro via create(is_phonemes=True). If misaki isn't
# installed (e.g. a fresh open-source clone), we transparently use kokoro's espeak path.
# Force the old path with DWELL_TTS_G2P=espeak.
_g2p = None
_g2p_tried = False
_g2p_lock = threading.Lock()


def _misaki_g2p():
    """Lazily build misaki's English G2P (dictionaries + espeak fallback for OOV words,
    reusing the espeak-ng lib kokoro-onnx already ships). Returns None if unavailable →
    caller falls back to kokoro's own espeak phonemization."""
    global _g2p, _g2p_tried
    if _g2p_tried:
        return _g2p
    with _g2p_lock:
        if _g2p_tried:
            return _g2p
        _g2p_tried = True
        if os.environ.get("DWELL_TTS_G2P", "").lower() == "espeak":
            return None
        try:
            import espeakng_loader
            espeakng_loader.make_library_available()
            lib, data = espeakng_loader.get_library_path(), espeakng_loader.get_data_path()
            # the shipped espeak-ng DLL has a stale build-time data path baked in; point
            # every resolver (env + wrapper) at the real bundled data dir, or it fails on
            # OOV words with "D:/a/.../phontab: No such file or directory".
            os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = lib
            os.environ["PHONEMIZER_ESPEAK_PATH"] = data
            os.environ["ESPEAK_DATA_PATH"] = data
            try:
                from phonemizer.backend.espeak.wrapper import EspeakWrapper
                EspeakWrapper.set_library(lib)
                if hasattr(EspeakWrapper, "set_data_path"):
                    EspeakWrapper.set_data_path(data)
            except Exception:  # noqa: BLE001
                pass
            from misaki import en, espeak
            try:
                fb = espeak.EspeakFallback(british=False)
            except Exception:  # noqa: BLE001 — no OOV fallback, dictionaries still work
                fb = None
            _g2p = en.G2P(trf=False, british=False, fallback=fb)
        except Exception:  # noqa: BLE001 — misaki not installed → espeak path
            _g2p = None
        return _g2p


def _phonemes_for(sentence: str) -> str | None:
    """misaki phonemes for one sentence, or None to signal 'use kokoro's espeak path'."""
    g = _misaki_g2p()
    if g is None:
        return None
    try:
        with _g2p_lock:                      # spacy pipeline isn't reentrant across threads
            ph, _ = g(sentence)
        return ph or None
    except Exception:  # noqa: BLE001
        return None


def _create(k, sentence: str, voice, speed: float):
    """One Kokoro synthesis for a sentence — via misaki phonemes when available, else
    kokoro's built-in espeak phonemization. Returns (samples, sr)."""
    ph = _phonemes_for(sentence)
    if ph:
        return k.create(ph, voice=voice, speed=speed, is_phonemes=True)
    return k.create(sentence, voice=voice, speed=speed, lang="en-us")


_SENT_RE = re.compile(r"[^.!?…]+[.!?…]+[\"”')\]]*|\S[^.!?…]*$")


def _split_sentences(text: str, maxlen: int = 280) -> list[str]:
    """Sentence-ish chunks small enough to synthesize quickly; long sentences are
    further split on clause punctuation so the first audio starts fast."""
    out: list[str] = []
    for s in (m.strip() for m in _SENT_RE.findall(text.strip())):
        if not s:
            continue
        if len(s) <= maxlen:
            out.append(s)
            continue
        buf = ""
        for part in re.split(r"(?<=[,;:])\s+", s):
            if len(buf) + len(part) + 1 <= maxlen:
                buf = f"{buf} {part}".strip()
            else:
                if buf:
                    out.append(buf)
                buf = part
        if buf:
            out.append(buf)
    return out


def tts_available() -> tuple[bool, str]:
    """Cheap check (no model load) of whether the audio stack is importable."""
    try:
        import sounddevice  # noqa: F401
        import kokoro_onnx  # noqa: F401
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


class Narrator:
    def __init__(self, voice: str = DEFAULT_NARRATOR_VOICE,
                 volume: float = 0.8, speed: float = 1.0):
        self._voice = voice
        self._volume = max(0.0, min(1.0, volume))
        self._speed = speed
        self._k = None
        self._sd = None
        self._stream = None
        self._sr = 24000
        self._ready = False
        self.error = ""
        self._lock = threading.Lock()
        self._jobq: queue.Queue = queue.Queue()
        self._audioq: queue.Queue = queue.Queue()
        self._buf: np.ndarray | None = None
        self._buf_pos = 0
        self._flush = False
        self._epoch = 0
        self._closing = False
        self._worker_t: threading.Thread | None = None

    # ---- lifecycle ------------------------------------------------------
    def ensure_ready(self, on_status=None) -> bool:
        """Load the model + open the audio stream. Heavy (download on first run,
        ~1s model load) — call from a background thread. Idempotent."""
        if self._ready:
            return True
        with self._lock:
            if self._ready:
                return True
            try:
                import sounddevice as sd
                from kokoro_onnx import Kokoro, SAMPLE_RATE
            except Exception as exc:  # noqa: BLE001
                self.error = f"audio libraries not installed ({exc})"
                return False
            try:
                self._ensure_models(on_status)
            except Exception as exc:  # noqa: BLE001
                self.error = f"could not fetch voice model ({exc})"
                return False
            try:
                if on_status:
                    on_status("loading the voice model…")
                self._k = Kokoro(str(MODELS_DIR / MODEL_FILE),
                                 str(MODELS_DIR / VOICES_FILE))
                self._sr = int(SAMPLE_RATE)
            except Exception as exc:  # noqa: BLE001
                self.error = f"voice model failed to load ({exc})"
                return False
            try:
                self._sd = sd
                self._stream = sd.OutputStream(
                    samplerate=self._sr, channels=1, dtype="float32",
                    blocksize=0, callback=self._callback)
                self._stream.start()
            except Exception as exc:  # noqa: BLE001
                self.error = f"no audio output device ({exc})"
                return False
            self._worker_t = threading.Thread(target=self._worker, daemon=True)
            self._worker_t.start()
            self._ready = True
            return True

    def _ensure_models(self, on_status=None) -> None:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        import urllib.request
        for fname, url in _URLS.items():
            dst = MODELS_DIR / fname
            if dst.exists() and dst.stat().st_size > 1_000_000:
                continue
            if on_status:
                on_status(f"downloading {fname} (one-time)…")
            tmp = dst.with_suffix(dst.suffix + ".tmp")
            urllib.request.urlretrieve(url, tmp)
            tmp.rename(dst)

    def close(self) -> None:
        self._closing = True
        self._epoch += 1
        self._flush = True
        try:
            self._jobq.put_nowait(None)
        except Exception:
            pass
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass

    # ---- controls -------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._ready

    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, float(v)))   # read live in the callback

    def set_speed(self, s: float) -> None:
        self._speed = max(0.5, min(2.0, float(s)))

    def set_voice(self, name: str) -> None:
        if name:
            self._voice = name

    def all_voices(self) -> list[str]:
        if self._k is not None:
            try:
                return sorted(self._k.get_voices())
            except Exception:  # noqa: BLE001
                pass
        return list(NARRATOR_VOICES)

    def speak(self, text: str) -> None:
        """Stop whatever is playing and narrate `text` from the top."""
        if not self._ready or not text.strip():
            return
        self.stop()
        sentences = _split_sentences(text)
        if sentences:
            self._jobq.put((self._epoch, sentences))

    def stop(self) -> None:
        """Silence immediately and abandon any in-flight synthesis."""
        self._epoch += 1          # in-flight worker sentences will be discarded
        self._flush = True        # callback drops its current buffer + queue

    # ---- internals ------------------------------------------------------
    def _worker(self) -> None:
        while not self._closing:
            job = self._jobq.get()
            if job is None:
                return
            epoch, sentences = job
            for s in sentences:
                if epoch != self._epoch:
                    break          # superseded by a newer speak()/stop()
                try:
                    samples, _sr = _create(
                        self._k, s, _resolve_voice(self._k, self._voice), self._speed)
                except Exception:  # noqa: BLE001 — skip a bad chunk, keep going
                    continue
                if epoch != self._epoch:
                    break
                self._audioq.put(np.ascontiguousarray(samples, dtype="float32"))

    def _callback(self, outdata, frames, _time, _status) -> None:
        out = outdata[:, 0]
        if self._flush:                       # stop()/speak() asked us to drop audio
            self._flush = False
            self._buf, self._buf_pos = None, 0
            try:
                while True:
                    self._audioq.get_nowait()
            except queue.Empty:
                pass
        vol = self._volume
        i = 0
        while i < frames:
            if self._buf is None or self._buf_pos >= len(self._buf):
                try:
                    self._buf = self._audioq.get_nowait()
                    self._buf_pos = 0
                except queue.Empty:
                    out[i:] = 0.0             # nothing ready → silence
                    return
            n = min(frames - i, len(self._buf) - self._buf_pos)
            out[i:i + n] = self._buf[self._buf_pos:self._buf_pos + n] * vol
            self._buf_pos += n
            i += n


# ---------------------------------------------------------------------------
# Server-side synthesis for the web app — synthesize per-sentence WAV bytes and
# hand them to the browser to play. Unlike Narrator above it does NOT open a
# local audio device (no sounddevice): the audio plays in the client, not on the
# server. The model is lazy-loaded once and shared across requests.
# ---------------------------------------------------------------------------
_web_k = None
_web_lock = threading.Lock()


def web_tts_available() -> tuple[bool, str]:
    """Can the server synthesize? (kokoro-onnx importable + model files present.)"""
    try:
        import kokoro_onnx  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"kokoro-onnx not installed ({exc})"
    if not (MODELS_DIR / MODEL_FILE).exists() or not (MODELS_DIR / VOICES_FILE).exists():
        return False, "voice model not downloaded"
    return True, ""


def _load_web_kokoro():
    global _web_k
    if _web_k is not None:
        return _web_k
    with _web_lock:
        if _web_k is None:
            from kokoro_onnx import Kokoro
            _web_k = Kokoro(str(MODELS_DIR / MODEL_FILE), str(MODELS_DIR / VOICES_FILE))
    return _web_k


def list_web_voices() -> list[str]:
    # Curated grade-A-first list + blends LEAD, then the rest of the 54 alphabetically —
    # so the picker opens on the good voices and the blends are selectable.
    try:
        allv = set(_load_web_kokoro().get_voices())
        rest = sorted(v for v in allv if v not in NARRATOR_VOICES)
        lead = [v for v in NARRATOR_VOICES if "+" in v or v in allv]
        return lead + rest
    except Exception:  # noqa: BLE001
        return list(NARRATOR_VOICES)


def _pcm_to_wav(samples, sr: int) -> bytes:
    """Float32 [-1,1] mono samples → 16-bit PCM WAV bytes (stdlib only)."""
    import io
    import wave
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    data = (pcm * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(data)
    return buf.getvalue()


def synth_wavs(text: str, voice: str = DEFAULT_NARRATOR_VOICE, speed: float = 1.0):
    """Yield (sentence, wav_bytes) for each sentence of `text`. The model loads on
    the first call (~1s). Streaming per sentence keeps client latency to ~1s."""
    k = _load_web_kokoro()
    v = voice or DEFAULT_NARRATOR_VOICE
    sp = max(0.5, min(2.0, float(speed)))
    for s in _split_sentences(text):
        try:
            samples, sr = _create(k, s, _resolve_voice(k, v), sp)
        except Exception:  # noqa: BLE001 — skip a bad chunk, keep going
            continue
        if samples is not None and len(samples):
            yield s, _pcm_to_wav(samples, sr)
