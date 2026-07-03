"""Learn — build a knowledge base from the web UI (APIRouter mounted by dwell_server).

Phase 2 = INTAKE (gather → curate). Create a draft vault, stash uploaded files into
`raw/uploads/`, record web/video links + a research prompt in a `_meta/learn.json`
manifest, and list/remove sources so the user can curate before committing. The ingest
swarm (the "commit" step) is Phase 3.

Self-contained on purpose (no import of dwell_server) so there's no import cycle — it
re-reads VAULT_ROOT from the same env var and talks to the `compendium` package directly.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/learn", tags=["learn"])

VAULT_ROOT = Path(os.environ.get("DWELL_VAULT_ROOT") or str(Path.home() / "Dwell"))
MANIFEST = "learn.json"               # under <vault>/_meta/
UPLOAD_KIND = "uploads"               # raw/<UPLOAD_KIND>/
_ALLOWED_EXT = {".pdf", ".md", ".markdown", ".txt"}
_MAX_BYTES = 64 * 1024 * 1024         # 64 MB per file


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return s or "untitled"


def _registry_paths() -> set[str]:
    """Imported/external vaults registered by dwell_server (same .dwell-vaults.json)."""
    try:
        v = json.loads((VAULT_ROOT / ".dwell-vaults.json").read_text(encoding="utf-8"))
        return {str(Path(p).resolve()) for p in v} if isinstance(v, list) else set()
    except Exception:
        return set()


def _safe_vault(vault: str) -> Path:
    """Resolve a vault path; allow vaults under VAULT_ROOT or registered (imported)
    externals, refuse anything else (path-traversal guard)."""
    root = VAULT_ROOT.resolve()
    d = Path(vault).resolve()
    if d == root or root in d.parents or str(d) in _registry_paths():
        return d
    raise HTTPException(status_code=403, detail="vault outside library")


def _topic_of(d: Path) -> str:
    try:
        for line in (d / "CLAUDE.md").read_text(encoding="utf-8").splitlines():
            if line.startswith("# Vault Schema"):
                return line.partition("—")[2].strip() or d.name
    except Exception:
        pass
    return d.name


def _upload_dir(d: Path) -> Path:
    return d / "raw" / UPLOAD_KIND


def _manifest_path(d: Path) -> Path:
    return d / "_meta" / MANIFEST


def _read_manifest(d: Path) -> dict:
    try:
        return json.loads(_manifest_path(d).read_text(encoding="utf-8"))
    except Exception:
        return {"status": "draft", "topic": "", "prompt": "", "links": []}


def _write_manifest(d: Path, m: dict) -> None:
    p = _manifest_path(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, indent=2), encoding="utf-8")


def _safe_name(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "").name) or "file"


def _hash_file(p: Path) -> str:
    """Content hash — use the compendium hasher (so it matches the ingest registry)
    with a sha256 fallback."""
    try:
        from compendium.vault import hash_file
        return hash_file(p)
    except Exception:
        import hashlib
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()


def _existing_source_hashes(d: Path) -> set[str]:
    """Content hashes of sources ALREADY in the vault's raw/ (excluding the new uploads and
    asset images) — for spotting an identical re-upload when expanding."""
    out: set[str] = set()
    raw = d / "raw"
    try:
        for sub in raw.iterdir():
            if not sub.is_dir() or sub.name in (UPLOAD_KIND, "assets"):
                continue
            for f in sub.iterdir():
                low = f.name.lower()
                if f.is_file() and not f.name.startswith(".") and not low.endswith((".claude-baseline", ".extracted.txt")):
                    try:
                        out.add(_hash_file(f))
                    except Exception:
                        pass
    except Exception:
        pass
    return out


def _ingest_registry(d: Path):
    """The vault's content-hash ingest registry, if it has one (None otherwise)."""
    try:
        from compendium.vault import IngestRegistry, VaultPaths
        return IngestRegistry(VaultPaths.for_vault(str(d)))
    except Exception:
        return None


