"""Lint metrics history — an append-only time-series of vault health.

Each `lint` run appends one JSON object (one line) to
`wiki/_meta/history.jsonl`. The `health` command reads the file back and
renders a trend so you can tell whether the vault is *converging* (gaps
closing, contradictions resolving) or *sprawling* (counts climbing).

The schema is intentionally open — callers pass a plain dict and unknown
keys round-trip untouched, so the metric set can grow without a
migration. Conventional keys written by the Linter:

    timestamp                ISO-8601 datetime string
    pages                    int   — pages inspected
    orphans                  int
    missing_targets          int   — distinct broken wikilink targets
    broken_refs              int   — total broken references (sum of ref_counts)
    contradictions_open      int   — needs-attention (excl. by-design)
    contradictions_new       int
    contradictions_regressed int
    contradictions_by_design int   — preserved scholarly tensions (silenced)
    citations_high           int
    citations_medium         int
    citations_unverified     int
    grounded                 int   — claim-grounding (omitted if not run)
    grounding_loose          int
    grounding_not_found      int
    cost                     float — USD for the run
"""

from __future__ import annotations

import json
from pathlib import Path

from compendium.vault.layout import VaultPaths


def append_history_entry(vault: VaultPaths, entry: dict) -> None:
    """Append one metrics snapshot to the vault's history log.

    Best-effort: never raises into the caller (a lint shouldn't fail
    because history couldn't be written). Creates `_meta/` if needed.
    """
    try:
        vault.meta.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with vault.history_jsonl.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except Exception:
        # History is advisory; swallow I/O / serialization errors.
        pass


def read_history(vault: VaultPaths) -> list[dict]:
    """Read all history snapshots, oldest first. Skips malformed lines."""
    path: Path = vault.history_jsonl
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out
