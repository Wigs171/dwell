"""Contradiction ledger — durable state for cross-page contradictions.

The Linter's contradiction scan is stateless: it re-derives the full set
of disagreeing-claim clusters from scratch every run. That throws away
two things a growing scholarship vault needs:

1. **Resolution memory.** If you edit two pages to settle a contradiction,
   the scan stops flagging it — fine. But if a later ingest reintroduces
   the conflict, nothing tells you it *regressed*. Marking a contradiction
   `resolved` arms regression detection: a reappearance is surfaced loudly.

2. **The scholarly-tension distinction.** Some contradictions are not
   errors. Burkert reading Pythagoras as a religious figure vs. a later
   tradition reading him as a mathematician is a *genuine disagreement in
   the sources* that the vault should preserve, not "fix." Marking such a
   cluster `by-design` silences it permanently from the needs-attention
   count while keeping it on the record.

The ledger is a JSON file at `wiki/_meta/contradiction-ledger.json`. Each
entry is keyed by the sorted set of page IDs in tension, so wording drift
in the LLM-generated summary between runs doesn't fork the identity.

States:
    open       — needs attention (default for newly detected)
    resolved   — settled; reappearance = regression (auto-reopened + flagged)
    by-design  — preserved scholarly tension; silenced, never re-escalated
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field

from compendium.models import Contradiction
from compendium.vault.layout import VaultPaths
from compendium.vault.pages import today_iso

_SCHEMA_VERSION = 1

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUS_BY_DESIGN = "by-design"
_VALID_STATUSES = {STATUS_OPEN, STATUS_RESOLVED, STATUS_BY_DESIGN}


def make_key(pages: list[str]) -> str:
    """Stable identity for a contradiction: its sorted page-id set."""
    norm = sorted(p.strip() for p in pages if p and p.strip())
    return "||".join(norm)


def short_id(key: str) -> str:
    """Human-quotable handle for a ledger key (stable across runs)."""
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return "c-" + digest[:8]


@dataclass
class LedgerEntry:
    key: str
    pages: list[str]
    status: str = STATUS_OPEN
    summary: str = ""
    details: str = ""
    resolution_note: str = ""
    first_seen: str = ""
    last_seen: str = ""
    times_seen: int = 0
    regressed_on: str = ""

    @property
    def sid(self) -> str:
        return short_id(self.key)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "pages": self.pages,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
            "resolution_note": self.resolution_note,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "times_seen": self.times_seen,
            "regressed_on": self.regressed_on,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LedgerEntry":
        pages = [str(p) for p in (d.get("pages") or [])]
        key = d.get("key") or make_key(pages)
        return cls(
            key=key,
            pages=pages,
            status=d.get("status") or STATUS_OPEN,
            summary=d.get("summary") or "",
            details=d.get("details") or "",
            resolution_note=d.get("resolution_note") or "",
            first_seen=d.get("first_seen") or "",
            last_seen=d.get("last_seen") or "",
            times_seen=int(d.get("times_seen") or 0),
            regressed_on=d.get("regressed_on") or "",
        )


@dataclass
class ReconcileResult:
    """Outcome of reconciling a fresh scan against the persisted ledger."""

    new: list[Contradiction] = field(default_factory=list)
    regressions: list[Contradiction] = field(default_factory=list)
    known_open: list[Contradiction] = field(default_factory=list)
    by_design: list[Contradiction] = field(default_factory=list)
    errors: list[Contradiction] = field(default_factory=list)
    entries_by_key: dict[str, LedgerEntry] = field(default_factory=dict)

    @property
    def needs_attention(self) -> list[Contradiction]:
        """Everything that should count toward the headline number.

        New + regressed + already-open. Excludes by-design (preserved
        tensions) and scan errors.
        """
        return [*self.new, *self.regressions, *self.known_open]

    def status_of(self, c: Contradiction) -> str:
        entry = self.entries_by_key.get(make_key(c.pages))
        return entry.status if entry else STATUS_OPEN

    def sid_of(self, c: Contradiction) -> str:
        return short_id(make_key(c.pages))


class ContradictionLedger:
    """JSON-backed contradiction state for a single vault."""

    def __init__(self, vault: VaultPaths):
        self.vault = vault
        self._path = vault.contradiction_ledger_json

    # ---- persistence -----------------------------------------------------

    def load(self) -> dict[str, LedgerEntry]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        out: dict[str, LedgerEntry] = {}
        for raw in data.get("entries", []) or []:
            entry = LedgerEntry.from_dict(raw)
            if entry.key:
                out[entry.key] = entry
        return out

    def save(self, entries: dict[str, LedgerEntry]) -> None:
        self.vault.meta.mkdir(parents=True, exist_ok=True)
        # Stable ordering: open first, then by-design, then resolved;
        # within a bucket, most-seen first, then key.
        order = {STATUS_OPEN: 0, STATUS_BY_DESIGN: 1, STATUS_RESOLVED: 2}
        ordered = sorted(
            entries.values(),
            key=lambda e: (order.get(e.status, 3), -e.times_seen, e.key),
        )
        data = {
            "version": _SCHEMA_VERSION,
            "entries": [e.to_dict() for e in ordered],
        }
        fd, tmp = tempfile.mkstemp(
            prefix=".contradiction-ledger.", suffix=".json.tmp",
            dir=str(self.vault.meta),
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

    # ---- reconcile -------------------------------------------------------

    def reconcile(
        self, scanned: list[Contradiction], *, today: str | None = None
    ) -> ReconcileResult:
        """Fold a fresh scan into the ledger and classify each cluster.

        - Unseen-before cluster  → recorded `open`, bucketed `new`.
        - Cluster already `open`  → bucketed `known_open`.
        - Cluster `resolved`      → REGRESSION: reopened, bucketed `regressions`.
        - Cluster `by-design`     → silenced, bucketed `by_design`.
        - Scan-error entry (<2 pages) → passed through as `errors`, not stored.

        Persists the updated ledger (including entries not seen this run,
        whose state is preserved untouched). Returns the classification.
        """
        today = today or today_iso()
        ledger = self.load()
        result = ReconcileResult()

        # Dedupe within this single scan by page-set. The contradiction
        # scanner runs several batches (tag clusters + a central cross-scan),
        # so the same page-set can be reported more than once per lint —
        # sometimes as genuinely distinct contradictions. Collapse to one
        # logical entry per page-set, MERGING distinct summaries/details so
        # nothing is lost, so `times_seen` counts lints (not intra-scan
        # repeats) and the headline counts unique contradictions.
        unique: dict[str, Contradiction] = {}
        for c in scanned:
            if len(c.pages) < 2:
                result.errors.append(c)
                continue
            key = make_key(c.pages)
            agg = unique.get(key)
            if agg is None:
                unique[key] = c.model_copy()
                continue
            if c.summary and c.summary not in agg.summary:
                agg.summary = f"{agg.summary} | {c.summary}".strip(" |")
            if c.details and c.details not in agg.details:
                agg.details = f"{agg.details}\n\n{c.details}".strip()
            if (
                c.suggested_resolution
                and c.suggested_resolution not in agg.suggested_resolution
            ):
                agg.suggested_resolution = (
                    f"{agg.suggested_resolution}\n\n{c.suggested_resolution}".strip()
                )

        for key, c in unique.items():
            entry = ledger.get(key)
            if entry is None:
                entry = LedgerEntry(
                    key=key,
                    pages=sorted(c.pages),
                    status=STATUS_OPEN,
                    summary=c.summary,
                    details=c.details,
                    first_seen=today,
                    last_seen=today,
                    times_seen=1,
                )
                ledger[key] = entry
                result.new.append(c)
            else:
                entry.last_seen = today
                entry.times_seen += 1
                if c.summary:
                    entry.summary = c.summary
                if c.details:
                    entry.details = c.details
                if entry.status == STATUS_RESOLVED:
                    entry.status = STATUS_OPEN
                    entry.regressed_on = today
                    result.regressions.append(c)
                elif entry.status == STATUS_BY_DESIGN:
                    result.by_design.append(c)
                else:
                    result.known_open.append(c)
            result.entries_by_key[key] = entry

        self.save(ledger)
        return result

    # ---- manual marking --------------------------------------------------

    def mark(
        self,
        identifier: str,
        status: str,
        *,
        note: str = "",
        today: str | None = None,
    ) -> LedgerEntry | None:
        """Set the status of a ledger entry by short-id or page list.

        `identifier` is either a short id (`c-1a2b3c4d`) or a comma- or
        pipe-separated list of the page IDs in tension. Returns the updated
        entry, or None if no matching entry exists or the status is invalid.
        """
        if status not in _VALID_STATUSES:
            return None
        today = today or today_iso()
        ledger = self.load()
        entry = self._resolve(ledger, identifier)
        if entry is None:
            return None
        entry.status = status
        entry.resolution_note = note
        if status != STATUS_OPEN:
            entry.regressed_on = ""  # clear stale regression marker
        self.save(ledger)
        return entry

    def _resolve(
        self, ledger: dict[str, LedgerEntry], identifier: str
    ) -> LedgerEntry | None:
        ident = (identifier or "").strip()
        if not ident:
            return None
        if ident.lower().startswith("c-"):
            target = ident.lower()
            for entry in ledger.values():
                if entry.sid == target:
                    return entry
            return None
        # Treat as a page list
        pages = [p.strip() for p in ident.replace("|", ",").split(",")]
        key = make_key(pages)
        return ledger.get(key)

    def entries(self) -> list[LedgerEntry]:
        order = {STATUS_OPEN: 0, STATUS_BY_DESIGN: 1, STATUS_RESOLVED: 2}
        return sorted(
            self.load().values(),
            key=lambda e: (order.get(e.status, 3), -e.times_seen, e.key),
        )
