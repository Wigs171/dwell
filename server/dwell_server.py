"""
dwell_server.py — FastAPI adapter that exposes the Dwell engine over HTTP + SSE.

This is **Phase 1** of DWELL_APP_PLAN.md: turn the tkinter prototype into a real
app by wrapping the (already UI-agnostic) engine in `dwell.py` behind a thin web
API. The engine is NOT rewritten — every endpoint here drives the exact same
`Brain` / `Navigator` / `Renderer` / `TweenCache` / `ReadingHistory` objects, in the
plan → commit → predict → render → propose → prefetch order. The web client
(`dwell-web/`) is the only UI; the engine itself stays UI-agnostic.

Run it:
    PYTHONIOENCODING=utf-8 python prototypes/dwell_server.py
    # or:  uvicorn dwell_server:app --app-dir prototypes --reload
then open  http://127.0.0.1:8000/  for the test client (dwell_web.html).

------------------------------------------------------------------------------
DESIGN NOTES (the async/thread bridge — the only genuinely fiddly part)
------------------------------------------------------------------------------
The engine is *blocking*: a page render is a network call to Mercury/Anthropic
(seconds), embeddings load is heavy (~6s once). FastAPI runs on an asyncio loop,
so every blocking call goes to a thread (`run_in_threadpool`, or a worker thread
feeding an `asyncio.Queue` via `loop.call_soon_threadsafe`). The event loop is
never blocked; SSE frames are relayed as the worker produces them.

Per-session serialization mirrors the tkinter UI's two locks:
  • `alock` (asyncio.Lock)   — one page op per session at a time (a /page stream
    holds it for its whole duration; /steer, /wander wait their turn).
  • `render_lock` (threading.Lock) — serializes the *LLM call* between a
    foreground render and the background prefetch, each double-checking the cache
    after acquiring it (so a page warmed by prefetch is replayed, not re-paid).

DEVIATION FROM THE PLAN (intentional): the plan sketched `/page` as a GET (SSE)
for EventSource. We use **POST for both streaming endpoints** (`/page`,
`/expand`) and consume them with fetch()+ReadableStream on the client. Reasons:
`/expand` needs a POST anyway (long selection/context bodies blow past URL
limits), so a single streaming client path is simpler; session/action live in a
JSON body instead of a query string. Idempotent reads stay GET. Every SSE event's
`data` is a JSON object, which sidesteps the multi-line-data / blank-line pitfalls
of streaming prose (paragraph breaks) over raw SSE.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import secrets
import struct
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

# Import the engine. dwell.py lives beside this file; add it to the path so the
# server can be launched from anywhere (uvicorn --app-dir, the .bat, or directly).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dwell import (  # noqa: E402
    Brain, Navigator, Renderer, ReadingHistory, TweenCache, PagePlan,
    VaultPaths, missed_connections, VOICES, DEFAULT_VOICE, LEVELS, DEFAULT_LEVEL,
    LANGUAGES, DEFAULT_LANGUAGE,
    FORMS, DEFAULT_FORM,
    TWEEN_CACHE_FILE, HISTORY_FILE, TAIL_CHARS, _read_env_key,
)
from dwell_tts import (  # noqa: E402 — audio narration (Kokoro, server-side)
    web_tts_available, synth_wavs, list_web_voices,
    DEFAULT_NARRATOR_VOICE, NARRATOR_VOICES,
)
from text_figures import choose_text_figure, DEFAULT_DENSITY  # noqa: E402 — derived text-figures
from compendium.vault import read_page  # noqa: E402 — resolve source ids → titles
from compendium.vault.pages import locate_page  # noqa: E402 — find a node's file (for image frontmatter)

HERE = Path(__file__).resolve().parent
WEB_CLIENT = HERE / "dwell_web.html"
DIST = HERE.parent / "web" / "dist"          # built Svelte app (single-server mode)

DEFAULT_WANDER = 0.4
SESSION_TTL = float(os.environ.get("DWELL_SESSION_TTL", str(6 * 3600)))  # idle evict
# Knowledge bases live in one folder. For a cloned repo this defaults to the bundled
# ./vaults (so the Biology 101 demo works out of the box). Override DWELL_VAULT_ROOT to
# point at your own vault library (e.g. ~/Dwell).
VAULT_ROOT = Path(os.environ.get("DWELL_VAULT_ROOT") or str(HERE.parent / "vaults"))
VAULT_ROOT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Session state — one per reader; holds the live engine objects + driver cursor
# ---------------------------------------------------------------------------
@dataclass
class DwellSession:
    id: str
    vault_path: str
    topic: str
    brain: Brain
    renderer: Renderer
    cache: TweenCache
    history: ReadingHistory
    rng: random.Random
    nav: Navigator | None = None
    tail: str = ""                                  # last page's text (for seam/flow)
    pending_plan: PagePlan | None = None            # predicted next page (flow + prefetch)
    proposed: dict[str, PagePlan] = field(default_factory=dict)  # plan_id -> branch plan
    page_renders: list = field(default_factory=list)             # per page: {plan,tail,recap,hint} for re-level
    node_page_order: dict[str, list[str]] = field(default_factory=dict)  # node_id -> plan.key()s in first-seen order (image cycling cursor)
    text_fig_density: str = DEFAULT_DENSITY                       # off|sparse|normal|rich — how often a no-image page carries a derived text-figure
    source_titles: dict[str, str] = field(default_factory=dict)  # source id -> readable title (cached)
    wander: float = DEFAULT_WANDER
    created: float = 0.0
    last_used: float = 0.0
    alock: asyncio.Lock = field(default_factory=asyncio.Lock)        # one page op at a time
    render_lock: threading.Lock = field(default_factory=threading.Lock)  # fg vs prefetch

    def touch(self) -> None:
        self.last_used = time.time()

    def flush(self) -> None:
        try:
            self.cache.flush()
        except Exception:
            pass
        try:
            self.history.save()
        except Exception:
            pass


SESSIONS: dict[str, DwellSession] = {}


def _require_session(session_id: str | None) -> DwellSession:
    s = SESSIONS.get(session_id or "")
    if s is None:
        raise HTTPException(status_code=404, detail="unknown or expired session")
    s.touch()
    return s


def _evict_stale() -> None:
    """Drop sessions idle past the TTL, flushing their cache+history first. The
    plan keeps session state in-memory to start; this is the cheap janitor."""
    now = time.time()
    for sid in [sid for sid, s in SESSIONS.items() if now - s.last_used > SESSION_TTL]:
        s = SESSIONS.pop(sid, None)
        if s is not None:
            s.flush()


def _cost(s: DwellSession) -> float:
    ct = s.renderer.cost_tracker
    if ct is None:
        return 0.0
    try:
        return round(float(ct.get_summary().get("estimated_cost_usd", 0.0)), 4)
    except Exception:
        return 0.0


def _source_titles(s: DwellSession, ids: list[str]) -> list[str]:
    """Resolve source-page ids → readable titles (lazy, cached per session)."""
    out: list[str] = []
    vault = None
    for sid in ids[:6]:
        title = s.source_titles.get(sid)
        if title is None:
            if vault is None:
                vault = VaultPaths.for_vault(s.vault_path)
            try:
                pg = read_page(vault, sid)
                title = (pg.title if pg else sid) or sid
            except Exception:
                title = sid
            s.source_titles[sid] = title
        out.append(title)
    return out


# ---------------------------------------------------------------------------
# Page images — resolve a node's figures (auto + pins), serve them, pick a layout
# ---------------------------------------------------------------------------
# The "auto + pins" model (see prototypes/IMAGE_SOURCING.md):
#   • PINS — a node's frontmatter `images:` list / `image_pin:` (+ optional
#     `layout:`) is the authoritative, human-curated choice.
#   • AUTO — else a node's `sources:` → that source's `raw/assets/<source>/` dir
#     (where the ingest pipeline already drops figures). Empty until a vault has
#     images; the pin path is what the test node exercises today.
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# Templates the reader renders. Single text-node (top/bottom/side/inset/rail) +
# multi text-node (magazine/diagonal/mosaic, via the prose offset map).
_SUPPORTED_LAYOUTS = {"top", "bottom", "side", "inset", "rail", "magazine", "diagonal", "mosaic"}
_SINGLE_LAYOUTS = {"top", "bottom", "side", "inset", "rail"}
_MULTI_LAYOUTS = {"magazine", "diagonal", "mosaic"}

# AUTO layout is ASPECT-AWARE: a layout (single OR multi-image) is eligible for a
# page only when every frame it places gets an image whose orientation fits that
# frame — so a portrait is never forced into a landscape slot, yet the richer
# multi-image compositions still appear automatically (no pinning required) when a
# node's images actually suit them. `bottom` is intentionally NOT auto (the reader
# flagged real readability issues with a bottom-anchored image at large fonts) — it
# stays available via an explicit pin.
#
# Orientation class by aspect ratio r = w/h:
#   tall < 0.62 · portrait [0.62,0.9) · square [0.9,1.15) · landscape [1.15,2.0) · wide ≥ 2.0
# Single-image layouts each image's class can take (first = primary, rest = variety
# on the image's repeat appearances):
_SINGLE_OPTS = {
    "tall":      ["rail"],              # full-height side band — the home for tall portraits
    "portrait":  ["side", "magazine"],  # float-beside, or the magazine column on repeats
    "square":    ["side", "inset"],
    "landscape": ["top"],
    "wide":      ["top"],
}
# Multi-image compositions and the orientation classes each ordered slot accepts.
# Tried richest-first; a composition is only chosen if EVERY slot can be filled by a
# distinct, class-compatible image (so the fixed frames only ever crop WITHIN an
# orientation — never portrait→landscape). Slots map to the reader's figure order.
_MULTI_SLOTS = [
    # mosaic: wide banner (21/6) + landscape detail (4/3) + square detail (1/1)
    ("mosaic",   [{"wide"}, {"landscape", "square"}, {"square"}]),
    # diagonal: landscape top-right (3/2) + square partway-down-left (1/1)
    ("diagonal", [{"landscape"}, {"square"}]),
]


def _img_class(im: dict) -> str:
    r = (im["w"] / im["h"]) if im.get("h") else 1.4
    if r < 0.62:
        return "tall"
    if r < 0.9:
        return "portrait"
    if r < 1.15:
        return "square"
    if r < 2.0:
        return "landscape"
    return "wide"


def _image_size(path: Path) -> tuple[int, int]:
    """Read (width, height) from a PNG/JPEG/GIF/WebP header — no PIL dependency.
    Returns (0, 0) if it can't be determined (the frontend then uses naturalWidth)."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head[:6] in (b"GIF87a", b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
                return int(w), int(h)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                if head[12:16] == b"VP8 ":
                    w, h = struct.unpack("<HH", head[26:30])
                    return int(w) & 0x3FFF, int(h) & 0x3FFF
                if head[12:16] == b"VP8L":
                    b = head[21:25]
                    n = b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)
                    return (n & 0x3FFF) + 1, ((n >> 14) & 0x3FFF) + 1
                if head[12:16] == b"VP8X":
                    w = 1 + (head[24] | (head[25] << 8) | (head[26] << 16))
                    h = 1 + (head[27] | (head[28] << 8) | (head[29] << 16))
                    return w, h
            if head[:2] == b"\xff\xd8":             # JPEG: scan for an SOF marker
                f.seek(2)
                while True:
                    byte = f.read(1)
                    if not byte:
                        break
                    if byte != b"\xff":
                        continue
                    marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    if not marker:
                        break
                    m = marker[0]
                    if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                        f.read(3)                    # segment length (2) + precision (1)
                        h, w = struct.unpack(">HH", f.read(4))
                        return int(w), int(h)
                    seg = f.read(2)
                    if len(seg) < 2:
                        break
                    f.seek(struct.unpack(">H", seg)[0] - 2, 1)
    except Exception:
        pass
    return 0, 0