def _sources(d: Path) -> dict:
    """Uploaded files + manifest links + prompt — the curate-step state. Each upload is
    flagged `status`: 'duplicate' when its content hash matches a source already ingested
    into this vault (or another upload in the batch), else 'new'. Duplicates are skipped at
    ingest, so the reader can drop them now."""
    up = _upload_dir(d)
    try:
        upfiles = sorted(f for f in up.iterdir() if f.is_file())
    except Exception:
        upfiles = []

    existing: set[str] = set()
    reg = None
    if upfiles:                              # only pay the hashing cost when there's something to check
        existing = _existing_source_hashes(d)
        reg = _ingest_registry(d)

    files, seen = [], set()
    for f in upfiles:
        try:
            h = _hash_file(f)
        except Exception:
            h = ""
        status = "new"
        if h and (h in seen or h in existing):
            status = "duplicate"
        elif h and reg is not None:
            try:
                if reg.find_by_hash(h) is not None or reg.is_tombstoned(hash=h) is not None:
                    status = "duplicate"
            except Exception:
                pass
        if h:
            seen.add(h)
        files.append({"id": "file:" + f.name, "name": f.stem, "ext": f.suffix.lstrip("."),
                      "size": f.stat().st_size, "status": status})

    m = _read_manifest(d)
    links = [{"id": "link:" + url, "name": url} for url in m.get("links", [])]
    return {"files": files, "links": links,
            "prompt": m.get("prompt", ""), "topic": m.get("topic", "")}


# ---- endpoints -------------------------------------------------------------
class CreateReq(BaseModel):
    name: str
    topic: str = ""


@router.post("/create")
def learn_create(req: CreateReq) -> dict:
    """Scaffold a draft vault under VAULT_ROOT (init + an empty learn manifest)."""
    from compendium.vault import VaultPaths, render_claude_md, write_index
    from compendium.vault.log import append_entry

    name = req.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="name too short")
    d = (VAULT_ROOT / _slug(name)).resolve()
    if VAULT_ROOT.resolve() not in d.parents:
        raise HTTPException(status_code=400, detail="bad name")
    paths = VaultPaths.for_vault(str(d))
    if paths.is_initialized():
        raise HTTPException(status_code=409, detail="a knowledge base with that name already exists")

    paths.root.mkdir(parents=True, exist_ok=True)
    for sub in paths.all_dirs():
        sub.mkdir(parents=True, exist_ok=True)
    _upload_dir(d).mkdir(parents=True, exist_ok=True)
    topic = req.topic.strip() or name
    paths.claude_md.write_text(render_claude_md(topic), encoding="utf-8")
    paths.log_md.write_text("# Log\n\n", encoding="utf-8")
    write_index(paths, topic=topic)
    try:
        append_entry(paths, op="init", subject=topic, body=f"- created via Learn (web)\n- topic: {topic}")
    except Exception:
        pass
    _write_manifest(d, {"status": "draft", "topic": topic, "prompt": "", "links": []})
    return {"vault": str(d), "name": d.name, "sources": _sources(d)}


class OpenReq(BaseModel):
    vault: str


@router.post("/open")
def learn_open(req: OpenReq) -> dict:
    """Prepare an EXISTING knowledge base for expansion: ensure the learn manifest +
    uploads dir, then return its intake sources. Backs the 'Expand' action — new material
    is added on top of an already-built vault (incremental re-ingest is Phase 3)."""
    d = _safe_vault(req.vault)
    if not (d / "CLAUDE.md").is_file():
        raise HTTPException(status_code=404, detail="not a knowledge base")
    _upload_dir(d).mkdir(parents=True, exist_ok=True)
    if not _manifest_path(d).exists():
        _write_manifest(d, {"status": "expand", "topic": _topic_of(d), "prompt": "", "links": []})
    return {"vault": str(d), "name": d.name, "sources": _sources(d)}


