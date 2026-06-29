"""IngestOrchestrator — end-to-end Ingest pipeline.

One source → IngestReport. Runs the three agents (Router, Writer,
Reviewer) against a vault, writes pages atomically, regenerates
index.md, appends to log.md. Reviewer issues are recorded in the
report but do not block the ingest.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from compendium.agents.explorer import Explorer
from compendium.agents.ingest_router import IngestRouter
from compendium.agents.page_writer import PageWriter
from compendium.agents.reviewer import Reviewer
from compendium.config import CompendiumConfig
from compendium.guardrails.cost_tracker import CostTracker
from compendium.models import (
    IngestPlan,
    IngestReport,
    Page,
    PageChangeOp,
    PageType,
    ReviewIssue,
    ReviewSeverity,
)
from compendium.vault import (
    VaultPaths,
    append_entry,
    page_exists,
    read_recent,
    slugify,
    timestamp_iso,
    write_index,
    write_page,
)


class VaultNotInitialized(Exception):
    pass


class IngestOrchestrator:
    """Runs the full Ingest pipeline for a single source."""

    def __init__(
        self,
        config: CompendiumConfig,
        vault: VaultPaths,
        *,
        structured: bool | None = None,
    ):
        if not vault.is_initialized():
            raise VaultNotInitialized(
                f"vault at {vault.root} is missing CLAUDE.md; "
                f"run `compendium init` first"
            )
        self.config = config
        self.vault = vault
        self.client = config.create_anthropic_client()
        self.cost_tracker = CostTracker(config.get_guardrails())
        tiered = config.tiered_models

        # `structured` selects the cheap single-call (non-REPL) Router /
        # PageWriter / Explorer instead of the recursive-language-model
        # REPL agents. The REPL path remains the default fallback. The
        # flag may also come from a config attribute (`structured_ingest`)
        # so the web "Learn" builds can default it ON via env without a CLI
        # flag.
        if structured is None:
            structured = bool(getattr(config, "structured_ingest", False))
        self.structured = structured

        if structured:
            from compendium.agents.structured_ingest import (
                StructuredExplorer,
                StructuredPageWriter,
                StructuredRouter,
            )

            self._router = StructuredRouter(
                self.client, config, self.cost_tracker, vault=vault, tiered=tiered
            )
            self._writer = StructuredPageWriter(
                self.client, config, self.cost_tracker, vault=vault, tiered=tiered
            )
            self._explorer = StructuredExplorer(
                self.client, config, self.cost_tracker, vault=vault, tiered=tiered
            )
        else:
            self._router = IngestRouter(
                client=self.client,
                config=config,
                cost_tracker=self.cost_tracker,
                vault=vault,
                tiered=tiered,
            )
            self._writer = PageWriter(
                client=self.client,
                config=config,
                cost_tracker=self.cost_tracker,
                vault=vault,
                tiered=tiered,
            )
            self._explorer = Explorer(
                client=self.client,
                config=config,
                cost_tracker=self.cost_tracker,
                vault=vault,
            )

        # Reviewer is already a single non-REPL messages.create call —
        # keep it identical in both modes.
        self._reviewer = Reviewer(
            client=self.client, config=config, cost_tracker=self.cost_tracker
        )

    def ingest(
        self,
        source_path: str | Path,
        *,
        run_explore: bool = True,
        progress: Callable[[str, dict], None] | None = None,
    ) -> IngestReport:
        """Ingest one source. Returns an IngestReport summarizing what happened.

        `progress(phase, payload)` — if given, is called at each pipeline phase
        (route / planned / write / wrote / review / explore / done) with the running
        cost + token counts attached, so a caller can stream live activity.
        """

        def _emit(phase: str, **extra) -> None:
            if progress is None:
                return
            try:
                s = self.cost_tracker.get_summary()
                progress(phase, {
                    **extra,
                    "cost": s["estimated_cost_usd"],
                    "tokens_in": s["total_input_tokens"],
                    "tokens_out": s["total_output_tokens"],
                })
            except Exception:
                pass

        src = Path(source_path).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"source not found: {src}")

        source_content = src.read_text(encoding="utf-8", errors="replace")
        source_title = src.stem.replace("_", " ").replace("-", " ").strip().title()
        source_id = _unique_source_id(self.vault, src.stem)

        # Copy to raw/ so the vault owns its own copy.
        raw_target = _copy_into_raw(self.vault, src, source_id)

        # Context for the Router
        topic = _read_topic(self.vault)
        index_text = self.vault.index_md.read_text(encoding="utf-8") if self.vault.index_md.exists() else ""
        recent = read_recent(self.vault, n=5)

        _emit("route", msg=f"Reading “{source_title}” and planning pages")
        plan = self._router.route(
            source_content=source_content,
            source_title=source_title,
            source_id=source_id,
            vault_topic=topic,
            vault_index=index_text,
            recent_log=recent,
        )
        _emit(
            "planned",
            pages=len(plan.changes),
            titles=[c.title for c in plan.changes][:12],
            summary=(plan.source_summary or "")[:240],
        )

        # Plan-level budget gate. Router has already done its strategic-
        # tier work planning N pages. If the projected Writer cost for
        # the full batch exceeds remaining budget, defer lower-priority
        # changes rather than starting the loop and rejecting pages
        # halfway (which was the pattern in every overrun session —
        # 9 write_failed rejections on day 1, 3 on day 2, etc.).
        #
        # Anchor calibration (2026-04-20): observed averages once prompt
        # caching + batch routing settled are:
        #   - Writer alone (flush-backlog, no Router): $0.35/page
        #   - Writer inside ingest (shares Router cost): $0.44/page
        # $0.45 leaves a small safety margin above those while fitting
        # ~9 pages into a $4 budget instead of the earlier ~4.
        # Override via `COMPENDIUM_PER_PAGE_ESTIMATE` env var if a
        # specific vault's writes run hotter than the average.
        import os as _os
        try:
            estimated_per_page = float(
                _os.environ.get("COMPENDIUM_PER_PAGE_ESTIMATE", "0.45")
            )
        except ValueError:
            estimated_per_page = 0.45
        summary = self.cost_tracker.get_summary()
        spent = summary["estimated_cost_usd"]
        guardrails = self.config.get_guardrails()
        remaining_budget = max(
            0.0, guardrails.max_cost_dollars - spent
        )
        max_pages_per_ingest = guardrails.max_pages_per_ingest
        planned = plan.changes[:max_pages_per_ingest]
        affordable = min(
            len(planned),
            int(remaining_budget / estimated_per_page) if estimated_per_page else len(planned),
        )
        deferred: list[tuple[str, str]] = []
        deferred_changes: list = []
        if affordable < len(planned):
            # Order matters: keep highest-value changes first. The Router
            # already orders plan.changes by importance; take the top N
            # that fit, defer the rest. CREATE ops take priority over
            # UPDATEs within each tier (CREATEs fill gaps; UPDATE can be
            # retried next ingest without losing existing content).
            by_priority = sorted(
                enumerate(planned),
                key=lambda ix: (
                    0 if ix[1].op == PageChangeOp.CREATE else 1,
                    ix[0],
                ),
            )
            keep_indices = {i for i, _ in by_priority[:affordable]}
            retained: list = []
            reason = (
                f"plan_truncated: ~${estimated_per_page:.2f}/page × "
                f"{len(planned)} > remaining ${remaining_budget:.2f}"
            )
            for i, ch in enumerate(planned):
                if i in keep_indices:
                    retained.append(ch)
                else:
                    deferred.append((ch.page_id, reason))
                    deferred_changes.append(ch)
            planned = retained

            # Persist deferred PageChanges to the backlog so `flush-backlog`
            # can drain them later without re-invoking Router. Best-effort:
            # a backlog write failure must not block the ingest itself.
            if deferred_changes:
                try:
                    from datetime import datetime
                    from compendium.vault.backlog import (
                        BacklogEntry, PageBacklog, write_backlog_md,
                    )

                    deferred_at = datetime.now().isoformat(timespec="seconds")
                    entries = [
                        BacklogEntry(
                            source_id=source_id,
                            source_title=source_title,
                            raw_path=str(raw_target),
                            change=ch.model_dump(mode="json"),
                            reason=reason,
                            deferred_at=deferred_at,
                        )
                        for ch in deferred_changes
                    ]
                    bl = PageBacklog(self.vault)
                    bl.extend(entries)
                    write_backlog_md(bl, topic=topic)
                except Exception as exc:
                    # Log but don't raise — backlog is advisory.
                    import logging
                    logging.getLogger(__name__).warning(
                        "backlog write failed: %s", exc,
                    )

        written: list[Page] = []
        write_failures: list[tuple[str, str]] = list(deferred)

        def _write_one(idx, change, emit_lock=None):
            """Draft + persist ONE page → (page, failure). Distinct pages are
            distinct files, so this is safe to run concurrently."""
            def _e(*a, **k):
                if emit_lock is None:
                    _emit(*a, **k)
                else:
                    with emit_lock:
                        _emit(*a, **k)
            _e("write", i=idx + 1, n=len(planned),
               title=change.title, page_id=change.page_id, op=change.op.value)
            try:
                page = self._writer.write(
                    change=change,
                    source_id=source_id,
                    source_title=source_title,
                    source_content=source_content,
                    sibling_index=index_text,
                )
                write_page(self.vault, page)
                _e("wrote", page_id=page.id, title=change.title)
                return page, None
            except Exception as exc:
                _e("write_failed", page_id=change.page_id, error=str(exc)[:160])
                return None, (change.page_id, str(exc))

        # Independent page-writes fan out concurrently in STRUCTURED mode (each
        # is a single LLM call; the cost tracker is thread-safe and the budget
        # was already gated above). REPL mode stays sequential — each REPL write
        # is itself a multi-turn session.
        concurrency = max(1, int(getattr(self.config, "ingest_concurrency", 1) or 1))
        if self.structured and concurrency > 1 and len(planned) > 1:
            import threading
            from concurrent.futures import ThreadPoolExecutor
            emit_lock = threading.Lock()
            results: list = [None] * len(planned)
            with ThreadPoolExecutor(max_workers=min(concurrency, len(planned))) as ex:
                futs = {ex.submit(_write_one, i, ch, emit_lock): i
                        for i, ch in enumerate(planned)}
                for fut in futs:
                    results[futs[fut]] = fut.result()
            for page, fail in results:
                if page is not None:
                    written.append(page)
                if fail is not None:
                    write_failures.append(fail)
        else:
            for idx, change in enumerate(planned):
                page, fail = _write_one(idx, change)
                if page is not None:
                    written.append(page)
                if fail is not None:
                    write_failures.append(fail)

        # Review (batch)
        _emit("review", n=len(written))
        review_results = self._reviewer.review(written)
        all_issues: list[ReviewIssue] = []
        for r in review_results:
            all_issues.extend(r.issues)
        for page_id, err in write_failures:
            # Deferred plan entries have a distinct kind so log readers
            # can tell "not written because budget" apart from "writer
            # crashed".
            kind = "plan_truncated" if err.startswith("plan_truncated:") else "write_failed"
            all_issues.append(
                ReviewIssue(
                    severity=(
                        ReviewSeverity.WARN
                        if kind == "plan_truncated"
                        else ReviewSeverity.ERROR
                    ),
                    page_id=page_id,
                    kind=kind,
                    message=err,
                )
            )

        # Regenerate index and append log
        write_index(self.vault, topic=topic)

        created_ids = [
            p.id for p, ch in _zip_pages_changes(written, plan) if ch.op == PageChangeOp.CREATE
        ]
        updated_ids = [
            p.id for p, ch in _zip_pages_changes(written, plan) if ch.op == PageChangeOp.UPDATE
        ]
        cost = self.cost_tracker.get_summary()["estimated_cost_usd"]

        # Explore: best-effort. Failures don't block the ingest.
        expansion_count = 0
        explore_error: str | None = None
        if run_explore:
            _emit("explore", msg="Exploring connections for expansion")
            try:
                explore_report = self._explorer.explore()
                expansion_count = len(explore_report.proposals)
                _emit("explored", proposals=expansion_count)
            except Exception as exc:
                explore_error = str(exc)
                _emit("explored", proposals=0, error=str(exc)[:160])

        _append_log_entry(
            self.vault,
            plan=plan,
            created=created_ids,
            updated=updated_ids,
            implied=plan.implied_wikilinks,
            issues=all_issues,
            cost=self.cost_tracker.get_summary()["estimated_cost_usd"],
            raw_path=raw_target,
            expansion_count=expansion_count,
            explore_error=explore_error,
        )

        _emit("done", created=len(created_ids), updated=len(updated_ids))
        return IngestReport(
            source_id=source_id,
            source_title=source_title,
            timestamp=timestamp_iso(),
            pages_created=created_ids,
            pages_updated=updated_ids,
            implied_wikilinks=list(plan.implied_wikilinks),
            review_issues=all_issues,
            cost_dollars=self.cost_tracker.get_summary()["estimated_cost_usd"],
            expansion_proposal_count=expansion_count,
        )


# ----- helpers ---------------------------------------------------------------


def _unique_source_id(vault: VaultPaths, stem: str) -> str:
    """Return a source_id that isn't already taken in wiki/sources/."""
    base = slugify(stem)
    candidate = base
    n = 2
    while page_exists(vault, candidate):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _copy_into_raw(vault: VaultPaths, src: Path, source_id: str) -> Path:
    """Place the source file inside the vault's raw/ tree.

    If `src` already lives somewhere inside `vault.raw/` (e.g. a PDF's
    extracted-text companion at `raw/papers/<slug>.md`, or a transcript
    summary at `raw/transcripts/<slug>.md`), leave it where it is.
    Otherwise, copy it to `raw/articles/<source_id>.<ext>` as the
    default bucket for text sources brought in from outside.
    """
    try:
        src.resolve().relative_to(vault.raw.resolve())
        return src
    except ValueError:
        pass

    target_dir = vault.raw_articles
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source_id}{src.suffix.lower() or '.md'}"
    if not target.exists() or target.resolve() != src.resolve():
        shutil.copy2(src, target)
    return target


