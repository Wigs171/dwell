"""Persistent loop state — so `cli.py loop` can survive across sessions.

Written at `<vault>/.loop-state.json` and updated after every completed
iteration. Tracks:

- `queue`: ExpansionProposals pending research (serialized as dicts)
- `seen`: normalized topic titles already researched (never re-queue)
- `sessions`: append-only ledger of loop runs, each with cost + iters
- `meta`: version, last_updated

This module provides atomic read/write/update and a `LoopSession`
context manager that guarantees state is saved even on SIGINT — so
a Ctrl+C mid-iteration doesn't lose the queue.
"""

from __future__ import annotations

import json
import os
import signal
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from compendium.models import ExpansionKind, ExpansionProposal
from compendium.vault.layout import VaultPaths


_STATE_FILENAME = ".loop-state.json"
_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------


@dataclass
class SessionRecord:
    """One completed (or interrupted) loop session."""

    started: str
    ended: str
    iterations: int
    cost_dollars: float
    pages_created: int
    seed_topic: str = ""
    terminated_by: str = "complete"  # complete | budget | signal | iters_cap | convergence

    def to_dict(self) -> dict:
        return {
            "started": self.started,
            "ended": self.ended,
            "iterations": self.iterations,
            "cost_dollars": round(self.cost_dollars, 4),
            "pages_created": self.pages_created,
            "seed_topic": self.seed_topic,
            "terminated_by": self.terminated_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionRecord":
        return cls(
            started=d.get("started", ""),
            ended=d.get("ended", ""),
            iterations=int(d.get("iterations") or 0),
            cost_dollars=float(d.get("cost_dollars") or 0.0),
            pages_created=int(d.get("pages_created") or 0),
            seed_topic=d.get("seed_topic", ""),
            terminated_by=d.get("terminated_by", "complete"),
        )


# Cap on how many unresearched proposals we persist between sessions.
# Above this we keep the highest-priority ones and drop the rest —
# Explorer will re-surface anything still relevant on its next run.
QUEUE_MAX = 60


@dataclass
class LoopState:
    queue: list[ExpansionProposal] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    sessions: list[SessionRecord] = field(default_factory=list)
    last_updated: str = ""

    def trim_queue(self, cap: int = QUEUE_MAX) -> int:
        """Trim the queue to at most `cap` proposals, keeping the highest
        priority (lowest number). Returns count of items dropped.

        Stable within equal priorities (preserves FIFO order for ties),
        so that seed-adjacent proposals surfaced earlier get worked
        through before newer lower-priority ones if all else is equal.
        """
        if len(self.queue) <= cap:
            return 0
        # Decorate with original index for stable sort
        indexed = list(enumerate(self.queue))
        indexed.sort(key=lambda t: (t[1].priority, t[0]))
        kept = [p for _, p in indexed[:cap]]
        dropped = len(self.queue) - cap
        self.queue = kept
        return dropped

    # ---- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": _SCHEMA_VERSION,
            "queue": [_proposal_to_dict(p) for p in self.queue],
            "seen": sorted(self.seen),
            "sessions": [s.to_dict() for s in self.sessions],
            "last_updated": self.last_updated or _now_iso(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LoopState":
        return cls(
            queue=[_proposal_from_dict(x) for x in (d.get("queue") or [])],
            seen=set(d.get("seen") or []),
            sessions=[SessionRecord.from_dict(s) for s in (d.get("sessions") or [])],
            last_updated=d.get("last_updated", ""),
        )

    # ---- aggregates -------------------------------------------------------

    def cumulative_cost(self) -> float:
        return sum(s.cost_dollars for s in self.sessions)

    def cumulative_iterations(self) -> int:
        return sum(s.iterations for s in self.sessions)

    def cumulative_pages(self) -> int:
        return sum(s.pages_created for s in self.sessions)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def state_path(vault: VaultPaths) -> Path:
    return vault.root / _STATE_FILENAME


def load(vault: VaultPaths) -> LoopState:
    """Load persisted state. Returns an empty LoopState if none exists."""
    path = state_path(vault)
    if not path.exists():
        return LoopState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return LoopState()
    return LoopState.from_dict(data)


def sync_proposals_to_queue(
    vault: VaultPaths,
    proposals: list[ExpansionProposal],
    *,
    research_kinds: set[ExpansionKind] | None = None,
) -> int:
    """Sync fresh Explorer proposals into the persisted loop queue.

    Runs when a standalone `cli.py explore` (or post-ingest auto-explore)
    produces proposals — so a later `cli.py loop --resume` has something
    to resume from. Deduplicates against the `seen` set by normalized
    title, respects the queue cap, and only adds research-worthy
    categories (gap / source_suggestion / open_question by default).

    Returns count of proposals actually added. Skipped silently if
    `.loop-state.json` doesn't exist yet — no sense creating an empty
    session ledger for a vault that's never been looped.
    """
    if not state_path(vault).exists():
        return 0
    if research_kinds is None:
        research_kinds = {
            ExpansionKind.GAP,
            ExpansionKind.SOURCE_SUGGESTION,
            ExpansionKind.OPEN_QUESTION,
        }
    state = load(vault)
    existing_titles = {
        _norm(p.title) for p in state.queue
    } | {_norm(t) for t in state.seen}
    added = 0
    for p in proposals:
        if p.kind not in research_kinds:
            continue
        if _norm(p.title) in existing_titles:
            continue
        state.queue.append(p)
        existing_titles.add(_norm(p.title))
        added += 1
    if added == 0:
        return 0
    state.trim_queue()
    save(vault, state)
    return added


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def save(vault: VaultPaths, state: LoopState) -> None:
    """Atomically write state to disk (tmp-file + os.replace)."""
    state.last_updated = _now_iso()
    path = state_path(vault)
    vault.root.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".loop-state.", suffix=".json.tmp", dir=vault.root
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# session manager — saves on normal exit, SIGINT, SIGTERM
# ---------------------------------------------------------------------------


class LoopSession:
    """Context manager that holds a mutable LoopState + auto-saves on exit.

    Usage:

        with LoopSession(vault, seed_topic="...") as sess:
            # Mutate sess.state.queue / sess.state.seen as the loop runs.
            # Call sess.save_snapshot() after each iteration for safety.
            ...

    On normal exit OR SIGINT / SIGTERM, a final save is performed and a
    SessionRecord is appended to the state's sessions ledger.
    """

    def __init__(
        self,
        vault: VaultPaths,
        *,
        seed_topic: str = "",
    ):
        self.vault = vault
        self.state: LoopState = load(vault)
        self._seed_topic = seed_topic
        self._started = _now_iso()
        self.iteration_count = 0
        self.session_cost = 0.0
        self.session_pages = 0
        self.terminated_by = "complete"
        self._installed_handlers: dict[int, Any] = {}
        self._lock = threading.Lock()

    # ---- lifecycle --------------------------------------------------------

    def __enter__(self) -> "LoopSession":
        # Install signal handlers so Ctrl+C still saves state.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._installed_handlers[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                # signal() fails in non-main threads — that's fine,
                # we'll just fall back to the context manager's own exit.
                pass
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._restore_handlers()
        if exc_type is KeyboardInterrupt:
            self.terminated_by = "signal"
        self._finalize_session()
        # Never suppress exceptions — we saved; let them propagate.
        return False

    def _on_signal(self, signum, frame) -> None:
        self.terminated_by = "signal"
        self._finalize_session()
        # Re-raise so the caller sees the interrupt.
        self._restore_handlers()
        raise KeyboardInterrupt

    def _restore_handlers(self) -> None:
        for sig, prev in self._installed_handlers.items():
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError):
                pass
        self._installed_handlers.clear()

    def _finalize_session(self) -> None:
        with self._lock:
            record = SessionRecord(
                started=self._started,
                ended=_now_iso(),
                iterations=self.iteration_count,
                cost_dollars=self.session_cost,
                pages_created=self.session_pages,
                seed_topic=self._seed_topic,
                terminated_by=self.terminated_by,
            )
            # Only append if something actually happened — don't pollute
            # the ledger with zero-iteration no-ops.
            if self.iteration_count > 0 or self.session_pages > 0:
                self.state.sessions.append(record)
            try:
                save(self.vault, self.state)
            except Exception:
                pass  # best-effort; don't mask upstream errors

    # ---- checkpointing ----------------------------------------------------

    def save_snapshot(self) -> None:
        """Atomic save of the current state. Safe to call after each iter."""
        with self._lock:
            try:
                save(self.vault, self.state)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _proposal_to_dict(p: ExpansionProposal) -> dict:
    return {
        "kind": p.kind.value,
        "title": p.title,
        "priority": int(p.priority),
        "signal": p.signal,
        "rationale": p.rationale,
        "related": list(p.related),
    }


def _proposal_from_dict(d: dict) -> ExpansionProposal:
    try:
        kind = ExpansionKind(d.get("kind"))
    except ValueError:
        kind = ExpansionKind.GAP
    return ExpansionProposal(
        kind=kind,
        title=d.get("title", ""),
        priority=int(d.get("priority") or 3),
        signal=d.get("signal", ""),
        rationale=d.get("rationale", ""),
        related=[str(x) for x in (d.get("related") or [])],
    )