@router.post("/upload")
async def learn_upload(vault: str = Form(...), files: list[UploadFile] = File(...)) -> dict:
    """Stash uploaded files (pdf/md/txt) into the draft/vault's raw/uploads/."""
    d = _safe_vault(vault)
    if not _manifest_path(d).exists():       # auto-prepare (e.g. expanding a built vault)
        _upload_dir(d).mkdir(parents=True, exist_ok=True)
        _write_manifest(d, {"status": "expand", "topic": _topic_of(d), "prompt": "", "links": []})
    up = _upload_dir(d)
    up.mkdir(parents=True, exist_ok=True)
    saved, skipped = 0, 0
    for uf in files:
        ext = Path(uf.filename or "").suffix.lower()
        if ext not in _ALLOWED_EXT:
            skipped += 1
            continue
        data = await uf.read()
        if not data or len(data) > _MAX_BYTES:
            skipped += 1
            continue
        (up / _safe_name(uf.filename)).write_bytes(data)
        saved += 1
    return {"saved": saved, "skipped": skipped, "sources": _sources(d)}


class MetaReq(BaseModel):
    vault: str
    prompt: str = ""
    links: list[str] = []


@router.post("/meta")
def learn_meta(req: MetaReq) -> dict:
    """Update the research prompt + web/video links on the draft manifest."""
    d = _safe_vault(req.vault)
    m = _read_manifest(d)
    m["prompt"] = req.prompt.strip()
    m["links"] = [l.strip() for l in req.links if l.strip()]
    _write_manifest(d, m)
    return {"sources": _sources(d)}


@router.get("/sources")
def learn_sources(vault: str) -> dict:
    return _sources(_safe_vault(vault))


@router.delete("/source")
def learn_remove(vault: str, id: str) -> dict:
    """Curate: drop an uploaded file or a link from the draft."""
    d = _safe_vault(vault)
    if id.startswith("file:"):
        target = (_upload_dir(d) / Path(id[len("file:"):]).name).resolve()
        if _upload_dir(d).resolve() in target.parents and target.is_file():
            try:
                target.unlink()
            except Exception:
                pass
    elif id.startswith("link:"):
        url = id[len("link:"):]
        m = _read_manifest(d)
        m["links"] = [l for l in m.get("links", []) if l != url]
        _write_manifest(d, m)
    return {"sources": _sources(d)}


@router.get("/peek")
def learn_peek(vault: str, kind: str, name: str) -> dict:
    """A quick look inside one raw source — so the reader can refresh themselves on
    what a (pending or learned) document actually is before building. Returns the
    text head of the best-readable variant: .md/.txt directly, a PDF via its cached
    *.extracted.txt (else PyMuPDF, first pages). Links have no body — the UI shows
    the URL itself."""
    d = _safe_vault(vault)
    kind = Path(kind).name                       # no traversal
    stem = Path(name).name
    if kind == "proposal":                       # ghost-door proposals live in _meta
        sub = (d / "wiki" / "_meta" / "proposals").resolve()
    else:
        sub = (d / "raw" / kind).resolve()
    if not sub.is_dir() or d.resolve() not in sub.parents:
        raise HTTPException(status_code=404, detail="unknown source kind")
    cand = [f for f in sub.iterdir() if f.is_file() and f.stem == stem]
    if not cand:
        raise HTTPException(status_code=404, detail="source not found")
    CAP = 8000
    text, via = "", ""
    by_ext = {f.suffix.lower(): f for f in cand}
    for ext in (".md", ".markdown", ".txt"):
        if ext in by_ext:
            try:
                text, via = by_ext[ext].read_text(encoding="utf-8", errors="replace"), ext
                break
            except Exception:
                pass
    if not text:
        extracted = sub / f"{stem}.pdf.extracted.txt"
        if not extracted.is_file():
            extracted = sub / f"{stem}.extracted.txt"
        if extracted.is_file():
            try:
                text, via = extracted.read_text(encoding="utf-8", errors="replace"), "extracted"
            except Exception:
                pass
    if not text and ".pdf" in by_ext:
        try:
            import fitz
            with fitz.open(str(by_ext[".pdf"])) as doc:
                parts = []
                for page in doc:
                    parts.append(page.get_text())
                    if sum(len(p) for p in parts) > CAP:
                        break
                text, via = "\n".join(parts), "pdf"
        except Exception:
            text = ""
    if not text:
        return {"name": stem, "kind": kind, "text": "",
                "note": "no readable text for this format", "truncated": False}
    truncated = len(text) > CAP
    return {"name": stem, "kind": kind, "text": text[:CAP],
            "note": "", "truncated": truncated}
