"""Models & Keys — user-managed LLM endpoints (multi-provider), the user-facing
replacement for backend-only .env keys. Any OpenAI-compatible or Anthropic endpoint is
just name + base_url + api_key, stored locally in ~/Dwell/.dwell-endpoints.json. The API
key NEVER leaves the server — responses carry only `has_key`. These endpoints power both
the reader engine (Stage B) and ingest model selection (Stage C).

Mounted by dwell_server. Uses compendium.llm.providers for URL/header shaping.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from compendium.llm.providers import detect_provider, models_url, build_headers, normalize_base

router = APIRouter(prefix="/endpoints", tags=["endpoints"])

VAULT_ROOT = Path(os.environ.get("DWELL_VAULT_ROOT") or str(Path.home() / "Dwell"))
_STORE = VAULT_ROOT / ".dwell-endpoints.json"

# Anthropic's /v1/models needs a valid key; seed a known list as a fallback so an
# Anthropic endpoint is usable even before a successful probe.
_ANTHROPIC_FALLBACK = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"]


def _read() -> list[dict]:
    try:
        return json.loads(_STORE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(eps: list[dict]) -> None:
    VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(eps, indent=2), encoding="utf-8")
    os.replace(tmp, _STORE)


def _public(ep: dict) -> dict:
    """Endpoint as the frontend sees it — NO api_key, just has_key."""
    return {
        "id": ep["id"], "name": ep["name"], "base_url": ep["base_url"],
        "provider": detect_provider(ep["base_url"]),
        "has_key": bool(ep.get("api_key")), "enabled": ep.get("enabled", True),
        "models": ep.get("models") or [],
    }


def _probe(base_url: str, api_key: str) -> list[str]:
    """List model ids from an endpoint's /models (OpenAI-compatible or Anthropic)."""
    headers = build_headers(api_key or None, base_url)
    headers.pop("Content-Type", None)
    try:
        r = httpx.get(models_url(base_url), headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        if not ids:                                  # Ollama native shape
            ids = [m.get("name") or m.get("model") for m in (data.get("models") or [])
                   if (m.get("name") or m.get("model"))]
        if ids:
            return ids
    except Exception:
        pass
    if detect_provider(base_url) == "anthropic":
        return list(_ANTHROPIC_FALLBACK)
    return []


# ---- helper for other modules (reader / ingest) ----------------------------
def resolve_endpoint(ep_id: str) -> dict | None:
    """The full stored endpoint (incl. api_key) for an id, if enabled. For internal
    server use only — never return this to a client."""
    for e in _read():
        if e["id"] == ep_id and e.get("enabled", True):
            return e
    return None


def first_enabled_endpoint() -> dict | None:
    """The first enabled endpoint with a base_url (incl. api_key). Used as a fallback
    so a Learn build works out of the box when the user configured a provider but
    didn't explicitly pick one. Internal server use only."""
    for e in _read():
        if e.get("enabled", True) and e.get("base_url"):
            return e
    return None


# ---- endpoints -------------------------------------------------------------
class EndpointIn(BaseModel):
    name: str = ""
    base_url: str
    api_key: str | None = None
    enabled: bool = True


@router.get("")
def list_endpoints() -> dict:
    return {"endpoints": [_public(e) for e in _read()]}


@router.post("")
def add_endpoint(req: EndpointIn) -> dict:
    base = normalize_base(req.base_url)
    if not base.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="base_url must start with http:// or https://")
    models = _probe(base, req.api_key or "")
    ep = {
        "id": "ep_" + uuid.uuid4().hex[:10],
        "name": (req.name or "").strip() or base,
        "base_url": base, "api_key": (req.api_key or "").strip(),
        "enabled": req.enabled, "models": models, "created_at": time.time(),
    }
    eps = _read()
    eps.append(ep)
    _write(eps)
    return _public(ep)


@router.patch("/{ep_id}")
def update_endpoint(ep_id: str, req: EndpointIn) -> dict:
    eps = _read()
    for e in eps:
        if e["id"] == ep_id:
            e["name"] = (req.name or "").strip() or e["name"]
            e["base_url"] = normalize_base(req.base_url) or e["base_url"]
            if req.api_key:                          # only replace the key when a new one is given
                e["api_key"] = req.api_key.strip()
            e["enabled"] = req.enabled
            e["models"] = _probe(e["base_url"], e.get("api_key", ""))
            _write(eps)
            return _public(e)
    raise HTTPException(status_code=404, detail="endpoint not found")


@router.delete("/{ep_id}")
def delete_endpoint(ep_id: str) -> dict:
    eps = _read()
    kept = [e for e in eps if e["id"] != ep_id]
    if len(kept) == len(eps):
        raise HTTPException(status_code=404, detail="endpoint not found")
    _write(kept)
    return {"ok": True}