def _aspect(w: int, h: int) -> str:
    if not w or not h:
        return "landscape"
    r = w / h
    if r < 0.9:
        return "portrait"
    if r < 1.15:
        return "square"
    if r < 2.0:
        return "landscape"
    return "wide"


def _node_frontmatter(vault: VaultPaths, node_id: str) -> dict:
    """The node file's raw YAML frontmatter (the Node model drops unknown keys
    like `images:`, so we read the file directly)."""
    path = locate_page(vault, node_id)
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        m = re.match(r"\A---\s*\n(.*?\n)---", text, re.DOTALL)
        if not m:
            return {}
        fm = yaml.safe_load(m.group(1)) or {}
        return fm if isinstance(fm, dict) else {}
    except Exception:
        return {}


def _resolve_asset(vault: VaultPaths, file: str) -> Path | None:
    """A frontmatter `file:` is vault-root-relative (e.g. raw/assets/x/y.png) or,
    as a convenience, relative to raw/assets/."""
    rel = str(file).strip().lstrip("/").replace("\\", "/")
    for cand in ((vault.root / rel), (vault.raw_assets / rel)):
        try:
            if cand.is_file():
                return cand
        except Exception:
            pass
    return None


def _node_images(s: DwellSession, node) -> tuple[list[dict], str | None]:
    """Resolve a node's figures → (image records, pinned-layout|None)."""
    vault = VaultPaths.for_vault(s.vault_path)
    fm = _node_frontmatter(vault, node.id)
    specs: list[dict] = []
    pinned = fm.get("layout") or fm.get("image_layout")

    pin = fm.get("image_pin")
    if isinstance(pin, dict) and pin.get("file"):
        specs.append({"file": pin["file"], "caption": pin.get("caption", ""),
                      "attribution": pin.get("attribution", "")})
        pinned = pin.get("layout") or pinned
    imgs = fm.get("images")
    if isinstance(imgs, list):
        for it in imgs:
            if isinstance(it, str):
                specs.append({"file": it, "caption": "", "attribution": ""})
            elif isinstance(it, dict) and it.get("file"):
                specs.append({"file": it["file"], "caption": it.get("caption", ""),
                              "attribution": it.get("attribution", "")})
    if not specs:                                   # AUTO: sources → raw/assets/<source>/
        for sid in (node.sources or [])[:4]:
            d = vault.raw_assets / sid
            if d.is_dir():
                for f in sorted(d.iterdir()):
                    if f.suffix.lower() in _IMG_EXTS:
                        specs.append({"file": str(f.relative_to(vault.root)).replace("\\", "/"),
                                      "caption": "", "attribution": ""})

    out: list[dict] = []
    root = vault.root.resolve()
    for sp in specs[:48]:                           # the full pool (AUTO cycles through it); was capped at 3 for the old multi-image layouts
        p = _resolve_asset(vault, sp["file"])
        if p is None:
            continue
        w, h = _image_size(p)
        rel = str(p.resolve().relative_to(root)).replace("\\", "/")
        out.append({"path": rel, "caption": sp.get("caption", ""),
                    "attribution": sp.get("attribution", ""),
                    "w": w, "h": h, "aspect": _aspect(w, h)})
    return out, (str(pinned) if pinned else None)


