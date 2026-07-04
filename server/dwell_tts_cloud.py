"""Cloud narration voices — Qwen3-TTS served by fal.ai.

The STUDIO lane beside Kokoro (dwell_tts.py): voices a user CLONES from a short
recording or DESIGNS from a text description. Enrollment is one-time — fal's
clone endpoint returns a speaker-embedding file that is downloaded and kept in
`~/Dwell/voices/<slug>/`, so the raw recording never has to leave the machine
again; each synthesis call passes text + that embedding. Synthesis is slower
than Kokoro (~RTF 2 per sentence on fal's current deployment), so sentences fan
out concurrently and stream back in order: first audio ~15-20s, then the queue
stays ahead of playback.

Voice ids are `cloud:<slug>` — the web client treats voice names as opaque
strings, so these ride the whole /tts pipeline untouched; only the Settings UI
knows how to create them. Key: FALAI_API_KEY (env or repo .env; never logged).
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dwell import _read_env_key
from dwell_tts import _split_sentences

VOICES_DIR = Path(os.environ.get("DWELL_VOICE_DIR", str(Path.home() / "Dwell" / "voices")))
CLOUD_PREFIX = "cloud:"
_TTS_EP = "fal-ai/qwen-3-tts/text-to-speech/1.7b"
_CLONE_EP = "fal-ai/qwen-3-tts/clone-voice/1.7b"
_PRESETS = ("Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
            "Ryan", "Aiden", "Ono_Anna", "Sohee")
_DEFAULT_BASE = "Eric"
_WORKERS = 4                      # concurrent sentence requests per page
PREVIEW_SENTENCE = ("Here is how this voice sounds reading a page — steady, "
                    "clear, and ready whenever you are.")

# -- the model registry: every fal-hosted TTS the picker offers -----------------
# One entry per model: fal endpoint, a CURATED handful of presets (not the full
# catalogs), list price per 1K characters (fal pricing page, 2026-07), and a
# measured per-sentence speed label (filled from a real probe — never guessed).
# A Dwell page runs ~3K characters, so page cost ≈ per_1k × 3.
PAGE_KCHARS = 3.0

CLOUD_MODELS: dict[str, dict] = {
    "qwen3": {
        "label": "Qwen3-TTS 1.7B",
        "endpoint": _TTS_EP,
        "presets": _PRESETS,
        "per_1k": 0.09,
        "speed": "~16s/sentence · pre-render",
        "args": lambda text, preset: {"text": text, "voice": preset,
                                      "language": "Auto"},
    },
    "chatterboxhd": {
        "label": "Chatterbox HD",
        "endpoint": "resemble-ai/chatterboxhd/text-to-speech",
        "presets": ("Aurora", "Blade", "Britney", "Carl", "Cliff",
                    "Richard", "Rico", "Siobhan", "Vicky"),
        "per_1k": 0.04,
        "speed": "~30–45s/sentence · pre-render",
        "args": lambda text, preset: {"text": text, "voice": preset},
    },
    "minimax": {
        "label": "MiniMax Speech 2.8 HD",
        "endpoint": "fal-ai/minimax/speech-2.8-hd",
        "presets": ("Wise_Woman", "Friendly_Person", "Deep_Voice_Man",
                    "Calm_Woman", "Casual_Guy", "Elegant_Man", "Sweet_Girl_2"),
        "per_1k": 0.10,
        "speed": "~2–3s/sentence · LIVE",
        "args": lambda text, preset: {"prompt": text, "output_format": "url",
                                      "voice_setting": {"voice_id": preset}},
    },
    "inworld": {
        "label": "Inworld TTS-1.5 Max",
        "endpoint": "fal-ai/inworld-tts",
        "presets": ("Craig (en)", "Olivia (en)", "James (en)"),
        "per_1k": 0.01,
        "speed": "~4s/sentence · LIVE",
        "args": lambda text, preset: {"text": text, "voice": preset},
    },
}


def cloud_models_public() -> list[dict]:
    """JSON-safe registry for the UI picker: label, presets, rough page cost,
    measured speed."""
    return [{"id": mid, "label": m["label"], "presets": list(m["presets"]),
             "per_1k": m["per_1k"],
             "page_usd": round(m["per_1k"] * PAGE_KCHARS, 2),
             "speed": m["speed"] or "untested"}
            for mid, m in CLOUD_MODELS.items()]


# -- clone engines: how a recorded/uploaded sample becomes a reusable voice -----
# qwen: fal returns a portable speaker EMBEDDING (one clone call at enroll time),
#       synth passes that embedding + the reference transcript — most natural.
# chatterbox: the reference AUDIO ITSELF is the voice — NO clone call, NO
#       embedding; enrollment is a pure upload (free), synth re-sends the clip.
#       Cheapest of all ($0.025/1k, verified on the fal model page), and adds an
#       expressiveness dial. Prices are per-1K-char synthesis (page ≈ ×3).
_CHATTERBOX_EP = "fal-ai/chatterbox/text-to-speech"
CLONE_ENGINES: dict[str, dict] = {
    "qwen": {"label": "Qwen3-TTS", "per_1k": 0.09, "speed": "~16s/sentence",
             "note": "most natural"},
    "chatterbox": {"label": "Chatterbox", "per_1k": 0.025, "speed": "~17s/sentence",
                   "note": "cheapest · free enroll"},
}
DEFAULT_CLONE_ENGINE = "qwen"


def clone_engines_public() -> list[dict]:
    return [{"id": eid, "label": e["label"], "note": e["note"],
             "per_1k": e["per_1k"],
             "page_usd": round(e["per_1k"] * PAGE_KCHARS, 2),
             "speed": e["speed"]}
            for eid, e in CLONE_ENGINES.items()]

_ref_urls: dict[tuple, str] = {}  # (slug, filename) -> fal storage URL (per-process)


# -- availability --------------------------------------------------------------
def _key() -> str:
    """The fal.ai key: a UI-set key (Settings → Read, stored server-side) wins;
    otherwise FALAI_API_KEY from the environment / repo .env."""
    try:
        from dwell_endpoints import read_tts_key
        k = read_tts_key()
        if k:
            return k
    except Exception:
        pass
    return _read_env_key("FALAI_API_KEY")


def cloud_tts_available() -> tuple[bool, str]:
    if not _key():
        return False, "no FALAI_API_KEY in environment or .env"
    try:
        import fal_client  # noqa: F401
    except ImportError:
        return False, "fal-client not installed (pip install fal-client)"
    return True, ""


def _fal():
    # Always refresh from _key() so a UI key change takes effect immediately
    # (setdefault would pin a stale key for the process lifetime).
    k = _key()
    if k:
        os.environ["FAL_KEY"] = k
    import fal_client
    return fal_client


# -- the voice library ----------------------------------------------------------
def _slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "voice"


def _meta_path(slug: str) -> Path:
    return VOICES_DIR / slug / "meta.json"


def load_meta(slug: str) -> dict | None:
    p = _meta_path(slug)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_cloud_voices(detailed: bool = False):
    """Voice ids (or full meta dicts) for every enrolled voice on disk."""
    out = []
    if VOICES_DIR.is_dir():
        for d in sorted(VOICES_DIR.iterdir()):
            m = load_meta(d.name)
            if m is None:
                continue
            out.append(m if detailed else CLOUD_PREFIX + d.name)
    return out


def delete_voice(slug: str) -> bool:
    d = VOICES_DIR / slug
    if not (d.is_dir() and (d / "meta.json").exists()):
        return False
    for f in d.iterdir():
        f.unlink(missing_ok=True)
    d.rmdir()
    for k in [k for k in _ref_urls if k[0] == slug]:
        _ref_urls.pop(k, None)
    return True


# -- enrollment ------------------------------------------------------------------
def enroll_clone(name: str, audio_bytes: bytes, ext: str, transcript: str = "",
                 engine: str = DEFAULT_CLONE_ENGINE, exaggeration: float = 0.5) -> dict:
    """One-time clone. The sample is ALWAYS kept on disk. For the `qwen` engine we
    also fetch a portable speaker embedding (one clone call); for `chatterbox` the
    sample itself is the voice (no clone call — enrollment is a pure upload, so it
    costs nothing beyond storage). Raises on any fal failure."""
    if engine not in CLONE_ENGINES:
        engine = DEFAULT_CLONE_ENGINE
    fal = _fal()
    slug = _slugify(name)
    d = VOICES_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    ext = (ext or "wav").lstrip(".").lower()
    sample = d / f"sample.{ext}"
    sample.write_bytes(audio_bytes)
    meta = {"slug": slug, "name": name.strip() or slug, "kind": "clone",
            "engine": engine, "sample_file": sample.name,
            "reference_text": transcript.strip(),
            "created": time.strftime("%Y-%m-%d %H:%M")}
    ref_url = fal.upload_file(str(sample))
    if engine == "qwen":
        args = {"audio_url": ref_url}
        if transcript.strip():
            args["reference_text"] = transcript.strip()
        res = fal.run(_CLONE_EP, arguments=args)
        emb_url = _find_url(res)
        if not emb_url:
            raise RuntimeError("clone endpoint returned no embedding URL")
        urllib.request.urlretrieve(emb_url, d / "embedding.safetensors")
        _ref_urls[(slug, "embedding.safetensors")] = emb_url
    else:                                   # chatterbox — the sample IS the voice
        meta["exaggeration"] = max(0.0, min(1.5, float(exaggeration)))
        _ref_urls[(slug, sample.name)] = ref_url   # reuse this upload this process-life
    _meta_path(slug).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def enroll_design(name: str, description: str, base_voice: str = "") -> dict:
    """A DESIGNED voice is just a stored style prompt — no fal call to create."""
    slug = _slugify(name)
    d = VOICES_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    meta = {"slug": slug, "name": name.strip() or slug, "kind": "design",
            "prompt": description.strip(),
            "base_voice": base_voice if base_voice in _PRESETS else _DEFAULT_BASE,
            "created": time.strftime("%Y-%m-%d %H:%M")}
    _meta_path(slug).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


# -- synthesis --------------------------------------------------------------------
def _find_url(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "url" and isinstance(v, str):
                return v
            r = _find_url(v)
            if r:
                return r
    if isinstance(obj, list):
        for v in obj:
            r = _find_url(v)
            if r:
                return r
    return None


def _reference_url(slug: str, filename: str) -> str:
    """A voice file's fal-storage URL — re-uploaded from disk when the cached one
    is gone (fresh process) or expired (fal storage is not forever). Serves both
    the qwen embedding and the chatterbox reference sample."""
    key = (slug, filename)
    if key in _ref_urls:
        return _ref_urls[key]
    fal = _fal()
    path = VOICES_DIR / slug / filename
    if not path.exists():
        raise RuntimeError(f"voice '{slug}' is missing {filename}")
    url = fal.upload_file(str(path))
    _ref_urls[key] = url
    return url


def _is_chatterbox_clone(meta: dict) -> bool:
    return meta.get("kind") == "clone" and meta.get("engine") == "chatterbox"


def _synth_args(meta: dict, text: str) -> dict:
    if _is_chatterbox_clone(meta):          # the reference clip IS the voice
        return {"text": text,
                "audio_url": _reference_url(meta["slug"], meta.get("sample_file", "sample.wav")),
                "exaggeration": meta.get("exaggeration", 0.5)}
    args = {"text": text, "language": "Auto"}
    if meta["kind"] == "clone":             # qwen: embedding + transcript
        args["speaker_voice_embedding_file_url"] = _reference_url(
            meta["slug"], "embedding.safetensors")
        if meta.get("reference_text"):
            args["reference_text"] = meta["reference_text"]
    else:                                   # design: preset timbre + style prompt
        args["voice"] = meta.get("base_voice") or _DEFAULT_BASE
        if meta.get("prompt"):
            args["prompt"] = meta["prompt"]
    return args


def _route(voice: str):
    """voice id → (endpoint, args_fn(text)->dict). Two id shapes:
    `cloud:<model>/<preset>` = a registry preset; `cloud:<slug>` = an enrolled
    library voice (slugs never contain '/'). The endpoint follows the voice's
    clone engine (chatterbox clones synth on the Chatterbox endpoint; everything
    else on Qwen). Raises on unknown ids."""
    rest = voice[len(CLOUD_PREFIX):] if voice.startswith(CLOUD_PREFIX) else voice
    if "/" in rest:
        model_id, preset = rest.split("/", 1)
        m = CLOUD_MODELS.get(model_id)
        if m is None:
            raise RuntimeError(f"unknown cloud model '{model_id}'")
        return m["endpoint"], (lambda text, _m=m, _p=preset: _m["args"](text, _p))
    meta = load_meta(rest)
    if meta is None:
        raise RuntimeError(f"unknown cloud voice '{rest}'")
    endpoint = _CHATTERBOX_EP if _is_chatterbox_clone(meta) else _TTS_EP
    return endpoint, (lambda text, _meta=meta: _synth_args(_meta, text))


def _synth_one(endpoint: str, args_fn, sentence: str) -> bytes | None:
    fal = _fal()
    try:
        res = fal.run(endpoint, arguments=args_fn(sentence))
        url = _find_url(res)
        if not url:
            return None
        with urllib.request.urlopen(url) as r:
            return r.read()
    except Exception:                       # noqa: BLE001 — skip a bad sentence
        return None


def synth_cloud_wavs(text: str, voice: str, speed: float = 1.0):
    """Yield (sentence, audio_bytes) like dwell_tts.synth_wavs — clips are
    mp3/WAV per model (the client decodes via decodeAudioData, format-agnostic)
    and sentences are synthesized CONCURRENTLY, yielded in reading order.
    `speed` is accepted for interface parity; these models don't expose it."""
    endpoint, args_fn = _route(voice)
    sentences = _split_sentences(text)
    if not sentences:
        return
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        futures = [ex.submit(_synth_one, endpoint, args_fn, s) for s in sentences]
        for s, fut in zip(sentences, futures):
            audio = fut.result()
            if audio:
                yield s, audio


def preview_clip(voice: str) -> bytes | None:
    """One fixed sentence in this voice — the Settings preview button."""
    try:
        endpoint, args_fn = _route(voice)
    except RuntimeError:
        return None
    return _synth_one(endpoint, args_fn, PREVIEW_SENTENCE)