def _read_topic(vault: VaultPaths) -> str:
    if not vault.claude_md.exists():
        return ""
    text = vault.claude_md.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("# Vault Schema"):
            _, _, after = line.partition("—")
            return after.strip()
    return ""


def _zip_pages_changes(written: list[Page], plan: IngestPlan):
    by_id = {c.page_id: c for c in plan.changes}
    for p in written:
        ch = by_id.get(p.id)
        if ch is not None:
            yield p, ch


def _append_log_entry(
    vault: VaultPaths,
    *,
    plan: IngestPlan,
    created: list[str],
    updated: list[str],
    implied: list[str],
    issues: list[ReviewIssue],
    cost: float,
    raw_path: Path,
    expansion_count: int = 0,
    explore_error: str | None = None,
) -> None:
    body_lines: list[str] = []
    body_lines.append(f"- source_id: `{plan.source_id}`")
    body_lines.append(f"- raw: `{raw_path.as_posix()}`")
    if plan.source_summary:
        body_lines.append(f"- summary: {plan.source_summary}")
    if created:
        body_lines.append(f"- created ({len(created)}): " + ", ".join(f"`{c}`" for c in created))
    if updated:
        body_lines.append(f"- updated ({len(updated)}): " + ", ".join(f"`{u}`" for u in updated))
    if implied:
        body_lines.append(f"- implied wikilinks ({len(implied)}): " + ", ".join(f"`{s}`" for s in implied))
    if issues:
        warn_or_worse = [i for i in issues if i.severity != ReviewSeverity.INFO]
        if warn_or_worse:
            body_lines.append(f"- review issues ({len(warn_or_worse)}):")
            for issue in warn_or_worse[:5]:
                body_lines.append(
                    f"    - **{issue.severity.value}** `{issue.page_id}` "
                    f"[{issue.kind}] {issue.message}"
                )
            if len(warn_or_worse) > 5:
                body_lines.append(f"    - ...and {len(warn_or_worse) - 5} more")
    if explore_error:
        body_lines.append(f"- explore: FAILED — {explore_error}")
    elif expansion_count:
        body_lines.append(f"- explore: {expansion_count} proposals → `wiki/_meta/expansion.md`")
    body_lines.append(f"- cost: ${cost:.4f}")

    append_entry(
        vault,
        op="ingest",
        subject=plan.source_title,
        body="\n".join(body_lines),
    )
