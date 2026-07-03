"""Learn — the ingest swarm (Phase 3). Drives the existing `compendium` ingest pipeline
(`cli.py ingest`) as CANCELLABLE subprocesses, one per source, streaming live progress over
SSE, with a Stop that kills the running process. The "swarm" is the compendium
IngestOrchestrator (orchestrator + Router/PageWriter subagents) — we don't rebuild it; we
sequence it over the draft's collected sources and surface progress + control.

A `dry` mode simulates the stages (no API, no subprocess) for fast UI verification.

Mounted by dwell_server. Self-contained except for reusing dwell_learn's vault helpers.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from dwell_learn import _safe_vault, _sources, _upload_dir

router = APIRouter(prefix="/learn", tags=["learn"])

REPO = Path(__file__).resolve().parent.parent          # cli.py lives at the repo root
CLI = REPO / "cli.py"
DEFAULT_MAX_COST = 5.0                                  # per-source budget cap (USD)
_DRY_STAGES = ["extracting text", "routing content", "writing pages", "linking", "grounding"]
# Large sources are CHUNKED before ingest (a full book won't fit one context — chunking,
# not agent count, is what handles big material; each chunk ingests in a fresh process).
PDF_SPLIT_PAGES = 40                                    # PDFs longer than this → split into chapters
TEXT_SPLIT_CHARS = 24_000                               # md/txt bigger than this → split on headings


def _count_pages(d: Path) -> int:
    n = 0
    wiki = d / "wiki"
    for sub in ("concepts", "entities", "syntheses"):
        sd = wiki / sub
        if sd.is_dir():
            n += sum(1 for _ in sd.glob("*.md"))
    return n


class IngestOpts:
    """Per-build ingest settings from the Learn settings tab (global defaults)."""
    def __init__(self, max_cost: float | None = DEFAULT_MAX_COST, total_cap: float | None = None,
                 model_orchestrator: str | None = None, model_writer: str | None = None,
                 model_mechanical: str | None = None, auto_explore: bool = True,
                 max_pages: int | None = None, endpoint_id: str | None = None):
        self.max_cost = max_cost                 # per-source cap (USD)
        self.total_cap = total_cap               # whole-build cap (USD); None = unlimited
        self.model_orchestrator = model_orchestrator   # → --model-strategic (Router)
        self.model_writer = model_writer               # → --model-synthesis (PageWriter)
        self.model_mechanical = model_mechanical       # → --model-mechanical
        self.auto_explore = auto_explore
        self.max_pages = max_pages               # per-source page cap
        self.endpoint_id = endpoint_id           # multi-provider: which LLM endpoint to ingest on


class BuildState:
    def __init__(self, vault: str, sources: list[dict], opts: "IngestOpts | None" = None):
        self.vault = vault
        self.sources = sources           # [{id, name, kind, path, status}]
        self.status = "running"          # running | done | cancelled | error
        self.cancel = False
        self.proc: subprocess.Popen | None = None
        self.pages_before = 0
        self.pages_after = 0
        self.cost = 0.0                  # running USD total across finished sources
        self.opts = opts or IngestOpts()


BUILDS: dict[str, BuildState] = {}


def _build_sources(d: Path, exclude: list[str] | None = None,
                   include: list[str] | None = None) -> list[dict]:
    """The draft's sources as a build worklist. Duplicates (already-ingested) start
    'skipped'; everything else 'queued'. `exclude` = source ids the reader UNCHECKED
    in the pending-sources list — they stay in the draft (still pending for a later
    build) but sit this one out as 'skipped'. `include` = vault-pending raw files
    ("vault:<kind>/<filename>") the reader explicitly OPTED IN — files already in
    raw/ but absent from the ingest registry (e.g. backfilled marathon vaults);
    opt-in only, so a vault with hundreds pending never builds them by accident."""
    ex = set(exclude or [])
    src = _sources(d)
    out: list[dict] = []
    for vid in include or []:
        if not vid.startswith("vault:") or "/" not in vid:
            continue
        kind, _, fname = vid[len("vault:"):].partition("/")
        base = (d / "wiki" / "_meta" / "proposals" if kind == "proposal"
                else d / "raw" / Path(kind).name)
        p = (base / Path(fname).name).resolve()
        if p.parent == base.resolve() and p.is_file():
            out.append({"id": vid, "name": fname, "kind": "file",
                        "path": str(p), "status": "queued"})
    for f in src["files"]:
        name = f["name"] + ("." + f["ext"] if f["ext"] else "")
        skip = f.get("status") == "duplicate" or f["id"] in ex
        out.append({"id": f["id"], "name": name, "kind": "file",
                    "path": str(_upload_dir(d) / name),
                    "status": "skipped" if skip else "queued"})
    for l in src["links"]:
        out.append({"id": l["id"], "name": l["name"], "kind": "link",
                    "path": l["name"],
                    "status": "skipped" if l["id"] in ex else "queued"})
    # A research prompt is buildable work too: web-research it + the graph's open nodes,
    # then ingest. Always queued (never a duplicate — it's re-runnable).
    prompt = (src.get("prompt") or "").strip()
    if prompt:
        label = prompt if len(prompt) <= 60 else prompt[:57] + "…"
        out.append({"id": "research", "name": label, "kind": "research",
                    "path": prompt,
                    "status": "skipped" if "research" in ex else "queued"})
    return out


# ---- large-source splitting (chunk before ingest) -------------------------
def _pdf_pages(path: Path) -> int:
    try:
        import fitz                                     # PyMuPDF (a pipeline dependency)
        with fitz.open(str(path)) as doc:
            return doc.page_count
    except Exception:
        return 0


def _chunk_markdown(text: str, target: int) -> list[str]:
    """Split markdown/text into ~`target`-char chunks on top-level (#/##) heading
    boundaries; fall back to fixed windows when there are no headings."""
    blocks: list[str] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if re.match(r"^#{1,2} ", line) and cur:
            blocks.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    if len(blocks) <= 1 and len(text) > target:         # no usable headings → fixed windows
        blocks = [text[i:i + target] for i in range(0, len(text), target)]
    chunks: list[str] = []
    buf = ""
    for b in blocks:
        if buf and len(buf) + len(b) > target:
            chunks.append(buf)
            buf = b
        else:
            buf = (buf + "\n" + b) if buf else b
    if buf:
        chunks.append(buf)
    return chunks


def _split_text(d: Path, path: Path) -> list[Path] | None:
    """Split a large .md/.txt into chunk files in raw/articles/. None if small enough."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if len(text) <= TEXT_SPLIT_CHARS:
        return None
    chunks = _chunk_markdown(text, TEXT_SPLIT_CHARS)
    if len(chunks) <= 1:
        return None
    out_dir = d / "raw" / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem)
    paths: list[Path] = []
    for i, c in enumerate(chunks, 1):
        p = out_dir / f"{base}.part{i:02d}.md"
        p.write_text(c, encoding="utf-8")
        paths.append(p)
    return paths