@router.post("/{ep_id}/probe")
def reprobe_endpoint(ep_id: str) -> dict:
    eps = _read()
    for e in eps:
        if e["id"] == ep_id:
            e["models"] = _probe(e["base_url"], e.get("api_key", ""))
            _write(eps)
            return _public(e)
    raise HTTPException(status_code=404, detail="endpoint not found")


@router.post("/test")
def test_endpoint(req: EndpointIn) -> dict:
    """Probe a base_url + key WITHOUT saving — validate the add form before committing."""
    base = normalize_base(req.base_url)
    if not base.startswith(("http://", "https://")):
        return {"ok": False, "models": [], "provider": "", "error": "base_url must be http(s)"}
    models = _probe(base, req.api_key or "")
    return {"ok": bool(models), "models": models, "provider": detect_provider(base)}


# ---- reader (Mercury) key — its own spot, separate from the ingest endpoints --------
# Mercury (Inception text-diffusion) is the ONLY reading engine; this is just its key.
_MERCURY_STORE = VAULT_ROOT / ".dwell-mercury.json"
reader_router = APIRouter(prefix="/reader", tags=["reader"])


def read_mercury_key() -> str:
    """The user-set Mercury key (empty if none) — the reader falls back to .env."""
    try:
        return (json.loads(_MERCURY_STORE.read_text(encoding="utf-8")).get("api_key") or "").strip()
    except Exception:
        return ""


class MercuryKeyIn(BaseModel):
    api_key: str


@reader_router.get("/mercury")
def get_mercury() -> dict:
    return {"has_key": bool(read_mercury_key())}


@reader_router.put("/mercury")
def set_mercury(req: MercuryKeyIn) -> dict:
    VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _MERCURY_STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"api_key": (req.api_key or "").strip()}), encoding="utf-8")
    os.replace(tmp, _MERCURY_STORE)
    return {"has_key": bool(read_mercury_key())}


@reader_router.delete("/mercury")
def clear_mercury() -> dict:
    try:
        _MERCURY_STORE.unlink()
    except FileNotFoundError:
        pass
    return {"has_key": False}


# ---- web search provider — powers research-prompt builds (cli.py loop) ----------------
# Tavily or Brave + key. Stored locally; the key never leaves the server. Falls back to
# COMPENDIUM_SEARCH_PROVIDER / _API_KEY in .env when unset.
_SEARCH_STORE = VAULT_ROOT / ".dwell-search.json"
search_router = APIRouter(prefix="/search", tags=["search"])
# tavily/brave are true search providers; jina is a key (Jina Search + Reader, r.jina.ai —
# good for JS/GitHub pages) that the loop falls back to when no provider is set.
_SEARCH_PROVIDERS = ("tavily", "brave", "jina")


def read_search_config() -> dict:
    """The user-set {provider, api_key} (empty strings if none) — for internal use only."""
    try:
        d = json.loads(_SEARCH_STORE.read_text(encoding="utf-8"))
        return {"provider": (d.get("provider") or "").strip(), "api_key": (d.get("api_key") or "").strip()}
    except Exception:
        return {"provider": "", "api_key": ""}


def search_available() -> bool:
    """Whether research can run — a stored provider+key, OR whatever the pipeline itself
    resolves from .env (compendium.config reads the .env the loop subprocess will use)."""
    c = read_search_config()
    if c["provider"] and c["api_key"]:
        return True
    try:
        from compendium.config import CompendiumConfig
        # The loop subprocess runs from the repo root, so resolve .env there (not the
        # server's cwd) to match exactly what the pipeline will actually see.
        env_file = Path(__file__).resolve().parent.parent / ".env"
        cfg = CompendiumConfig(_env_file=str(env_file)) if env_file.exists() else CompendiumConfig()
        return (cfg.search_provider or "none") not in ("", "none") or bool(cfg.jina_api_key)
    except Exception:
        return False


class SearchIn(BaseModel):
    provider: str
    api_key: str


@search_router.get("")
def get_search() -> dict:
    c = read_search_config()
    return {"provider": c["provider"], "has_key": bool(c["api_key"]),
            "providers": list(_SEARCH_PROVIDERS), "available": search_available()}


@search_router.put("")
def set_search(req: SearchIn) -> dict:
    prov = (req.provider or "").strip().lower()
    if prov not in _SEARCH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider must be one of {_SEARCH_PROVIDERS}")
    VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _SEARCH_STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"provider": prov, "api_key": (req.api_key or "").strip()}), encoding="utf-8")
    os.replace(tmp, _SEARCH_STORE)
    return get_search()


@search_router.delete("")
def clear_search() -> dict:
    try:
        _SEARCH_STORE.unlink()
    except FileNotFoundError:
        pass
    return get_search()