def _node_page_pos(s: DwellSession, node_id: str, plan_key: str) -> int:
    """Stable 0-based ordinal of this LOGICAL page among the pages seen for this
    node, in first-seen order — the cursor into the node's page schedule (page 0 →
    schedule[0], page 1 → schedule[1], … wrapping after the schedule length).
    Re-leveling / coast / repage reuse the same plan.key() → same ordinal → same
    page (stable); only a genuinely new page of the node advances it. Prefetch never
    reaches here (it emits no `done`). Per-session; reset on action=='first'."""
    order = s.node_page_order.setdefault(node_id, [])
    if plan_key not in order:
        order.append(plan_key)
    return order.index(plan_key)


def _match_slots(slots: list[set], classes: list[str], uses: list[int]) -> list[int] | None:
    """Assign DISTINCT pool images to a composition's ordered slots so each slot
    gets a class-compatible image (backtracking → finds an assignment if one exists,
    not just a greedy one), preferring least-shown images. Returns slot→index in
    slot order, or None if the pool can't satisfy every slot."""
    assign: list[int] = []

    def bt(si: int) -> bool:
        if si == len(slots):
            return True
        cand = sorted((i for i in range(len(classes))
                       if classes[i] in slots[si] and i not in assign),
                      key=lambda i: (uses[i], i))
        for i in cand:
            assign.append(i)
            if bt(si + 1):
                return True
            assign.pop()
        return False

    return assign[:] if bt(0) else None


def _build_image_schedule(classes: list[str]) -> list[tuple[str, list[int]]]:
    """A deterministic, repeating sequence of page compositions for a node's image
    pool, each `(layout, [pool indices in figure order])`. Properties:
      • Every image appears at least once per cycle (no image is ever stranded).
      • Single-image pages alternate with multi-image pages, so the richer
        compositions appear automatically — but ONLY where the images fit the slots
        (else that slot falls back to a single page). Nothing is force-cropped.
      • Pure deterministic (least-shown + index tiebreak), so the same page ordinal
        always maps to the same composition — no caching needed.
    Indexed by the page's ordinal on the node (`_node_page_pos`)."""
    n = len(classes)
    if n == 0:
        return []
    if n == 1:
        return [(_SINGLE_OPTS[classes[0]][0], [0])]
    uses = [0] * n
    sched: list[tuple[str, list[int]]] = []
    guard = 0
    while (min(uses) == 0 or len(sched) < n) and guard < 4 * n + 8:
        guard += 1
        comp: tuple[str, list[int]] | None = None
        if len(sched) % 2 == 1:                     # every other page: try a multi-image composition
            for lay, slots in _MULTI_SLOTS:
                idx = _match_slots(slots, classes, uses)
                if idx is not None:
                    comp = (lay, idx)
                    break
        if comp is None:                            # single-image page: least-shown image, varied layout
            i = min(range(n), key=lambda i: (uses[i], i))
            opts = _SINGLE_OPTS[classes[i]]
            comp = (opts[uses[i] % len(opts)], [i])
        for i in comp[1]:
            uses[i] += 1
        sched.append(comp)
    return sched


def _page_images(s: DwellSession, node, plan=None) -> dict:
    """The `images[]` + `layout` block folded into a page's `done` event.

    AUTO is aspect-aware: it walks a per-node SCHEDULE (built from the pool's aspect
    ratios) that mixes single- and multi-image pages and cycles through every image,
    choosing a layout ONLY when each of its frames gets an orientation-compatible
    image. So a portrait is never forced into a landscape frame, the multi-image
    compositions still appear automatically when the images suit them, and dwelling
    on a topic rotates through all of its pictures. An explicit frontmatter pin
    overrides the schedule (author's deliberate choice)."""
    if node is None:
        return {"images": [], "layout": None}
    images, pinned = _node_images(s, node)
    if not images:
        return {"images": [], "layout": None}

    def served(im: dict) -> dict:
        return {"url": f"/asset?session={s.id}&path={quote(im['path'])}",
                "caption": im["caption"], "attribution": im["attribution"],
                "w": im["w"], "h": im["h"], "aspect": im["aspect"]}

    pos = _node_page_pos(s, node.id, plan.key()) if plan is not None else 0

    # Author pin overrides AUTO. A multi-image pin renders the whole pool in that
    # composition; a single-image pin fixes the template but the image still cycles.
    if pinned in _MULTI_LAYOUTS:
        return {"layout": pinned, "images": [served(im) for im in images]}
    if pinned in _SINGLE_LAYOUTS:
        return {"layout": pinned, "images": [served(images[pos % len(images)])]}

    # AUTO: the aspect-aware schedule decides the layout + which image(s) this page shows.
    schedule = _build_image_schedule([_img_class(im) for im in images])
    layout, idxs = schedule[pos % len(schedule)]
    return {"layout": layout, "images": [served(images[i]) for i in idxs]}


def _page_text_figure(s: DwellSession, node, plan, page_text: str, has_image: bool) -> dict:
    """The `text_figure` block folded into a page's `done` event — a DERIVED text-figure
    (drop-cap / pull-quote / …) for a no-image page, or None. Deterministic by the page's
    stable ordinal on its node (so re-pitch/coast don't flicker), gated by the active form's
    affinity + a density dial. See text_figures.choose_text_figure."""
    if node is None or plan is None:
        return {"text_figure": None}
    pos = _node_page_pos(s, node.id, plan.key())
    fig = choose_text_figure(page_text, s.renderer.form, pos, node.id,
                             has_image=has_image, density=s.text_fig_density)
    return {"text_figure": fig}


def _voices_payload(s: DwellSession) -> dict:
    vault_voices = sorted(s.brain.voice_profiles)
    presets = [v for v in VOICES if v not in s.brain.voice_profiles]
    # Per-preset metadata for the UI: a one-line PURPOSE (so the picker shows what each
    # voice is for, not just a name) and the paired spoken voice (`tts`) so karaoke
    # narration can match the written voice.
    cards = {v: {"purpose": VOICES[v].purpose, "tts": VOICES[v].tts} for v in presets}
    return {
        "vault_voices": vault_voices,
        "presets": presets,
        "cards": cards,
        "default": s.brain.voice_default,
        "current": s.renderer.voice,
        "current_id": s.renderer.voice_id,
        "current_tts": (s.renderer.voice_card.tts if s.renderer.voice_card else None),
    }


