"""Ingest registry — content-addressable dedup for vault sources.

A small JSON file at `<vault>/raw/.ingest-registry.json` records every
source that has been brought into the vault, keyed by:

- `hash` — SHA-256 of the file bytes (for local files, downloaded PDFs)
- `url`  — the source URL (for web-fetched articles, research downloads)

On every ingest entry point (local PDF, URL fetch, research PDF
download), we consult the registry BEFORE doing expensive work
(figure transcription, Router/Writer, etc.). A hit → the source is
already in the vault; skip with an informative return. A miss → do
the work and append a new entry.

The registry is designed to be forward-compatible: unknown JSON keys
are preserved across read/write, and schema `version` is tracked so
future migrations can be additive.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from compendium.vault.layout import VaultPaths


_REGISTRY_FILENAME = ".ingest-registry.json"
_SCHEMA_VERSION = 1


@dataclass
class RegistryEntry:
    """One recorded ingest."""

    source_id: str
    raw_path: str                 # relative path within the vault
    ingested: str                 # ISO timestamp
    hash: str = ""                # SHA-256 hex of file bytes; "" if unknown
    url: str = ""                 # origin URL; "" for local-only files
    origin: str = ""              # free-form origin string (e.g., local file path)
    extras: dict[str, Any] = field(default_factory=dict)


class IngestRegistry:
    """JSON-backed registry of ingested sources for a single vault."""

    def __init__(self, vault: VaultPaths):
        self.vault = vault
        self._path = vault.raw / _REGISTRY_FILENAME

    # ---- loading / saving ------------------------------------------------

    def _load(self) -> dict:
        if not self._path.exists():
            return {"version": _SCHEMA_VERSION, "entries": []}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt registry — don't block ingest, just warn-by-reset.
            return {"version": _SCHEMA_VERSION, "entries": []}

    def _save(self, data: dict) -> None:
        self.vault.raw.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".ingest-registry.", suffix=".json.tmp", dir=self.vault.raw
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- lookups ---------------------------------------------------------

    def find_by_hash(self, content_hash: str) -> RegistryEntry | None:
        if not content_hash:
            return None
        data = self._load()
        for raw in data.get("entries", []):
            if raw.get("hash") == content_hash:
                return _entry_from_dict(raw)
        return None

    def find_by_url(self, url: str) -> RegistryEntry | None:
        if not url:
            return None
        # Normalize trivial URL differences (trailing slash, fragment)
        canonical = _canonicalize_url(url)
        data = self._load()
        for raw in data.get("entries", []):
            if _canonicalize_url(raw.get("url") or "") == canonical:
                return _entry_from_dict(raw)
        return None

    # ---- mutations -------------------------------------------------------

    def record(self, entry: RegistryEntry) -> None:
        """Add an entry to the registry. Idempotent on exact hash/source_id."""
        data = self._load()
        entries = data.get("entries", [])
        # Drop any existing entry with the same source_id to avoid stale pointers.
        entries = [e for e in entries if e.get("source_id") != entry.source_id]
        entries.append(_entry_to_dict(entry))
        data["entries"] = entries
        data["version"] = _SCHEMA_VERSION
        self._save(data)

    # ---- tombstones ------------------------------------------------------
    # Culled sources go here. IngestRouter and ResearchAgent check
    # `is_tombstoned()` before doing expensive ingest work so a source
    # the Curator judged stale doesn't get re-downloaded and re-ingested
    # on a future run. Tombstones live in the registry alongside regular
    # entries so the whole ingest history is in one file.

    def tombstone(
        self,
        *,
        source_id: str,
        hash: str = "",
        url: str = "",
        reason: str = "",
    ) -> None:
        """Record a culled source. At least one of hash/url must be set."""
        if not (hash or url or source_id):
            return
        data = self._load()
        tombstones = data.get("tombstones", [])
        # Idempotent on source_id
        tombstones = [
            t for t in tombstones if t.get("source_id") != source_id
        ]
        tombstones.append({
            "source_id": source_id,
            "hash": hash or "",
            "url": _canonicalize_url(url) if url else "",
            "reason": reason or "",
            "culled": now_iso(),
        })
        data["tombstones"] = tombstones
        data["version"] = _SCHEMA_VERSION
        self._save(data)

    def is_tombstoned(
        self, *, hash: str = "", url: str = ""
    ) -> dict | None:
        """Return the tombstone dict if hash or URL has been culled, else None."""
        if not (hash or url):
            return None
        data = self._load()
        canonical = _canonicalize_url(url) if url else ""
        for t in data.get("tombstones", []):
            if hash and t.get("hash") == hash:
                return t
            if canonical and t.get("url") == canonical:
                return t
        return None

    def list_tombstones(self) -> list[dict]:
        return list(self._load().get("tombstones", []))

    def remove_entry(self, source_id: str) -> None:
        """Drop an entry for a culled source. Does NOT add a tombstone —
        call tombstone() explicitly for that."""
        data = self._load()
        entries = data.get("entries", [])
        before = len(entries)
        entries = [e for e in entries if e.get("source_id") != source_id]
        if len(entries) == before:
            return
        data["entries"] = entries
        self._save(data)


# ---- helpers -------------------------------------------------------------


def hash_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file's bytes, streamed so large PDFs don't blow memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _canonicalize_url(url: str) -> str:
    """Collapse trivial differences so `http://x` and `http://x/` match."""
    u = (url or "").strip()
    if "#" in u:
        u = u.split("#", 1)[0]
    return u.rstrip("/")


def _entry_to_dict(e: RegistryEntry) -> dict:
    d: dict[str, Any] = {
        "source_id": e.source_id,
        "raw_path": e.raw_path,
        "ingested": e.ingested,
        "hash": e.hash,
        "url": e.url,
        "origin": e.origin,
    }
    if e.extras:
        d["extras"] = dict(e.extras)
    return d


def _entry_from_dict(d: dict) -> RegistryEntry:
    return RegistryEntry(
        source_id=d.get("source_id", ""),
        raw_path=d.get("raw_path", ""),
        ingested=d.get("ingested", ""),
        hash=d.get("hash") or "",
        url=d.get("url") or "",
        origin=d.get("origin") or "",
        extras=dict(d.get("extras") or {}),
    )
