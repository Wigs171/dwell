"""Page backlog — queue of PageChange objects deferred by the plan-budget-gate.

When IngestOrchestrator's plan-budget-gate decides that Router planned
more pages than the remaining budget can afford, the lower-priority
PageChange objects are *deferred*: not written, not lost, and (crucially)
not re-planned. Each one lands here with enough context to reconstruct
the Writer call without paying Router cost twice.

The data lives in two places:
- `<vault>/.page-backlog.json` — authoritative JSON (dotfile, machine-read)
- `<vault>/wiki/_meta/page-backlog.md` — rendered view (Obsidian-read)

A future `cli.py flush-backlog` command iterates entries, reads the
raw/ source back off disk, reconstructs the `PageChange`, runs
PageWriter, and removes completed entries on success.

Design notes:
- Idempotent on `(source_id, page_id)` — re-running the same ingest
  that already deferred a page does not duplicate the entry.
- Raw path is stored, NOT raw content — the raw/ file is the source
  of truth and is already persisted by the ingest pipeline.
- PageChange is serialized via `model_dump(mode="json")` so enums come
  back as strings and reconstruction via `PageChange(**d)` works.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from compendium.vault.layout import VaultPaths


_BACKLOG_FILENAME = ".page-backlog.json"
_SCHEMA_VERSION = 1


@dataclass
class BacklogEntry:
    """One deferred PageChange with enough context to drain later."""

    source_id: str
    source_title: str
    raw_path: str          # absolute path to the raw/ source file
    change: dict           # serialized PageChange (model_dump mode="json")
    reason: str            # the plan_truncated message
    deferred_at: str       # ISO timestamp (seconds precision)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_title": self.source_title,
            "raw_path": self.raw_path,
            "change": dict(self.change),
            "reason": self.reason,
            "deferred_at": self.deferred_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BacklogEntry":
        return cls(
            source_id=d.get("source_id", ""),
            source_title=d.get("source_title", ""),
            raw_path=d.get("raw_path", ""),
            change=dict(d.get("change") or {}),
            reason=d.get("reason", ""),
            deferred_at=d.get("deferred_at", ""),
        )


class PageBacklog:
    """JSON-backed queue of deferred PageChange objects."""

    def __init__(self, vault: VaultPaths):
        self.vault = vault
        self._path = vault.root / _BACKLOG_FILENAME

    # ---- load / save -----------------------------------------------------

    def _load(self) -> dict:
        if not self._path.exists():
            return {"version": _SCHEMA_VERSION, "entries": []}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt file — don't block ingest, start fresh.
            return {"version": _SCHEMA_VERSION, "entries": []}

    def _save(self, data: dict) -> None:
        self.vault.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".page-backlog.",
            suffix=".json.tmp",
            dir=self.vault.root,
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

    # ---- mutations --------------------------------------------------------

    def extend(self, entries: list[BacklogEntry]) -> int:
        """Bulk append. Idempotent on `(source_id, page_id)`.

        Returns the number of entries actually added.
        """
        if not entries:
            return 0
        data = self._load()
        existing = list(data.get("entries", []))
        existing_keys: set[tuple[str, str]] = {
            (e.get("source_id", ""), (e.get("change") or {}).get("page_id", ""))
            for e in existing
        }
        added = 0
        for entry in entries:
            key = (entry.source_id, entry.change.get("page_id", ""))
            if not key[1]:
                continue
            if key in existing_keys:
                continue
            existing.append(entry.to_dict())
            existing_keys.add(key)
            added += 1
        if added:
            data["entries"] = existing
            data["version"] = _SCHEMA_VERSION
            self._save(data)
        return added

    def remove(self, source_id: str, page_id: str) -> bool:
        """Remove a single entry. Returns True if a match was removed."""
        data = self._load()
        entries = list(data.get("entries", []))
        before = len(entries)
        entries = [
            e for e in entries
            if not (
                e.get("source_id") == source_id
                and (e.get("change") or {}).get("page_id") == page_id
            )
        ]
        if len(entries) == before:
            return False
        data["entries"] = entries
        self._save(data)
        return True

    # ---- reads ------------------------------------------------------------

    def list_entries(self) -> list[BacklogEntry]:
        data = self._load()
        return [BacklogEntry.from_dict(e) for e in data.get("entries", [])]

    def grouped_by_source(self) -> dict[str, list[BacklogEntry]]:
        """Return entries keyed by source_id; preserves insertion order."""
        out: dict[str, list[BacklogEntry]] = {}
        for e in self.list_entries():
            out.setdefault(e.source_id, []).append(e)
        return out

    def count(self) -> int:
        return len(self._load().get("entries", []))


# ---------------------------------------------------------------------------
# Rendering to markdown for wiki/_meta/page-backlog.md
# ---------------------------------------------------------------------------


def render_backlog_md(
    backlog: PageBacklog,
    *,
    topic: str | None = None,
) -> str:
    """Render the backlog as the Obsidian-readable `_meta/page-backlog.md`.

    Wikilinks to deferred page_ids are kept as raw `[[slug]]` so clicking
    them in Obsidian surfaces the standard "create new page" UX — which
    is correct since the Writer will create them on the next flush.
    """
    entries = backlog.list_entries()
    grouped = backlog.grouped_by_source()

    lines: list[str] = ["# Page Backlog", ""]
    header_parts = [
        f"updated {datetime.now().strftime('%Y-%m-%d')}",
        f"{len(entries)} page{'s' if len(entries) != 1 else ''} across "
        f"{len(grouped)} source{'s' if len(grouped) != 1 else ''}",
    ]
    if topic:
        header_parts.insert(0, topic)
    lines.append("*" + " · ".join(header_parts) + "*")
    lines.append("")
    lines.append(
        "> Pages Router planned but the Writer didn't reach due to the "
        "plan-budget-gate. Router cost is already paid — draining this "
        "backlog costs only the Writer share (~$0.30-0.80 per page)."
    )
    lines.append("")
    lines.append(
        "> Drain with: "
        "`python cli.py flush-backlog --vault <vault> --max-cost N`"
    )
    lines.append("")

    if not entries:
        lines.append("*Backlog is empty — nothing deferred.*")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    for source_id in sorted(grouped.keys()):
        items = grouped[source_id]
        first_deferred = items[0].deferred_at[:10] if items[0].deferred_at else "?"
        source_title = items[0].source_title or source_id
        lines.append(
            f"## From [[{source_id}|{source_title}]] "
            f"(deferred {first_deferred}, {len(items)} pending)"
        )
        lines.append("")
        for e in items:
            ch = e.change or {}
            page_id = ch.get("page_id", "?")
            page_type = ch.get("page_type", "?")
            op = ch.get("op", "?")
            title = ch.get("title", page_id)
            lines.append(f"- [[{page_id}|{title}]] — {page_type} ({op})")
            rsn = ch.get("reason") or ""
            if rsn:
                lines.append(f"  - router reason: {rsn}")
            if e.reason:
                lines.append(f"  - defer reason: {e.reason}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_backlog_md(
    backlog: PageBacklog,
    *,
    topic: str | None = None,
) -> Path:
    """Regenerate `wiki/_meta/page-backlog.md` from current state."""
    backlog.vault.meta.mkdir(parents=True, exist_ok=True)
    path = backlog.vault.meta / "page-backlog.md"
    path.write_text(render_backlog_md(backlog, topic=topic), encoding="utf-8")
    return path