def _branches(s: DwellSession) -> list[dict]:
    """Recompute the reader-facing directions and (re)stash their plans by id.
    `Navigator.propose` is non-mutating and consumes no RNG, so it is safe to call
    repeatedly. Each branch reports whether its render is already cached (ready)."""
    out: list[dict] = []
    s.proposed.clear()
    if s.nav is None:
        return out
    for plan, label in s.nav.propose(3):
        pid = plan.key()
        s.proposed[pid] = plan
        ready = s.cache.get(s.renderer.cache_key(plan)) is not None
        out.append({
            "plan_id": pid,
            "label": label,
            "mode": plan.mode,            # open | dwell | move
            "node": plan.node,
            "title": plan.title,
            "ready": ready,               # cached → instant + free
            "leap": label.startswith("✧"),  # ✧ unexpected (near-but-unlinked)
        })
    return out


# ---------------------------------------------------------------------------
# The async ⇄ thread SSE bridge
# ---------------------------------------------------------------------------
async def _sse_from_thread(loop: asyncio.AbstractEventLoop, produce):
    """Run blocking `produce(emit)` on a worker thread; yield the (kind, payload)
    events it emits, in order, until it finishes. `emit` is thread-safe (it hops
    back onto the loop via call_soon_threadsafe). The worker is detached: if the
    client disconnects mid-stream the render still completes and caches itself, so
    the tokens already paid for are not wasted."""
    aq: asyncio.Queue = asyncio.Queue()

    def emit(kind: str, payload) -> None:
        loop.call_soon_threadsafe(aq.put_nowait, (kind, payload))

    def worker() -> None:
        try:
            produce(emit)
        except Exception as exc:  # noqa: BLE001 — surface as an SSE error event
            emit("error", {"message": str(exc)})
        finally:
            emit("__end__", None)

    loop.run_in_executor(None, worker)
    while True:
        kind, payload = await aq.get()
        if kind == "__end__":
            return
        yield kind, payload


def _sse_event(kind: str, payload) -> dict:
    return {"event": kind, "data": json.dumps(payload, ensure_ascii=False)}


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    for s in list(SESSIONS.values()):   # flush all reading memory + caches on exit
        s.flush()