def _split_pdf(d: Path, path: Path) -> list[Path] | None:
    """Chapter-split a long PDF via `cli.py split-book` (native text extraction, no LLM
    cost). Returns the new raw/articles/*.md chunk files (before/after diff). None if the
    PDF is short or split produced nothing."""
    if _pdf_pages(path) < PDF_SPLIT_PAGES:
        return None
    articles = d / "raw" / "articles"
    before = set(articles.glob("*.md")) if articles.is_dir() else set()
    cmd = [sys.executable, str(CLI), "split-book", "--pdf", str(path), "--vault", str(d)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        subprocess.run(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=env, timeout=600)
    except Exception:
        return None
    after = set(articles.glob("*.md")) if articles.is_dir() else set()
    new = sorted(after - before)
    return new or None


def _expand_sources(d: Path, sources: list[dict], emit) -> list[dict]:
    """Replace oversized file sources with their chunks (big PDFs → chapters; big text →
    heading splits). Links and already-skipped duplicates pass through untouched."""
    out: list[dict] = []
    for s in sources:
        if s["status"] != "queued" or s["kind"] == "link":
            out.append(s)
            continue
        path = Path(s["path"])
        ext = path.suffix.lower()
        chunks: list[Path] | None = None
        try:
            if ext == ".pdf":
                chunks = _split_pdf(d, path)
            elif ext in (".md", ".markdown", ".txt"):
                chunks = _split_text(d, path)
        except Exception as exc:                        # noqa: BLE001
            emit("log", {"id": s["id"], "line": f"[split skipped] {exc}"})
        if chunks:
            emit("split", {"id": s["id"], "name": s["name"], "into": len(chunks)})
            for i, cp in enumerate(chunks, 1):
                out.append({"id": f"{s['id']}#part{i}", "name": cp.name, "kind": "chunk",
                            "path": str(cp), "status": "queued"})
        else:
            out.append(s)
    return out


# Human-readable one-liner for each phase. Each names the AGENT doing the work, so the
# delegation is visible: the Planner (Router) maps pages, the Writer (PageWriter) drafts
# them, the Reviewer checks, the Explorer suggests expansions — distinct agents/models,
# NOT one orchestrator doing everything.
def _phase_activity(p: dict) -> str | None:
    ph = p.get("phase")
    if ph == "route":
        return "Planner reading the source & mapping pages…"
    if ph == "planned":
        n = p.get("pages", 0)
        return f"Planner mapped {n} page{'' if n == 1 else 's'}" + (f" · {', '.join(p['titles'][:3])}" if p.get("titles") else "")
    if ph == "write":
        return f"Writer drafting page {p.get('i')}/{p.get('n')}" + (f" · {p['title']}" if p.get("title") else "")
    if ph == "review":
        n = p.get("n", 0)
        return f"Reviewer checking {n} page{'' if n == 1 else 's'}"
    if ph == "explore":
        return "Explorer finding connections…"
    if ph == "explored":
        n = p.get("proposals", 0)
        return f"Explorer found {n} expansion idea{'' if n == 1 else 's'}"
    if ph == "done":
        return f"Finished · {p.get('created', 0)} created, {p.get('updated', 0)} updated"
    return None


def _ingest_one(state: BuildState, emit, s: dict, vault: str, dry: bool) -> bool:
    is_research = s.get("kind") == "research"
    if dry:
        if is_research:
            sim = [("Searching the web for your prompt…", 0.0),
                   ("Found 4 sources · reading…", 0.01),
                   ("Exploring the graph's open nodes…", 0.02),
                   ("Writing 3 new pages…", 0.04),
                   ("Finished · 3 created", 0.05)]
            cur = 0.0
            for act, c in sim:
                if state.cancel:
                    return False
                cur = c
                emit("progress", {"id": s["id"], "phase": "research", "activity": act, "cost": cur})
                emit("cost", {"total": round(state.cost + cur, 4), "source": round(cur, 4)})
                time.sleep(0.4)
            state.cost = round(state.cost + cur, 4)
            return True
        # Simulate the orchestrator's phase stream so the UI (activity line + cost
        # ticker) can be verified end-to-end without spending on the API.
        sim2 = [
            ("route", {"msg": "Reading the source and planning pages", "cost": 0.0}),
            ("planned", {"pages": 3, "titles": ["First Concept", "Second Concept", "A Synthesis"], "cost": 0.004}),
            ("write", {"i": 1, "n": 3, "title": "First Concept", "cost": 0.012}),
            ("write", {"i": 2, "n": 3, "title": "Second Concept", "cost": 0.021}),
            ("write", {"i": 3, "n": 3, "title": "A Synthesis", "cost": 0.030}),
            ("review", {"n": 3, "cost": 0.034}),
            ("explore", {"msg": "Exploring connections", "cost": 0.038}),
            ("explored", {"proposals": 4, "cost": 0.041}),
            ("done", {"created": 3, "updated": 0, "cost": 0.041}),
        ]
        cur = 0.0
        for phase, payload in sim2:
            if state.cancel:
                return False
            payload = {"phase": phase, **payload}
            cur = float(payload.get("cost") or cur)
            act = _phase_activity(payload)
            if act:
                emit("progress", {"id": s["id"], "phase": phase, "activity": act, "cost": cur})
            emit("cost", {"total": round(state.cost + cur, 4), "source": round(cur, 4)})
            time.sleep(0.4)
        state.cost = round(state.cost + cur, 4)
        return True

    o = state.opts
    # Per-source cap, never letting one source exceed what remains under the total cap.
    cap = o.max_cost
    if o.total_cap is not None:
        remaining = max(0.0, o.total_cap - state.cost)
        cap = remaining if cap is None else min(cap, remaining)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    if o.max_pages:
        env["COMPENDIUM_MAX_PAGES_PER_INGEST"] = str(int(o.max_pages))
    # Multi-provider: point the pipeline at the chosen endpoint. Non-Anthropic →
    # the compat shim; Anthropic → the SDK at that base. No endpoint = default .env.
    if o.endpoint_id:
        from dwell_endpoints import resolve_endpoint
        from compendium.llm.providers import detect_provider
        ep = resolve_endpoint(o.endpoint_id)
        if ep:
            env["COMPENDIUM_LLM_BASE_URL"] = ep["base_url"]
            env["COMPENDIUM_LLM_API_KEY"] = ep.get("api_key", "")
            env["COMPENDIUM_LLM_PROVIDER"] = detect_provider(ep["base_url"])

    if is_research:
        # A research prompt → web-research it + the graph's open nodes, then ingest, via a
        # single loop pass. Needs a search provider (UI store, else .env).
        from dwell_endpoints import read_search_config, search_available
        sc = read_search_config()
        if sc["provider"] == "jina" and sc["api_key"]:
            # Jina = its own key (Jina Search fallback + Reader); not a search_provider.
            env["JINA_API_KEY"] = sc["api_key"]
        elif sc["provider"] in ("tavily", "brave") and sc["api_key"]:
            env["COMPENDIUM_SEARCH_PROVIDER"] = sc["provider"]
            env["COMPENDIUM_SEARCH_API_KEY"] = sc["api_key"]
        elif not search_available():
            emit("log", {"id": s["id"], "line": "[error] No web search provider configured — "
                         "add one in Learn settings → Web search to use a research prompt."})
            return False
        cmd = [sys.executable, str(CLI), "loop", s["path"], "--vault", vault,
               "--max-iterations", "1", "--auto", "3", "--no-lint"]
    else:
        cmd = [sys.executable, str(CLI), "ingest", s["path"], "--vault", vault,
               "--allow-skip", "--json-progress"]
        if not o.auto_explore:
            cmd += ["--no-explore"]
    if cap is not None:
        cmd += ["--max-cost", str(round(cap, 4))]
    if o.model_orchestrator:
        cmd += ["--model-strategic", o.model_orchestrator]
    if o.model_writer:
        cmd += ["--model-synthesis", o.model_writer]
    if o.model_mechanical:
        cmd += ["--model-mechanical", o.model_mechanical]

    # stdin=DEVNULL so the child can never block on an inherited stdin handle;
    # unbuffered so its progress streams to us live.
    state.proc = subprocess.Popen(
        cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
    cur_cost = 0.0
    try:
        for line in state.proc.stdout:                 # stream the pipeline's output
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("@@PROG@@"):            # a structured phase event (ingest)
                try:
                    p = json.loads(line[len("@@PROG@@"):])
                except Exception:
                    continue
                if p.get("cost") is not None:
                    cur_cost = float(p["cost"])
                act = _phase_activity(p)
                if act:
                    emit("progress", {"id": s["id"], "phase": p.get("phase"),
                                      "activity": act, "cost": cur_cost})
                emit("cost", {"total": round(state.cost + cur_cost, 4), "source": round(cur_cost, 4)})
            else:                                       # raw console chatter
                emit("log", {"id": s["id"], "line": line[:300]})
                if is_research:                         # loop has no @@PROG@@ — scrape its cost line
                    m = re.search(r"\$([0-9]+\.[0-9]{2,})", line)
                    if m:
                        cur_cost = float(m.group(1))
                        emit("cost", {"total": round(state.cost + cur_cost, 4), "source": round(cur_cost, 4)})
        state.proc.wait()
        rc = state.proc.returncode
    finally:
        state.proc = None
    state.cost = round(state.cost + cur_cost, 4)        # bank this source's spend
    return rc == 0


def _run_build(state: BuildState, emit, dry: bool, resume: bool = False) -> None:
    d = Path(state.vault)
    cap = state.opts.total_cap
    capped = False
    if not resume:                                      # fresh build: snapshot + chunk oversized sources
        state.pages_before = _count_pages(d)
        emit("preparing", {})                           # (no LLM cost)
        if not state.cancel:
            state.sources = _expand_sources(d, state.sources, emit)
    emit("build-start", {"vault": state.vault, "resume": resume,
                         "sources": [{"id": s["id"], "name": s["name"], "kind": s["kind"], "status": s["status"]} for s in state.sources]})
    for s in state.sources:
        if state.cancel:
            break
        if s["status"] in ("skipped", "done"):          # already settled (resume keeps finished work)
            emit("source", {"id": s["id"], "status": s["status"]})
            continue
        if cap is not None and state.cost >= cap:        # total budget exhausted → stop, leave the rest queued
            capped = True
            break
        s["status"] = "ingesting"
        emit("source", {"id": s["id"], "status": "ingesting"})
        try:
            ok = _ingest_one(state, emit, s, state.vault, dry)
            s["status"] = "done" if ok else ("cancelled" if state.cancel else "failed")
        except Exception as exc:                        # noqa: BLE001
            s["status"] = "failed"
            emit("log", {"id": s["id"], "line": f"[error] {exc}"})
        emit("source", {"id": s["id"], "status": s["status"]})
        # An ACCEPTED ghost-door proposal is now real vault material — retire the
        # draft to proposals/accepted/ so it stops listing as pending (rejection
        # stays manual: delete the file). Not on dry runs — nothing was ingested.
        if (s["status"] == "done" and not dry
                and s["id"].startswith("vault:proposal/")):
            try:
                p = Path(s["path"])
                acc = p.parent / "accepted"
                acc.mkdir(parents=True, exist_ok=True)
                os.replace(p, acc / p.name)
                emit("log", {"id": s["id"],
                             "line": f"proposal accepted → proposals/accepted/{p.name}"})
            except Exception:
                pass
        if cap is not None and state.cost >= cap and any(x["status"] == "queued" for x in state.sources):
            capped = True
            break

    state.pages_after = _count_pages(d)
    if state.cancel:
        state.status = "cancelled"
    elif capped:
        state.status = "capped"
        emit("error", {"message": f"Total cost cap ${cap:.2f} reached — stopped with "
                                  f"{sum(1 for x in state.sources if x['status'] == 'queued')} source(s) left. "
                                  "Raise the cap in Learn settings, then Resume."})
    elif any(s["status"] == "failed" for s in state.sources):
        state.status = "error"
    else:
        state.status = "done"
    emit("build-done", {"status": state.status, "pages": state.pages_after,
                        "added": state.pages_after - state.pages_before,
                        "cost": round(state.cost, 4)})


# ---- SSE bridge (same shape as dwell_server's) -----------------------------
async def _sse_from_thread(loop: asyncio.AbstractEventLoop, produce):
    aq: asyncio.Queue = asyncio.Queue()

    def emit(kind: str, payload) -> None:
        loop.call_soon_threadsafe(aq.put_nowait, (kind, payload))

    def worker() -> None:
        try:
            produce(emit)
        except Exception as exc:                        # noqa: BLE001
            emit("error", {"message": str(exc)})
        finally:
            emit("__end__", None)

    loop.run_in_executor(None, worker)
    while True:
        kind, payload = await aq.get()
        if kind == "__end__":
            return
        yield {"event": kind, "data": json.dumps(payload, ensure_ascii=False)}


# ---- endpoints -------------------------------------------------------------
class BuildReq(BaseModel):
    vault: str
    dry: bool = False
    exclude: list[str] = []           # source ids unchecked in the pending list
    include: list[str] = []           # vault-pending raw files OPTED IN ("vault:<kind>/<file>")
    max_cost: float | None = DEFAULT_MAX_COST     # per-source cap
    total_cap: float | None = None                # whole-build cap; None = unlimited
    model_orchestrator: str | None = None
    model_writer: str | None = None
    model_mechanical: str | None = None
    auto_explore: bool = True
    max_pages: int | None = None
    endpoint_id: str | None = None
    resume: bool = False


def _opts_from(req: "BuildReq") -> IngestOpts:
    return IngestOpts(
        max_cost=req.max_cost, total_cap=req.total_cap,
        model_orchestrator=req.model_orchestrator, model_writer=req.model_writer,
        model_mechanical=req.model_mechanical, auto_explore=req.auto_explore,
        max_pages=req.max_pages, endpoint_id=req.endpoint_id,
    )


@router.post("/build")
async def learn_build(req: BuildReq):
    d = _safe_vault(req.vault)
    if not (d / "CLAUDE.md").is_file():
        raise HTTPException(status_code=404, detail="not a knowledge base")
    key = str(d)
    existing = BUILDS.get(key)
    if existing is not None and existing.status == "running":
        raise HTTPException(status_code=409, detail="a build is already running for this knowledge base")

    # Resume: continue the SAME build state — finished sources keep their 'done'
    # status (skipped), the interrupted source + any not-yet-started ones re-run.
    # This is exact (we know what finished this session) rather than relying on
    # content-hash dedup, which doesn't cover uploaded .md/.txt.
    resume = bool(req.resume) and existing is not None and existing.status in ("cancelled", "error", "capped", "done")
    if resume:
        state = existing
        state.cancel = False
        state.status = "running"
        state.opts = _opts_from(req)                    # apply the latest settings (e.g. a raised cap)
        for s in state.sources:
            if s["status"] in ("cancelled", "failed"):  # the interrupted source re-runs from the top
                s["status"] = "queued"
        if not any(s["status"] == "queued" for s in state.sources):
            raise HTTPException(status_code=400, detail="nothing left to build — every source finished")
    else:
        state = BuildState(vault=key, sources=_build_sources(d, exclude=req.exclude,
                                                             include=req.include),
                           opts=_opts_from(req))
        if not any(s["status"] == "queued" for s in state.sources):
            raise HTTPException(status_code=400, detail="nothing new to build (all sources already ingested)")
    BUILDS[key] = state
    loop = asyncio.get_running_loop()

    def produce(emit):
        _run_build(state, emit, req.dry, resume=resume)

    async def gen():
        async for ev in _sse_from_thread(loop, produce):
            yield ev

    return EventSourceResponse(gen())


@router.post("/build/stop")
def learn_build_stop(req: BuildReq) -> dict:
    st = BUILDS.get(str(_safe_vault(req.vault)))
    if st is None:
        raise HTTPException(status_code=404, detail="no build for this knowledge base")
    st.cancel = True
    if st.proc is not None:
        try:
            st.proc.kill()                              # unblocks the stdout read → loop ends
        except Exception:
            pass
    return {"ok": True}


@router.get("/build/state")
def learn_build_state(vault: str) -> dict:
    st = BUILDS.get(str(_safe_vault(vault)))
    if st is None:
        return {"running": False, "status": None, "sources": []}
    return {"running": st.status == "running", "status": st.status,
            "sources": st.sources, "cost": round(st.cost, 4)}