app = FastAPI(title="Dwell", version="0.1.0", lifespan=lifespan)
app.add_middleware(                      # permissive for local dev / a separate Vite frontend
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Learn — vault-builder intake (/learn/*) + the ingest swarm (/learn/build).
from dwell_learn import router as learn_router  # noqa: E402
from dwell_build import router as build_router  # noqa: E402
from dwell_endpoints import router as endpoints_router, reader_router  # noqa: E402 — Models & Keys
app.include_router(learn_router)
app.include_router(build_router)
app.include_router(endpoints_router)
app.include_router(reader_router)

# Serve the built frontend's hashed JS/CSS (single-server mode). Mounted at a
# specific prefix so it never shadows the API routes; index.html is served by "/".
if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "sessions": len(SESSIONS), "vault_root": str(VAULT_ROOT)}


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    # Single-server mode: serve the built Svelte app (web/dist) when present,
    # falling back to the legacy test client, then a bare status page.
    dist_index = DIST / "index.html"
    if dist_index.exists():
        return HTMLResponse(dist_index.read_text(encoding="utf-8"))
    if WEB_CLIENT.exists():
        return HTMLResponse(WEB_CLIENT.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>Dwell server</h1><p>API is up, but no frontend was found. Build it with "
        "<code>npm run build</code> in <code>web/</code>.</p>")


@app.get("/asset")
def asset(session: str, path: str) -> FileResponse:
    """Serve a vault image file referenced by a page. `path` is vault-root-relative
    and validated to stay inside the session's vault (no traversal escape)."""
    s = _require_session(session)
    root = VaultPaths.for_vault(s.vault_path).root.resolve()
    target = (root / path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(status_code=403, detail="asset outside vault")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(target)


# ---- vault-selection (hero) card metadata ----------------------------------
_COVER_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _vault_cover_path(d: Path) -> Path | None:
    """A vault's EXPLICIT cover image (no auto-pick): a `cover.<ext>` at the vault
    root or under `_meta/` / `wiki/_meta/`. Authors designate it deliberately; a
    vault with none falls back to a themed gradient card on the client."""
    for base in (d, d / "_meta", d / "wiki" / "_meta"):
        for ext in _COVER_EXTS:
            p = base / f"cover{ext}"
            try:
                if p.is_file():
                    return p
            except Exception:
                pass
    return None


def _vault_blurb(d: Path) -> str:
    """One-line description for the vault card. Prefer index.md's italic intro line
    (minus its trailing '· updated … · N pages' metadata); else the CLAUDE.md H1."""
    try:
        text = (d / "index.md").read_text(encoding="utf-8")
        m = re.search(r"^\s*\*([^*\n]{12,})\*\s*$", text, re.MULTILINE)
        if m:
            blurb = re.split(r"\s*·\s*(?:updated\b|\d+\s+pages\b)", m.group(1).strip())[0]
            return blurb.strip()[:240]
    except Exception:
        pass
    try:
        cm = (d / "CLAUDE.md").read_text(encoding="utf-8")
        h1 = re.search(r"^#\s+(.+)$", cm, re.MULTILINE)
        if h1:
            return re.sub(r"^Vault Schema\s*[—-]\s*", "", h1.group(1).strip())[:240]
    except Exception:
        pass
    return ""


# Files that live in raw/ alongside sources but aren't themselves sources.
def _is_source_artifact(name: str) -> bool:
    low = name.lower()
    return (name.startswith(".") or low.endswith(".claude-baseline")
            or low.endswith(".extracted.txt"))


def _vault_source_list(d: Path) -> list[dict]:
    """Logical source documents (raw/<kind>/* except assets), for the detail view — each
    as {name (stem), kind (the raw subdir), exts (formats kept)}. Format variants of ONE
    document (e.g. a paper kept as both .pdf and .md) collapse to a single entry, and
    internal artifacts (.claude-baseline, *.extracted.txt, dotfiles) are skipped. Keying
    by (kind, stem) also guarantees the list has no duplicate identity for the UI."""
    by_key: dict[tuple[str, str], dict] = {}
    raw = d / "raw"
    try:
        for sub in sorted(raw.iterdir()):
            if not sub.is_dir() or sub.name == "assets":
                continue
            for f in sorted(sub.iterdir()):
                if not f.is_file() or _is_source_artifact(f.name):
                    continue
                key = (sub.name, f.stem)
                ext = f.suffix.lstrip(".")
                entry = by_key.setdefault(key, {"name": f.stem, "kind": sub.name, "exts": []})
                if ext and ext not in entry["exts"]:
                    entry["exts"].append(ext)
    except Exception:
        pass
    return list(by_key.values())


def _vault_sources(d: Path) -> int:
    """Count of logical source documents (deduped format variants, minus artifacts)."""
    return len(_vault_source_list(d))


@app.get("/vault-cover")
def vault_cover(vault: str) -> FileResponse:
    """Serve a vault's explicit cover image for the picker (no session needed).
    `vault` is a vault dir under the library root or a registered import."""
    d = _resolve_vault_any(vault)
    cover = _vault_cover_path(d)
    if cover is None:
        raise HTTPException(status_code=404, detail="no cover")
    return FileResponse(cover)


@app.get("/vault-sources")
def vault_sources_list(vault: str) -> dict:
    """List a vault's source documents for the detail view (no Brain load); `vault` is
    a vault dir under the library root or a registered import."""
    return {"sources": _vault_source_list(_resolve_vault_any(vault))}


# ---- vault discovery -------------------------------------------------------
# Imported (external) vault dirs are registered here so they show in the gallery without
# moving the user's files. A dotfile (not a dir) so the VAULT_ROOT scan ignores it.
_REGISTRY = VAULT_ROOT / ".dwell-vaults.json"


def _read_registry() -> list[str]:
    try:
        v = json.loads(_REGISTRY.read_text(encoding="utf-8"))
        return [str(p) for p in v] if isinstance(v, list) else []
    except Exception:
        return []


def _write_registry(paths: list[str]) -> None:
    try:
        _REGISTRY.write_text(json.dumps(sorted(set(paths)), indent=2), encoding="utf-8")
    except Exception:
        pass


def _resolve_vault_any(vault: str) -> Path:
    """Resolve a vault path, allowing the library root, its children, or a registered
    (imported) external; 403 otherwise."""
    d = Path(vault).resolve()
    root = VAULT_ROOT.resolve()
    if d == root or root in d.parents or any(Path(p).resolve() == d for p in _read_registry()):
        return d
    raise HTTPException(status_code=403, detail="unknown vault")


def _vault_entry(d: Path, imported: bool) -> dict | None:
    """Cheap probe of one vault dir → a gallery card dict, or None if it isn't a readable
    vault (needs CLAUDE.md + ≥1 wiki page). node count excludes sources/_meta; has_voice
    spots a `the-voice-of-*` page."""
    if not (d / "CLAUDE.md").is_file():
        return None
    wiki = d / "wiki"
    nodes, has_voice = 0, False
    for sub in ("concepts", "entities", "syntheses"):
        sd = wiki / sub
        if sd.is_dir():
            for f in sd.glob("*.md"):
                nodes += 1
                if f.stem.lower().startswith("the-voice-of"):
                    has_voice = True
    if nodes == 0:
        return None
    return {"path": str(d), "name": d.name, "nodes": nodes, "has_voice": has_voice,
            "topic": _vault_blurb(d), "sources": _vault_sources(d),
            "has_cover": _vault_cover_path(d) is not None, "imported": imported}


@app.get("/vaults")
def vaults() -> dict:
    """Vaults under VAULT_ROOT plus any imported (registered) external dirs — cheap scan,
    no Brain load."""
    out: list[dict] = []
    seen: set[Path] = set()
    try:
        entries = sorted(p for p in VAULT_ROOT.iterdir() if p.is_dir())
    except Exception:
        entries = []
    for d in entries:
        e = _vault_entry(d, imported=False)
        if e:
            out.append(e)
            seen.add(d.resolve())
    for p in _read_registry():
        d = Path(p).resolve()
        if d in seen:
            continue
        e = _vault_entry(d, imported=True)
        if e:
            out.append(e)
            seen.add(d)
    return {"root": str(VAULT_ROOT), "vaults": out}


class ImportReq(BaseModel):
    path: str


@app.post("/vault/import")
def vault_import(req: ImportReq) -> dict:
    """Register an existing vault folder (anywhere on disk) so it shows in the gallery —
    non-destructive. A vault already under VAULT_ROOT needs no registration."""
    d = Path(req.path.strip().strip('"')).expanduser().resolve()
    if not d.is_dir():
        raise HTTPException(status_code=400, detail="not a folder")
    entry = _vault_entry(d, imported=True)
    if entry is None:
        raise HTTPException(status_code=400, detail="not a knowledge base (needs CLAUDE.md and wiki pages)")
    # /vaults auto-discovers only DIRECT children of the root; register anything else
    # (nested-under-root or fully external) so it shows.
    if d.parent.resolve() != VAULT_ROOT.resolve():
        reg = _read_registry()
        if str(d) not in reg:
            reg.append(str(d))
            _write_registry(reg)
    else:
        entry["imported"] = False           # a direct child — already auto-discovered
    return {"ok": True, "vault": entry}


@app.delete("/vault")
def vault_delete(vault: str, purge: bool = False) -> dict:
    """Remove a vault. `purge=false` only FORGETS a registered external (files kept);
    `purge=true` DELETES the directory from disk. Guarded to known vault dirs."""
    d = Path(vault).resolve()
    reg = _read_registry()
    registered = any(Path(p).resolve() == d for p in reg)
    root = VAULT_ROOT.resolve()
    under_root = d == root or root in d.parents
    if not (registered or under_root):
        raise HTTPException(status_code=403, detail="unknown vault")
    if registered:
        _write_registry([p for p in reg if Path(p).resolve() != d])
    if purge:
        if d == root or not (d / "CLAUDE.md").is_file():
            raise HTTPException(status_code=400, detail="refusing to delete: not a vault dir")
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        for sid, s in list(SESSIONS.items()):       # drop any live session on it
            try:
                if Path(s.vault_path).resolve() == d:
                    SESSIONS.pop(sid, None)
            except Exception:
                pass
    return {"ok": True}


_COVER_MAX = 16 * 1024 * 1024            # 16 MB


def _clear_covers(d: Path) -> None:
    for base in (d, d / "_meta", d / "wiki" / "_meta"):
        for e in _COVER_EXTS:
            try:
                (base / f"cover{e}").unlink(missing_ok=True)
            except Exception:
                pass


@app.post("/vault/cover")
async def vault_set_cover(vault: str = Form(...), file: UploadFile = File(...)) -> dict:
    """Set a vault's cover image (`cover.<ext>` at the vault root). Replaces any existing
    cover. Accepts jpg/png/webp/gif."""
    d = _resolve_vault_any(vault)
    if not (d / "CLAUDE.md").is_file():
        raise HTTPException(status_code=404, detail="not a knowledge base")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _COVER_EXTS:
        raise HTTPException(status_code=400, detail="cover must be jpg, png, webp, or gif")
    data = await file.read()
    if not data or len(data) > _COVER_MAX:
        raise HTTPException(status_code=400, detail="empty image, or larger than 16 MB")
    _clear_covers(d)                      # avoid two cover.* with different extensions
    (d / f"cover{ext}").write_bytes(data)
    return {"ok": True, "has_cover": True}


@app.delete("/vault/cover")
def vault_remove_cover(vault: str) -> dict:
    _clear_covers(_resolve_vault_any(vault))
    return {"ok": True, "has_cover": False}


# ---- session lifecycle -----------------------------------------------------
class SessionReq(BaseModel):
    vault: str
    voice: str | None = None
    level: str | None = None         # reading level (elementary…scholar); None → general
    form: str | None = None          # output form (article/steps/qa/dialogue); None → article
    language: str | None = None      # output language (preset or free text); None → source
    engine: str | None = None        # "anthropic" | "mercury" (None → DWELL_PROVIDER)
    dry: bool = False


def _read_vault_mode(vault: VaultPaths) -> str:
    """Vault kind gates which behaviors are legal (academic vs narrative — see
    DWELL_APP_PLAN.md §7). Default academic if absent. Tolerant of a frontmatter
    `mode:` line or a `**Mode:** x` token in CLAUDE.md."""
    try:
        text = vault.claude_md.read_text(encoding="utf-8")
    except Exception:
        return "academic"
    for line in text.splitlines()[:40]:
        low = line.strip().lower()
        if low.startswith("mode:") or low.startswith("**mode:**"):
            val = line.split(":", 1)[1].strip().strip("*").strip().lower()
            if val:
                return val
    return "academic"


def _load_session(req: SessionReq) -> dict:
    vault = VaultPaths.for_vault(req.vault)
    if not vault.is_initialized():
        raise HTTPException(status_code=400,
                            detail="not a vault (no CLAUDE.md at that path)")
    notes: list[str] = []
    brain = Brain.load(vault, embed_model=None, progress=notes.append)
    voice = req.voice or brain.voice_default or DEFAULT_VOICE
    # Default engine: the request wins; else DWELL_PROVIDER from .env (the engine's
    # os.environ-only read misses .env because nothing loads it); else the
    # Renderer's own "anthropic" default. So `DWELL_PROVIDER=mercury` in .env makes
    # Mercury (free + fast) the default, as the plan documents.
    engine = req.engine or (_read_env_key("DWELL_PROVIDER") or None)
    # Mercury is the only reading engine; its key comes from Settings → Dwell (else .env).
    from dwell_endpoints import read_mercury_key as _mkey
    renderer = Renderer(brain.topic, req.dry, voice=voice,
                        vault_voices=brain.voice_profiles, provider=engine,
                        level=req.level or DEFAULT_LEVEL, form=req.form or DEFAULT_FORM,
                        language=req.language or DEFAULT_LANGUAGE,
                        mercury_key=_mkey() or None)
    cache = TweenCache(vault.meta / TWEEN_CACHE_FILE)
    history = ReadingHistory(vault.meta / HISTORY_FILE)
    history.start_session()

    sid = secrets.token_hex(8)
    now = time.time()
    s = DwellSession(id=sid, vault_path=str(vault.root), topic=brain.topic,
                     brain=brain, renderer=renderer, cache=cache, history=history,
                     rng=random.Random(7), created=now, last_used=now)
    SESSIONS[sid] = s

    menu: list[dict] = []
    if history.last and history.last.get("node") in brain.nodes:
        last = history.last["node"]
        menu.append({"key": "resume", "label": "Resume",
                     "hint": f"pick up near {brain.nodes[last].title}"})
    menu.append({"key": "new", "label": "Somewhere new", "hint": "central, but unread"})
    menu.append({"key": "surprise", "label": "Surprise me", "hint": "roam somewhere far"})

    return {
        "session_id": sid,
        "vault": s.vault_path,
        "topic": brain.topic,
        "mode": _read_vault_mode(vault),
        "nodes": len(brain.nodes),
        "embed_label": brain.embed_label,
        "provider": renderer.provider,
        "model": renderer.model or None,
        "dry": renderer.dry,
        "init_error": renderer.init_error or None,
        "menu": menu,
        "voices": _voices_payload(s),
        "level": renderer.level,
        "levels": list(LEVELS.keys()),
        "form": renderer.form,
        "forms": list(FORMS.keys()),
        "language": renderer.language,
        "languages": list(LANGUAGES.keys()),
        "notes": notes,
    }


@app.post("/session")
async def session(req: SessionReq) -> dict:
    _evict_stale()
    return await run_in_threadpool(_load_session, req)


@app.delete("/session")
async def close_session(session: str) -> dict:
    s = SESSIONS.pop(session, None)
    if s is not None:
        await run_in_threadpool(s.flush)
        return {"ok": True}
    return {"ok": False, "detail": "unknown session"}


@app.get("/state")
def state(session: str) -> dict:
    """Current driver state — handy for a client that (re)connects mid-session."""
    s = _require_session(session)
    cur = s.nav.current if s.nav is not None else None
    return {
        "started": s.nav is not None,
        "current": cur,
        "current_title": s.brain.nodes[cur].title if cur in s.brain.nodes else None,
        "recap": s.nav.recap() if s.nav is not None else "",
        "wander": s.wander,
        "cost": _cost(s),
        "provider": s.renderer.provider,
        "dry": s.renderer.dry,
        "voice": s.renderer.voice,
        "language": s.renderer.language,
        "branches": _branches(s),
    }


# ---- the page stream (the heart) -------------------------------------------
class PageReq(BaseModel):
    session: str
    action: str = "auto"              # first | auto | plan
    plan_id: str | None = None        # required when action == "plan"
    start: str = "new"                # central | new | surprise | resume (action==first)
    seed: str | None = None           # start at this exact node id (action==first)
    wander: float | None = None       # set the walk's step size on this/first page
    diffusing: bool = True            # each frame is the full refining text (Mercury)


def _produce_page(s: DwellSession, req: PageReq, emit) -> None:
    """Blocking page production, run on a worker thread. Mirrors the tkinter
    _beat_worker: resolve the plan, commit, predict the next page (to lean this one
    toward it), render (cache-first, streamed if live), then propose branches."""
    if req.action == "first":
        s.wander = DEFAULT_WANDER if req.wander is None else float(req.wander)
        seed = req.seed if (req.seed and req.seed in s.brain.nodes) else None
        s.nav = Navigator(s.brain, seed, s.wander, s.rng, s.history, start=req.start)
        s.tail = ""
        s.pending_plan = None
        s.proposed.clear()
        s.page_renders.clear()
        s.node_page_order.clear()                   # restart the per-node image-cycling cursors
    if s.nav is None:
        emit("error", {"message": "session not started — call /page with action=first"})
        return
    if req.wander is not None:                  # live wander update (slider)
        s.wander = float(req.wander)
        s.nav.wander = s.wander
    nav = s.nav

    if req.action == "first":
        plan = nav.plan_first()
    elif req.action == "plan":
        plan = s.proposed.get(req.plan_id or "")
        if plan is None:
            emit("error", {"message": "unknown or expired plan_id — refresh branches"})
            return
    else:  # auto / flow — reuse the predicted page if we have one (no extra RNG)
        plan = s.pending_plan or nav.plan_auto()

    nav.commit(plan)
    s.pending_plan = nav.plan_auto()            # predict the next page...
    hint = nav.hint_for(s.pending_plan)         # ...so this page leans toward it
    recap = nav.recap()
    key = s.renderer.cache_key(plan)
    # Remember this page's render context so it can be re-pitched to another level
    # in place (same plan/tail/recap, new level → new cache key).
    s.page_renders.append({"plan": plan, "tail": s.tail[-TAIL_CHARS:], "recap": recap, "hint": hint})

    emit("start", {"node": plan.node, "title": plan.title, "mode": plan.mode,
                   "steer_bucket": plan.steer_bucket, "diffusing": req.diffusing,
                   "form": s.renderer.form})

    cached = s.cache.get(key)
    if cached is not None:
        text, marker = cached, "coast"
        emit("frame", {"text": text})
    elif s.renderer.dry:
        text = s.renderer.render(plan, s.tail[-TAIL_CHARS:], recap, hint)
        s.cache.put(key, text)
        marker = "live"
        emit("frame", {"text": text})
    else:
        with s.render_lock:                     # serialize live render vs prefetch
            cached = s.cache.get(key)           # prefetch may have filled it meanwhile
            if cached is not None:
                text, marker = cached, "coast"
                emit("frame", {"text": text})
            else:
                text = s.renderer.render(
                    plan, s.tail[-TAIL_CHARS:], recap, hint,
                    on_stream=lambda full: emit("frame", {"text": full}),
                    diffusing=req.diffusing)
                s.cache.put(key, text)
                marker = "live"

    s.tail = text
    s.history.save()
    node = s.brain.nodes.get(plan.node)
    imgs = _page_images(s, node, plan)
    emit("done", {
        "text": text, "node": plan.node, "title": plan.title, "mode": plan.mode,
        "marker": marker, "recap": recap, "steer_bucket": plan.steer_bucket,
        "sources": _source_titles(s, node.sources) if node else [],
        "cost": _cost(s), "branches": _branches(s), "form": s.renderer.form,
        **imgs,
        **_page_text_figure(s, node, plan, text, has_image=bool(imgs.get("images"))),
    })


@app.post("/page")
async def page(req: PageReq):
    s = _require_session(req.session)
    loop = asyncio.get_running_loop()

    async def gen():
        async with s.alock:                     # one page op per session at a time
            async for kind, payload in _sse_from_thread(loop,
                                                         lambda emit: _produce_page(s, req, emit)):
                yield _sse_event(kind, payload)
        _schedule_prefetch(s)                    # warm the most-likely next page

    return EventSourceResponse(gen())


def _schedule_prefetch(s: DwellSession) -> None:
    """BALANCED look-ahead: render the predicted next page in the background so
    flowing forward (or clicking that branch) is instant. Captures a snapshot of
    tail/recap; never mutates the Navigator; double-checks the cache under the
    render lock so it never duplicates a foreground render."""
    plan = s.pending_plan
    if plan is None or s.renderer.dry:
        return
    key = s.renderer.cache_key(plan)
    if s.cache.get(key) is not None:
        return
    tail, recap = s.tail, (s.nav.recap() if s.nav is not None else "")

    def work() -> None:
        with s.render_lock:
            if s.cache.get(key) is not None:
                return
            try:
                text = s.renderer.render(plan, tail[-TAIL_CHARS:], recap, "")
                s.cache.put(key, text)
            except Exception:
                pass

    threading.Thread(target=work, daemon=True).start()


def _reproduce_page(s: DwellSession, index: int, emit) -> None:
    """Re-render an already-composed page at the renderer's CURRENT level/voice,
    WITHOUT advancing the navigator — an instant before/after when the reader changes
    level. Reuses the page's stored render context; cache-first per (voice, level)."""
    if s.nav is None or not (0 <= index < len(s.page_renders)):
        emit("error", {"message": "no such page to re-render"})
        return
    lr = s.page_renders[index]
    plan = lr["plan"]
    key = s.renderer.cache_key(plan)
    node = s.brain.nodes.get(plan.node)
    emit("start", {"node": plan.node, "title": plan.title, "mode": plan.mode,
                   "steer_bucket": plan.steer_bucket, "diffusing": True,
                   "form": s.renderer.form})
    cached = s.cache.get(key)
    if cached is not None:
        text, marker = cached, "coast"
        emit("frame", {"text": text})
    elif s.renderer.dry:
        text = s.renderer.render(plan, lr["tail"], lr["recap"], lr["hint"])
        s.cache.put(key, text)
        marker = "live"
        emit("frame", {"text": text})
    else:
        with s.render_lock:                     # share the live-render lock
            cached = s.cache.get(key)
            if cached is not None:
                text, marker = cached, "coast"
                emit("frame", {"text": text})
            else:
                text = s.renderer.render(
                    plan, lr["tail"], lr["recap"], lr["hint"],
                    on_stream=lambda full: emit("frame", {"text": full}), diffusing=True)
                s.cache.put(key, text)
                marker = "live"
    # NB: do NOT touch s.tail or the navigator — this is a re-pitch of one page.
    imgs = _page_images(s, node, plan)
    emit("done", {
        "text": text, "node": plan.node, "title": plan.title, "mode": plan.mode,
        "marker": marker, "recap": lr["recap"], "steer_bucket": plan.steer_bucket,
        "sources": _source_titles(s, node.sources) if node else [],
        "cost": _cost(s), "branches": _branches(s), "form": s.renderer.form,
        **imgs,
        **_page_text_figure(s, node, plan, text, has_image=bool(imgs.get("images"))),
    })


class RepageReq(BaseModel):
    session: str
    index: int = -1          # which composed page to re-pitch (-1 = the latest)


@app.post("/repage")
async def repage(req: RepageReq):
    s = _require_session(req.session)
    loop = asyncio.get_running_loop()
    idx = req.index if req.index >= 0 else len(s.page_renders) - 1

    async def gen():
        async with s.alock:
            async for kind, payload in _sse_from_thread(
                    loop, lambda emit: _reproduce_page(s, idx, emit)):
                yield _sse_event(kind, payload)

    return EventSourceResponse(gen())


# ---- steering / wander / branches ------------------------------------------
class SteerReq(BaseModel):
    session: str
    text: str


def _apply_steer(s: DwellSession, text: str) -> None:
    s.nav.apply_steering(text)       # embeds `text` through the vault's space
    s.pending_plan = None            # discard the prediction; the next page must bend
    s.proposed.clear()


@app.post("/steer")
async def steer(req: SteerReq) -> dict:
    s = _require_session(req.session)
    if s.nav is None:
        raise HTTPException(status_code=409, detail="session not started")
    async with s.alock:
        await run_in_threadpool(_apply_steer, s, req.text)
    return {"ok": True}


class WanderReq(BaseModel):
    session: str
    value: float


@app.post("/wander")
def wander(req: WanderReq) -> dict:
    s = _require_session(req.session)
    s.wander = max(0.0, min(1.0, float(req.value)))
    if s.nav is not None:
        s.nav.wander = s.wander
    return {"ok": True, "wander": s.wander}


@app.get("/branches")
def branches(session: str) -> dict:
    s = _require_session(session)
    return {"branches": _branches(s)}


# ---- select-to-expand (stretchtext) ----------------------------------------
class ExpandReq(BaseModel):
    session: str
    selected: str
    before: str = ""
    after: str = ""
    mode: str = "expand"             # expand | simplify | deeper


@app.post("/expand")
async def expand(req: ExpandReq):
    s = _require_session(req.session)
    if s.renderer.dry:
        raise HTTPException(status_code=409, detail="expand needs the LLM (not dry mode)")
    loop = asyncio.get_running_loop()

    def produce(emit) -> None:
        with s.render_lock:          # share the lock so expand doesn't collide with prefetch
            text = s.renderer.expand(
                req.selected, req.before, req.after, req.mode,
                on_stream=lambda full: emit("frame", {"text": full}))
        emit("done", {"text": text, "cost": _cost(s)})

    async def gen():
        async for kind, payload in _sse_from_thread(loop, produce):
            yield _sse_event(kind, payload)

    return EventSourceResponse(gen())


# ---- quiz: retrieval practice over recently-read pages ---------------------
class QuizReq(BaseModel):
    session: str
    pages: list[str] = []        # the page texts to quiz over (e.g. the previous 5)
    count: int = 5               # how many questions to ask
    types: list[str] = []        # enabled question formats (empty = all)


@app.post("/quiz")
async def quiz(req: QuizReq) -> dict:
    s = _require_session(req.session)
    if s.renderer.dry:
        raise HTTPException(status_code=409, detail="quiz needs the LLM (not dry mode)")
    questions = await run_in_threadpool(s.renderer.quiz, req.pages, int(req.count), req.types or None)
    return {"questions": questions, "cost": _cost(s)}


# ---- voices ----------------------------------------------------------------
@app.get("/voices")
def voices(session: str) -> dict:
    return _voices_payload(_require_session(session))


class VoiceReq(BaseModel):
    session: str
    name: str


@app.post("/voice")
def set_voice(req: VoiceReq) -> dict:
    s = _require_session(req.session)
    s.renderer.set_voice(req.name)   # resolution: vault voice → preset → free text
    return {"ok": True, "voice": s.renderer.voice, "voice_id": s.renderer.voice_id}


# ---- reading level ---------------------------------------------------------
class LevelReq(BaseModel):
    session: str
    name: str


@app.post("/level")
def set_level(req: LevelReq) -> dict:
    s = _require_session(req.session)
    s.renderer.set_level(req.name)   # elementary…scholar; unknown → general
    return {"ok": True, "level": s.renderer.level}


# ---- output form (rhetorical shape — re-pitched in place via /repage) -------
class FormReq(BaseModel):
    session: str
    name: str


@app.post("/form")
def set_form(req: FormReq) -> dict:
    s = _require_session(req.session)
    s.renderer.set_form(req.name)    # article/steps/qa/dialogue; unknown → article
    return {"ok": True, "form": s.renderer.form}


# ---- output language (the page's medium — re-pitched in place via /repage) --
class LanguageReq(BaseModel):
    session: str
    name: str


@app.post("/language")
def set_language(req: LanguageReq) -> dict:
    s = _require_session(req.session)
    s.renderer.set_language(req.name)   # preset or free text; 'source' → no translation
    return {"ok": True, "language": s.renderer.language}


# ---- popular nodes (sidebar) ----------------------------------------------
@app.get("/nodes")
def nodes(session: str, top: int = 14) -> dict:
    """Pages ranked by centrality (in-degree + out-degree) — the vault's hubs. The
    sidebar lists the top few ('Popular') and uses the full list (top=0) for search.
    Clicking one starts a thread seeded at that node."""
    s = _require_session(session)
    b = s.brain
    ranked = sorted(b.ids, key=lambda n: b.centrality(n), reverse=True)
    if top > 0:
        ranked = ranked[:top]
    return {"nodes": [{"id": n, "title": b.nodes[n].title,
                       "centrality": b.centrality(n),
                       "seen": s.history.seen_count(n)} for n in ranked]}


# ---- missed connections ----------------------------------------------------
@app.get("/missed")
async def missed(session: str, n: int = 20) -> dict:
    s = _require_session(session)
    pairs = await run_in_threadpool(missed_connections, s.brain, int(n))
    return {
        "embed_label": s.brain.embed_label,
        "pairs": [{"a": a, "b": b, "sim": round(sim, 3),
                   "title_a": s.brain.nodes[a].title,
                   "title_b": s.brain.nodes[b].title} for a, b, sim in pairs],
    }


# ---- timeline (Tier-2 view over the enrichment temporal sidecar) -----------
@app.get("/timeline")
def timeline(session: str, min_conf: float = 0.7) -> dict:
    """Chronological view built from `_meta/enrichment-temporal.json` (cli.py enrich).
    Events at/above `min_conf` (era-explicit dates+periods clear the default 0.7;
    bare citation-ish years are 0.55), sorted by year, each resolved to its node +
    title. The first Tier-2 consumer (DWELL_ENRICH_PLAN.md) — a re-traversal of the
    graph by time, not a restyle of one page."""
    s = _require_session(session)
    path = VaultPaths.for_vault(s.vault_path).enrichment_temporal_json
    if not path.exists():
        return {"available": False, "events": [], "count": 0,
                "note": "no enrichment yet — run: cli.py enrich --vault <path>"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "events": [], "count": 0, "note": "unreadable sidecar"}
    out: list[dict] = []
    for e in data.get("events", []) or []:
        year = e.get("year")
        if year is None or float(e.get("conf", 0)) < min_conf:
            continue
        pid = e.get("page", "")
        node = s.brain.nodes.get(pid)
        out.append({"year": int(year), "text": e.get("text", ""),
                    "kind": e.get("kind", "date"), "page": pid,
                    "title": node.title if node else pid,
                    "conf": round(float(e.get("conf", 0)), 2)})
    out.sort(key=lambda x: x["year"])
    return {"available": True, "count": len(out), "min_conf": min_conf,
            "topic": data.get("topic", ""), "events": out}


# ---- audio narration (Kokoro, server-side; streamed to the browser) --------
@app.get("/tts/voices")
def tts_voices() -> dict:
    ok, err = web_tts_available()
    return {"available": ok, "error": err or None,
            "voices": (list_web_voices() if ok else list(NARRATOR_VOICES)),
            "default": DEFAULT_NARRATOR_VOICE}


class TtsReq(BaseModel):
    text: str
    voice: str | None = None
    speed: float = 1.0
    session: str | None = None        # accepted but unused — narration is stateless


@app.post("/tts")
async def tts(req: TtsReq):
    """Synthesize `text` with Kokoro and stream it sentence-by-sentence as base64
    WAV clips, so the browser can start playing within ~1s and stitch them
    gaplessly. The model loads on the first call (~1s) on a worker thread."""
    ok, err = web_tts_available()
    if not ok:
        raise HTTPException(status_code=503, detail=f"TTS unavailable: {err}")
    loop = asyncio.get_running_loop()

    def produce(emit) -> None:
        for sentence, wav in synth_wavs(req.text, req.voice or DEFAULT_NARRATOR_VOICE, req.speed):
            emit("clip", {"text": sentence, "b64": base64.b64encode(wav).decode("ascii")})

    async def gen():
        async for kind, payload in _sse_from_thread(loop, produce):
            yield _sse_event(kind, payload)

    return EventSourceResponse(gen())


@app.exception_handler(HTTPException)
async def _http_exc(_req, exc: HTTPException):   # JSON errors everywhere
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/{full_path:path}")
def public_asset(full_path: str):
    """Serve the built frontend's top-level public files — the assets Vite copies to
    dist/ root (the DWELL logo mask, favicon, icons). Registered last so it never
    shadows the API routes above; a real 404 for anything that isn't a file (Dwell
    navigates by in-app state + hash, so there's no SPA URL fallback to fake)."""
    if DIST.is_dir() and full_path:
        try:
            resolved = (DIST / full_path).resolve()
            if resolved.is_file() and DIST.resolve() in resolved.parents:
                return FileResponse(str(resolved))
        except Exception:
            pass
    raise HTTPException(status_code=404, detail="not found")


def main() -> None:
    import uvicorn
    host = os.environ.get("DWELL_HOST", "127.0.0.1")
    # PORT (set by the preview tooling / most PaaS) wins; DWELL_PORT is the explicit
    # override; 8000 is the default.
    port = int(os.environ.get("PORT") or os.environ.get("DWELL_PORT") or "8000")
    print(f"  ~ Dwell server ~  http://{host}:{port}/   (vault root: {VAULT_ROOT})")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
