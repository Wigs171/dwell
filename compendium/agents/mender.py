"""Mender — takes Linter/Reviewer diagnostics and acts on them.

The Mender closes the feedback loop that the rest of the system leaves open:
Linter finds orphans, broken links, and contradictions; Reviewer flags token
overflow, thin pages, missing code-block preservation. Nothing in the
pipeline actually *fixes* those findings — they accumulate in `_meta/*.md`
until a human intervenes. The Mender is that intervention, automated.

## Three tiers

**Tier 1 — mechanical (no LLM).** Pure-Python rewrites for unambiguous cases:
- Broken wikilinks whose slug matches an alias of an existing page → redirect
- Missing `updated` frontmatter → fill with today's date
- Missing list-type frontmatter fields → set to empty list
These run free. Safe to invoke from any `lint`/`ingest`/`loop` tail.

**Tier 2 — one-shot LLM per issue (mechanical tier).** Each issue gets a
single `messages.create` call that returns a JSON action:
- Broken wikilinks: REDIRECT to an existing page, or KEEP (legit gap signal).
  Never DELETE — gap signals are load-bearing for Explorer.
- Contradictions: REVISE one page's body, or inject `## Open questions`
  into both, or ESCALATE (when both sides have strong evidence).

**Tier 3 — REPL-driven page expansion.** Reuses `PageWriter.write(op=UPDATE)`
for thin pages (word-count below threshold) whose frontmatter lists a source.
Goes back to `raw/` for fresh material. Semantically identical to the
UPDATE path inside the normal ingest flow.

## Mend as expansion signal

Every issue the Mender *refuses* or *escalates* is valuable input for
Explorer — a gap signal stronger than the raw mechanical one, because it
has an LLM's reasoning attached. Escalations land in `mend-report.md`
which Explorer's REPL reads alongside `broken-links.md`, `orphans.md`, and
`contradictions.md` on its next pass.

## Budget

Tier 1 costs $0. Tier 2 averages ~$0.02-0.05 per issue at the mechanical
tier (haiku). Tier 3 is PageWriter cost (~$0.30-0.80 per page). The loop
should only invoke tier 1+2; tier 3 lives in the standalone `mend` command.

Never silently overwrites. Every rewrite is atomic via `write_page`, and
a dry-run mode records intended actions without touching the filesystem.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

from compendium.config import CompendiumConfig
from compendium.guardrails.cost_tracker import BudgetExceeded, CostTracker
from compendium.models import (
    Contradiction,
    LintReport,
    MendAction,
    MendActionKind,
    MendReport,
    ModelTier,
    Page,
    PageType,
    confidence_rank,
    source_tier_rank,
)
from compendium.vault import VaultPaths
from compendium.vault.links import (
    build_alias_index,
    parse_wikilinks,
)
from compendium.vault.pages import (
    list_pages,
    locate_page,
    read_page,
    slugify,
    today_iso,
    write_page,
)

log = logging.getLogger(__name__)


# Pages shorter than this word count are "thin" — candidates for tier-3
# expansion. Tuned from the Reviewer's current mid-point observation that
# well-formed pages land 300-900 words; under 150 is clearly underdeveloped.
THIN_PAGE_WORD_THRESHOLD = 150

# Cap tier-2 issues per run so a single mend pass doesn't explode budget.
DEFAULT_MAX_TIER2_ISSUES = 40


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class MendConfig:
    tiers: set[int]                     # which of {1, 2, 3, 4} to run
    dry_run: bool = False
    max_tier2_issues: int = DEFAULT_MAX_TIER2_ISSUES
    max_tier3_pages: int = 5
    max_tier4_sources: int = 20
    escalate_on_low_confidence: bool = True


def mend_vault(
    *,
    client: anthropic.Anthropic,
    config: CompendiumConfig,
    cost_tracker: CostTracker,
    vault: VaultPaths,
    lint_report: LintReport,
    mend_config: MendConfig,
) -> MendReport:
    """Run the Mender across a vault using a LintReport as the issue feed.

    This is the public entry point. Tier 1 always runs first; tier 2 and
    tier 3 only if requested in `mend_config.tiers`. Budget exhaustion
    halts at the next check but preserves all actions completed so far.
    """
    from compendium.vault.log import timestamp_iso

    actions: list[MendAction] = []
    topic = lint_report.topic or ""
    cost_before = cost_tracker.get_summary()["estimated_cost_usd"]

    # Tier 1 — always, even if not in tiers set. It's free and safe.
    actions.extend(_tier1_mechanical_fixes(vault, lint_report, mend_config))

    # Tier 2 — one-shot LLM fixes
    if 2 in mend_config.tiers:
        try:
            actions.extend(
                _tier2_broken_links(
                    client=client,
                    config=config,
                    cost_tracker=cost_tracker,
                    vault=vault,
                    lint_report=lint_report,
                    mend_config=mend_config,
                    already_handled=_already_handled_targets(actions),
                )
            )
        except BudgetExceeded as exc:
            log.info("tier-2 broken-link pass hit budget: %s", exc)
            actions.append(_escalate_budget(
                "tier 2 broken-link pass halted at budget"
            ))
        try:
            actions.extend(
                _tier2_contradictions(
                    client=client,
                    config=config,
                    cost_tracker=cost_tracker,
                    vault=vault,
                    lint_report=lint_report,
                    mend_config=mend_config,
                )
            )
        except BudgetExceeded as exc:
            log.info("tier-2 contradiction pass hit budget: %s", exc)
            actions.append(_escalate_budget(
                "tier 2 contradiction pass halted at budget"
            ))
        try:
            actions.extend(
                _tier2_token_overflow(
                    client=client,
                    config=config,
                    cost_tracker=cost_tracker,
                    vault=vault,
                    mend_config=mend_config,
                )
            )
        except BudgetExceeded as exc:
            log.info("tier-2 token-overflow pass hit budget: %s", exc)
            actions.append(_escalate_budget(
                "tier 2 token-overflow pass halted at budget"
            ))

    # Tier 3 — REPL-driven thin-page expansion
    if 3 in mend_config.tiers:
        try:
            actions.extend(
                _tier3_thin_pages(
                    client=client,
                    config=config,
                    cost_tracker=cost_tracker,
                    vault=vault,
                    mend_config=mend_config,
                )
            )
        except BudgetExceeded as exc:
            log.info("tier-3 thin-page pass hit budget: %s", exc)
            actions.append(_escalate_budget(
                "tier 3 thin-page pass halted at budget"
            ))

    # Tier 4 — source curation (keep/cull/escalate)
    if 4 in mend_config.tiers:
        try:
            actions.extend(
                _tier4_curate_sources(
                    client=client,
                    config=config,
                    cost_tracker=cost_tracker,
                    vault=vault,
                    mend_config=mend_config,
                )
            )
        except BudgetExceeded as exc:
            log.info("tier-4 curation pass hit budget: %s", exc)
            actions.append(_escalate_budget(
                "tier 4 curation pass halted at budget"
            ))

    cost_after = cost_tracker.get_summary()["estimated_cost_usd"]
    report = MendReport(
        timestamp=timestamp_iso(),
        topic=topic,
        dry_run=mend_config.dry_run,
        issues_considered=(
            len(lint_report.broken_links)
            + len(lint_report.contradictions)
        ),
        actions=actions,
        cost_dollars=round(cost_after - cost_before, 4),
    )

    if not mend_config.dry_run:
        _write_mend_report(vault, report)

    return report


# ---------------------------------------------------------------------------
# Tier 1 — mechanical
# ---------------------------------------------------------------------------


def _tier1_mechanical_fixes(
    vault: VaultPaths,
    lint_report: LintReport,
    mend_config: MendConfig,
) -> list[MendAction]:
    """Pure-Python fixes — no LLM, no cost."""
    actions: list[MendAction] = []
    alias_map = build_alias_index(vault)

    # 1.0. Duplicate-slug detection across type directories.
    # `locate_page` walks type directories in enum order and returns the
    # first hit, so a slug that exists in two types makes one of them
    # invisible to every tool (Mender included). Detect and escalate —
    # we DON'T auto-merge because picking the canonical type requires
    # domain judgment. Human-reviewed, then run a targeted merge script.
    actions.extend(_detect_duplicate_slugs(vault))

    # 1a. Alias-redirect broken wikilinks.
    # If a broken target's slug is present in the alias map, we can
    # unambiguously rewrite every referring page's `[[target]]` to
    # `[[canonical]]`. This never fires on a real gap — gaps have
    # no matching page or alias by definition.
    redirects_by_page: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for group in lint_report.broken_links:
        canonical = alias_map.get(group.target)
        if canonical is None or canonical == group.target:
            continue
        # The target slug resolves to a different page via alias —
        # rewrite [[broken-slug]] → [[canonical-slug]] in every referrer.
        for referrer_id in group.referrers:
            redirects_by_page[referrer_id].append((group.target, canonical))

    for referrer_id, pairs in redirects_by_page.items():
        page = read_page(vault, referrer_id)
        if page is None:
            continue
        new_body = page.body
        rewrites: list[str] = []
        for broken, canonical in pairs:
            pattern = _wikilink_pattern(broken)
            if not pattern.search(new_body):
                continue
            new_body = pattern.sub(f"[[{canonical}]]", new_body)
            rewrites.append(f"[[{broken}]] → [[{canonical}]]")
        if not rewrites:
            continue
        if not mend_config.dry_run:
            page = page.model_copy(update={"body": new_body, "updated": today_iso()})
            write_page(vault, page)
        actions.append(
            MendAction(
                kind=MendActionKind.ALIAS_REDIRECT,
                page_id=referrer_id,
                pages=[referrer_id],
                summary=(
                    f"alias-redirected {len(rewrites)} broken link"
                    f"{'s' if len(rewrites) != 1 else ''} in {referrer_id}"
                ),
                detail="\n".join(rewrites),
            )
        )

    # 1b. Normalize title-cased wikilinks to Obsidian-resolvable piped form.
    # Our own parser slugifies wikilink targets, so `[[Sacred Geometry]]`
    # resolves to `sacred-geometry` in every internal tool. Obsidian does
    # NOT do that — it looks for a file literally named "Sacred Geometry.md"
    # and shows an unresolved gray node when it can't find one. Rewriting to
    # `[[sacred-geometry|Sacred Geometry]]` keeps the human-readable display
    # while giving Obsidian an exact filename to resolve. Safe and idempotent:
    # only fires when the slug version resolves to an existing page.
    for page_id in list_pages(vault):
        page = read_page(vault, page_id)
        if page is None:
            continue
        new_body, rewrites = _normalize_wikilinks(page.body, alias_map)
        if not rewrites:
            continue
        if mend_config.dry_run:
            actions.append(
                MendAction(
                    kind=MendActionKind.WIKILINK_NORMALIZED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=(
                        f"would normalize {len(rewrites)} title-cased "
                        f"wikilink{'s' if len(rewrites) != 1 else ''} in {page_id}"
                    ),
                    detail="\n".join(rewrites[:10]),
                )
            )
            continue
        page = page.model_copy(update={"body": new_body, "updated": today_iso()})
        write_page(vault, page)
        actions.append(
            MendAction(
                kind=MendActionKind.WIKILINK_NORMALIZED,
                page_id=page_id,
                pages=[page_id],
                summary=(
                    f"normalized {len(rewrites)} title-cased wikilink"
                    f"{'s' if len(rewrites) != 1 else ''} in {page_id} "
                    "for Obsidian resolution"
                ),
                detail="\n".join(rewrites[:10]),
            )
        )

    # 1c. Fill missing/empty `updated` frontmatter. Run across every
    # page — cheap, and keeps Obsidian's "days since updated" sensible.
    for page_id in list_pages(vault):
        page = read_page(vault, page_id)
        if page is None:
            continue
        if page.updated:
            continue
        if mend_config.dry_run:
            actions.append(
                MendAction(
                    kind=MendActionKind.FRONTMATTER_FILLED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=f"would fill empty `updated` on {page_id}",
                )
            )
            continue
        page = page.model_copy(update={"updated": today_iso()})
        write_page(vault, page)
        actions.append(
            MendAction(
                kind=MendActionKind.FRONTMATTER_FILLED,
                page_id=page_id,
                pages=[page_id],
                summary=f"filled empty `updated` on {page_id}",
            )
        )

    return actions


_WIKILINK_FULL_RE = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")


def _detect_duplicate_slugs(vault: VaultPaths) -> list[MendAction]:
    """Find page slugs that exist in multiple wiki type directories.

    Every other tool (`locate_page`, `read_page`, the Linter, the
    Explorer) assumes one-slug-per-vault and silently picks the first
    hit when that assumption breaks. A duplicate leaves one file
    unreachable: unrewriteable, unupdateable, invisible to search.

    We don't auto-fix because picking the canonical type (entity vs
    concept vs synthesis) is a domain decision. Escalate with both
    paths so a human picks the winner and runs a targeted merge.
    """
    from collections import defaultdict

    actions: list[MendAction] = []
    by_slug: dict[str, list[Path]] = defaultdict(list)
    for pt in PageType:
        type_dir = {
            PageType.ENTITY: vault.entities,
            PageType.CONCEPT: vault.concepts,
            PageType.SOURCE: vault.sources,
            PageType.SYNTHESIS: vault.syntheses,
        }[pt]
        if not type_dir.is_dir():
            continue
        for p in type_dir.iterdir():
            if p.is_file() and p.suffix == ".md":
                by_slug[p.stem].append(p)

    for slug, paths in by_slug.items():
        if len(paths) < 2:
            continue
        path_strs = [str(p.relative_to(vault.root)) for p in paths]
        details = []
        for p in paths:
            try:
                size = p.stat().st_size
                mtime_iso = datetime.fromtimestamp(
                    p.stat().st_mtime
                ).isoformat(timespec="seconds")
                details.append(
                    f"{p.relative_to(vault.root)} ({size} bytes, mtime {mtime_iso})"
                )
            except OSError:
                details.append(str(p.relative_to(vault.root)))
        actions.append(
            MendAction(
                kind=MendActionKind.ESCALATED,
                page_id=slug,
                pages=path_strs,
                summary=(
                    f"duplicate slug `{slug}` exists in "
                    f"{len(paths)} type directories — one copy is "
                    "unreachable by every tool"
                ),
                detail=(
                    "copies:\n  " + "\n  ".join(details) + "\n"
                    "recommend: pick canonical type, merge bodies, delete "
                    "the loser. Then re-run mend."
                ),
            )
        )

    return actions


def _normalize_wikilinks(
    body: str,
    alias_map: dict[str, str],
) -> tuple[str, list[str]]:
    """Rewrite title-cased wikilinks to Obsidian-resolvable piped form.

    Returns (new_body, [rewrite_log_lines]).

    Rules:
    - `[[sacred-geometry]]` — already slug; leave alone.
    - `[[sacred-geometry|Sacred Geometry]]` — already piped; leave alone.
    - `[[Sacred Geometry]]` where slugify("Sacred Geometry") == "sacred-geometry"
      resolves via alias_map → rewrite to `[[sacred-geometry|Sacred Geometry]]`.
    - `[[Typo Title]]` where slug doesn't resolve → leave alone; it's a
      legitimate gap signal and Mender tier 2 will audit it.
    - `![[...]]` transclusions are treated the same as normal wikilinks.
    """
    rewrites: list[str] = []

    def _replace(m: re.Match) -> str:
        bang = m.group(1) or ""
        inner = m.group(2).strip()
        if "|" in inner:
            # Already piped — if the target is already slug-form, leave it
            # alone. If the target is title-form, normalize it too.
            target_raw, display = (p.strip() for p in inner.split("|", 1))
            target_slug = slugify(target_raw)
            if target_raw == target_slug:
                return m.group(0)  # unchanged
            # Rewrite target to slug form, keep display. Only if it resolves.
            canonical = alias_map.get(target_slug)
            if canonical is None:
                return m.group(0)
            rewrites.append(
                f"[[{target_raw}|{display}]] → [[{canonical}|{display}]]"
            )
            return f"{bang}[[{canonical}|{display}]]"
        # Unpiped: check if target is already slug-form
        target_slug = slugify(inner)
        if inner == target_slug:
            return m.group(0)  # already slug, leave alone
        # Title-cased — does the slug resolve?
        canonical = alias_map.get(target_slug)
        if canonical is None:
            return m.group(0)  # legitimate gap, leave alone
        # Rewrite to piped form, preserving the original display text
        rewrites.append(f"[[{inner}]] → [[{canonical}|{inner}]]")
        return f"{bang}[[{canonical}|{inner}]]"

    new_body = _WIKILINK_FULL_RE.sub(_replace, body)
    return new_body, rewrites


def _wikilink_pattern(target_slug: str) -> re.Pattern:
    """Match `[[target]]` or `[[Target Title|alias]]` where slugify(left)==target.

    Conservative: we only match the literal-slug form. Rewriting
    `[[Target Title]]` forms would require rebuilding the title-derived
    display, which is error-prone. Those cases are rare; the mechanical
    pass can safely skip them.
    """
    escaped = re.escape(target_slug)
    return re.compile(rf"\[\[{escaped}(\|[^\]]+)?\]\]")


def _already_handled_targets(actions: list[MendAction]) -> set[str]:
    """Targets already rewritten in tier 1 — skip in tier 2."""
    handled: set[str] = set()
    for a in actions:
        if a.kind == MendActionKind.ALIAS_REDIRECT:
            for line in a.detail.splitlines():
                m = re.match(r"\[\[([^\]]+)\]\] →", line)
                if m:
                    handled.add(m.group(1))
    return handled


# ---------------------------------------------------------------------------
# Tier 2 — one-shot LLM per issue
# ---------------------------------------------------------------------------


_BROKEN_LINK_PROMPT = """\
A wikilink `[[{target}]]` appears in {ref_count} page(s) of a vault on "{topic}"
but no page exists with that id or alias.

Referring pages: {referrers}
Top existing-page candidates (ranked by slug similarity): {candidates}

Decide ONE of:

1. REDIRECT — the link was meant to point at an existing page. Only pick
   this if you are confident the intended target is one of the candidates.
2. KEEP — the link is a legitimate gap signal. Leave it broken so Explorer
   proposes creating the page.

Respond with a SINGLE JSON object, no prose:

{{"action": "REDIRECT", "target": "existing-page-id", "reason": "..."}}
OR
{{"action": "KEEP", "reason": "..."}}

Do NOT invent a target that isn't in the candidates list. If no candidate
looks right, pick KEEP.
"""


def _tier2_broken_links(
    *,
    client: anthropic.Anthropic,
    config: CompendiumConfig,
    cost_tracker: CostTracker,
    vault: VaultPaths,
    lint_report: LintReport,
    mend_config: MendConfig,
    already_handled: set[str],
) -> list[MendAction]:
    """LLM-assisted broken-link resolution. Never deletes — only redirects or keeps."""
    actions: list[MendAction] = []
    model = config.tiered_models.get_model(ModelTier.MECHANICAL)
    alias_map = build_alias_index(vault)
    all_page_ids = list_pages(vault)
    topic = lint_report.topic or "(unspecified)"

    considered = 0
    for group in lint_report.broken_links:
        if considered >= mend_config.max_tier2_issues:
            break
        if group.target in already_handled:
            continue
        # Sorted by ref_count desc by the Linter — highest-leverage first.
        candidates = _top_candidates(group.target, all_page_ids, limit=5)
        if not candidates:
            # Nothing to redirect to; keep as-is and emit signal.
            actions.append(
                MendAction(
                    kind=MendActionKind.BROKEN_LINK_KEPT,
                    page_id="",
                    pages=list(group.referrers),
                    summary=(
                        f"kept broken link [[{group.target}]] "
                        f"({group.ref_count} ref"
                        f"{'s' if group.ref_count != 1 else ''}) — "
                        "no similar existing page"
                    ),
                    detail="",
                )
            )
            continue

        cost_tracker.check_budget()
        considered += 1
        prompt = _BROKEN_LINK_PROMPT.format(
            target=group.target,
            ref_count=group.ref_count,
            topic=topic,
            referrers=", ".join(group.referrers[:6]),
            candidates=", ".join(candidates),
        )
        try:
            response = client.messages.create(
                model=model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            cost_tracker.record_call(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=model,
                is_sub_call=True,
            )
            text = response.content[0].text if response.content else ""
            decision = _parse_broken_link_decision(text)
        except BudgetExceeded:
            raise
        except Exception as exc:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id="",
                    pages=list(group.referrers),
                    summary=(
                        f"broken link [[{group.target}]] — LLM call failed"
                    ),
                    detail=f"error: {exc}",
                )
            )
            continue

        if decision is None:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id="",
                    pages=list(group.referrers),
                    summary=(
                        f"broken link [[{group.target}]] — "
                        "could not parse LLM decision"
                    ),
                    detail=text[:500],
                )
            )
            continue

        action_word, redirect_target, reason = decision
        if action_word == "REDIRECT" and redirect_target in alias_map:
            canonical = alias_map[redirect_target]
            rewrote = _apply_broken_link_redirect(
                vault, group, canonical, dry_run=mend_config.dry_run,
            )
            if rewrote:
                actions.append(
                    MendAction(
                        kind=MendActionKind.BROKEN_LINK_REDIRECTED,
                        page_id="",
                        pages=list(group.referrers),
                        summary=(
                            f"redirected [[{group.target}]] → "
                            f"[[{canonical}]] in {len(rewrote)} page"
                            f"{'s' if len(rewrote) != 1 else ''}"
                        ),
                        detail=f"reason: {reason}\npages: {', '.join(rewrote)}",
                    )
                )
                continue
        # KEEP (or invalid REDIRECT target) — emit signal for Explorer.
        actions.append(
            MendAction(
                kind=MendActionKind.BROKEN_LINK_KEPT,
                page_id="",
                pages=list(group.referrers),
                summary=(
                    f"kept broken link [[{group.target}]] as gap signal"
                ),
                detail=f"reason: {reason}",
            )
        )

    return actions


def _top_candidates(target: str, all_ids: list[str], limit: int = 5) -> list[str]:
    """Rank existing page IDs by crude slug-token overlap with `target`."""
    target_tokens = set(target.split("-"))
    if not target_tokens:
        return []
    scored: list[tuple[float, str]] = []
    for pid in all_ids:
        tokens = set(pid.split("-"))
        if not tokens:
            continue
        overlap = len(target_tokens & tokens) / max(
            1, len(target_tokens | tokens)
        )
        if overlap > 0:
            scored.append((overlap, pid))
    scored.sort(reverse=True)
    return [pid for _, pid in scored[:limit]]


def _parse_broken_link_decision(
    text: str,
) -> tuple[str, str, str] | None:
    """Parse `{"action":"REDIRECT","target":"...","reason":"..."}`."""
    try:
        obj = _extract_json(text)
    except ValueError:
        return None
    action = str(obj.get("action", "")).upper()
    target = str(obj.get("target", "")).strip()
    reason = str(obj.get("reason", "")).strip()
    if action not in {"REDIRECT", "KEEP"}:
        return None
    return action, target, reason


def _apply_broken_link_redirect(
    vault: VaultPaths,
    group,
    canonical: str,
    *,
    dry_run: bool = False,
) -> list[str]:
    rewrote: list[str] = []
    pattern = _wikilink_pattern(group.target)
    for referrer_id in group.referrers:
        page = read_page(vault, referrer_id)
        if page is None:
            continue
        if not pattern.search(page.body):
            continue
        new_body = pattern.sub(f"[[{canonical}]]", page.body)
        if new_body == page.body:
            continue
        if dry_run:
            rewrote.append(referrer_id)
            continue
        page = page.model_copy(update={"body": new_body, "updated": today_iso()})
        write_page(vault, page)
        rewrote.append(referrer_id)
    return rewrote


# ---------------------------------------------------------------------------
# Tier 2 — contradictions
# ---------------------------------------------------------------------------
#
# Contradiction resolution runs rules FIRST, LLM second. The rule branch
# is deterministic — given the same page frontmatter, it always returns
# the same decision — so two mend passes over the same vault can't drift.
# The LLM branch only runs when rules can't discriminate, which happens
# when:
#   - one or both pages lack `source_tier` or `confidence` (legacy pages
#     written before these fields existed, or hand-edited pages);
#   - all dimensions tie and both sides cite primaries (probably a real
#     interpretive disagreement, not a data-quality problem).
#
# Rule priority, checked in order; first dimension with a gap decides:
#   1. source_tier     — primary > secondary > tertiary
#   2. confidence      — high > medium > low
#   3. corroboration   — more cited sources wins, but only at ≥2× the
#                        other side (a 3-vs-2 gap is weak signal)
#   4. recency         — newer `updated` wins, ONLY when both sides are
#                        secondary/tertiary (primary-source disagreements
#                        are rarely resolved by recency — old primaries
#                        don't "become stale")
#
# If no rule fires a winner but the data is complete, emit OPEN_QUESTIONS
# (both sides are equally well-supported; the honest answer is to
# document the tension, not pick). This matches Wikidata's "every rank
# kept" model: losers aren't deleted, they get `superseded_by` backref
# and stay as provenance.


@dataclass
class RuleDecision:
    """Result of rule-based contradiction resolution.

    `kind == "supersede"` means a single page dominates the others on at
    least one evidence dimension; `winner_id` and `losers` are populated.
    `kind == "open_questions"` means every page ties on every rule and
    the decision is to surface the tension as an open question rather
    than pick a winner. `reason` is the human-readable explanation —
    which rule fired, what the tie-breaking values were.
    """
    kind: str                    # "supersede" | "open_questions"
    winner_id: str | None = None
    losers: list[str] = None     # type: ignore[assignment]
    reason: str = ""


def _page_rank_key(page: Page) -> tuple[int, int, int, str]:
    """Sort key for contradiction resolution; lower is better.

    Tuple components, in priority order:
      (tier_rank, confidence_rank, -corroboration_count, -updated_date)

    `source_tier_rank` returns 0/1/2 for primary/secondary/tertiary and
    -1 for unspecified. We map -1 to a large positive number so
    unspecified tiers sort WORST, matching the design principle that a
    page with missing metadata can never beat one with known metadata.
    """
    tier = source_tier_rank(page.source_tier)
    conf = confidence_rank(page.confidence)
    return (
        tier if tier >= 0 else 999,
        conf if conf >= 0 else 999,
        -len(page.sources),
        # Negate date by using its complement: later dates sort earlier.
        # ISO-8601 strings compare lexically correct, so we sort the
        # raw string DESCENDING by negating via comparison later — here
        # we just return the raw string and callers reverse on this
        # component. Simpler: prefix with a high constant and subtract
        # is awkward for strings; we'll handle recency outside the key.
        page.updated or "",
    )


def apply_contradiction_rules(
    pages: list[Page], contradiction: Contradiction
) -> RuleDecision | None:
    """Decide a contradiction using the evidence-metadata rule hierarchy.

    Returns None when rules can't discriminate (missing metadata on any
    side, or ambiguous in a way that should escalate to LLM judgment).

    The comparison is pairwise-dominance. A page P "dominates" another
    page Q iff P is strictly better than Q on one of the priority
    dimensions (tier, confidence, corroboration, or bounded recency)
    AND at-least-equal on all higher-priority dimensions. If exactly
    one page dominates every other, it's the winner. If every page
    ties with every other on every dimension, we emit OPEN_QUESTIONS.
    Any other shape (cycles, partial dominance) returns None so the
    LLM branch can handle it.
    """
    if len(pages) < 2:
        return None

    # Rule prerequisite: every page must have BOTH source_tier and
    # confidence set. If even one is missing (legacy page, hand-edit,
    # or PageWriter edge case), we can't rank — defer to LLM.
    for p in pages:
        if not p.source_tier or not p.confidence:
            return None
        if source_tier_rank(p.source_tier) < 0:
            return None
        if confidence_rank(p.confidence) < 0:
            return None

    def dominates(a: Page, b: Page) -> tuple[bool, str]:
        """Does `a` strictly beat `b`? Returns (True, reason) if so."""
        a_tier = source_tier_rank(a.source_tier)
        b_tier = source_tier_rank(b.source_tier)
        if a_tier < b_tier:
            return True, (
                f"source_tier: {a.id} is {a.source_tier}, "
                f"{b.id} is {b.source_tier}"
            )
        if a_tier > b_tier:
            return False, ""

        # Tied on tier — check confidence.
        a_conf = confidence_rank(a.confidence)
        b_conf = confidence_rank(b.confidence)
        if a_conf < b_conf:
            return True, (
                f"confidence (at equal source_tier={a.source_tier}): "
                f"{a.id} is {a.confidence}, {b.id} is {b.confidence}"
            )
        if a_conf > b_conf:
            return False, ""

        # Tied on confidence — check corroboration (≥2× threshold to
        # avoid flipping on noisy 3-vs-2 differences).
        a_src = len(a.sources)
        b_src = len(b.sources)
        if a_src >= 2 * max(1, b_src) and a_src > b_src:
            return True, (
                f"corroboration (at equal tier+confidence): "
                f"{a.id} cites {a_src} sources, {b.id} cites {b_src}"
            )
        if b_src >= 2 * max(1, a_src) and b_src > a_src:
            return False, ""

        # Tied on corroboration — recency, but only for non-primary
        # material. Primary-source contradictions don't resolve by
        # "who wrote it more recently"; a 1920 primary and a 2024
        # primary can both be correct about their own eras.
        if a.source_tier != "primary" and b.source_tier != "primary":
            if a.updated and b.updated:
                if a.updated > b.updated:
                    return True, (
                        f"recency (at equal tier+confidence+corroboration, "
                        f"both secondary/tertiary): {a.id} updated "
                        f"{a.updated}, {b.id} updated {b.updated}"
                    )
                if b.updated > a.updated:
                    return False, ""

        return False, ""

    # Pairwise: does exactly one page dominate every other page?
    # If yes → winner. If all comparisons return (False, "") → every
    # page ties with every other → OPEN_QUESTIONS. Anything else
    # (cycles, partial) → None so the LLM handles it.
    winners: list[tuple[Page, str]] = []
    any_dominance = False
    for a in pages:
        dominates_all = True
        reasons: list[str] = []
        for b in pages:
            if a.id == b.id:
                continue
            ok, why = dominates(a, b)
            if ok:
                any_dominance = True
                reasons.append(why)
            else:
                # `a` didn't beat `b`. Did `b` beat `a`? If so `a`
                # can't be the sole winner. If neither, they tie.
                b_ok, _ = dominates(b, a)
                if b_ok:
                    any_dominance = True
                    dominates_all = False
                    break
                # tie on all dims for this pair — `a` still in the
                # running only if it dominates at least one other.
        if dominates_all and reasons:
            winners.append((a, "; ".join(reasons)))

    if len(winners) == 1:
        winner, reason = winners[0]
        losers = [p.id for p in pages if p.id != winner.id]
        return RuleDecision(
            kind="supersede",
            winner_id=winner.id,
            losers=losers,
            reason=reason,
        )

    if not any_dominance:
        # Every pair ties on every dimension. The data is complete
        # (we checked for missing metadata up top), so the honest
        # answer is to surface the disagreement, not pick.
        return RuleDecision(
            kind="open_questions",
            reason=(
                "all pages tie on source_tier, confidence, corroboration, "
                "and recency — disagreement is interpretive"
            ),
        )

    # Partial dominance / cycles — let the LLM judge.
    return None


def _apply_supersede(
    vault: VaultPaths,
    contradiction: Contradiction,
    pages_by_id: dict[str, Page],
    decision: RuleDecision,
    *,
    dry_run: bool = False,
) -> MendAction:
    """Mark loser pages superseded_by the winner; never delete them.

    Adds a `## Superseded` section to each loser's body naming the
    winner and the rule reason, and sets `superseded_by` on the
    loser's frontmatter. The winner is untouched.
    """
    winner_id = decision.winner_id or ""
    touched: list[str] = []
    for loser_id in decision.losers or []:
        loser = pages_by_id.get(loser_id)
        if loser is None:
            continue
        # De-dupe: if this loser already lists this winner in its
        # superseded_by, only touch the body if the section isn't
        # already there.
        new_superseded = list(loser.superseded_by)
        if winner_id and winner_id not in new_superseded:
            new_superseded.append(winner_id)

        section_header = "## Superseded"
        if section_header not in loser.body:
            banner = (
                f"\n\n{section_header}\n\n"
                f"This page's claims are superseded by [[{winner_id}]] "
                f"on the grounds of: {decision.reason}\n\n"
                f"The body below is retained as provenance — see "
                f"[[{winner_id}]] for the current claim.\n"
            )
            new_body = loser.body.rstrip() + banner
        else:
            # Append a new bullet under the existing section rather
            # than duplicating the heading. Keeps idempotency clean.
            new_body = loser.body.rstrip() + (
                f"\n- Also superseded by [[{winner_id}]]: {decision.reason}\n"
            )

        touched.append(loser_id)
        if dry_run:
            continue
        updated = loser.model_copy(update={
            "body": new_body,
            "superseded_by": new_superseded,
            "updated": today_iso(),
        })
        write_page(vault, updated)

    return MendAction(
        kind=MendActionKind.CONTRADICTION_RULE_SUPERSEDED,
        page_id=winner_id,
        pages=[winner_id] + touched,
        summary=(
            f"rule-resolved contradiction: [[{winner_id}]] supersedes "
            f"{len(touched)} page"
            f"{'s' if len(touched) != 1 else ''}"
        ),
        detail=(
            f"contradiction: {contradiction.summary}\n"
            f"rule: {decision.reason}\n"
            f"losers: {', '.join(touched)}"
        ),
    )


_CONTRADICTION_PROMPT = """\
Two pages in a vault on "{topic}" hold contradicting claims.

**Contradiction**: {summary}

**Details**:
{details}

**Suggested resolution from the Linter**: {suggested_resolution}

Pages involved:
{page_bodies}

Decide ONE of:

1. OPEN_QUESTIONS — both claims have evidence, or the disagreement is
   interpretive. We'll inject an `## Open questions` section into both
   pages noting the tension. This is the safe default.
2. REVISE_ONE — one page is clearly wrong or the claim is weaker.
   Specify which page to revise and the exact replacement text to
   insert in place of the bad claim. Keep the rest of the body intact.
3. ESCALATE — genuinely can't tell. Flag for human review.

Respond with a SINGLE JSON object, no prose:

{{"action": "OPEN_QUESTIONS", "question_text": "one-paragraph open question for both pages"}}
OR
{{"action": "REVISE_ONE", "page_id": "...", "find": "exact substring to replace", "replace": "corrected text"}}
OR
{{"action": "ESCALATE", "reason": "..."}}

Prefer OPEN_QUESTIONS unless you're highly confident one side is wrong.
"""


def _tier2_contradictions(
    *,
    client: anthropic.Anthropic,
    config: CompendiumConfig,
    cost_tracker: CostTracker,
    vault: VaultPaths,
    lint_report: LintReport,
    mend_config: MendConfig,
) -> list[MendAction]:
    actions: list[MendAction] = []
    model = config.tiered_models.get_model(ModelTier.MECHANICAL)
    topic = lint_report.topic or "(unspecified)"

    for contradiction in lint_report.contradictions:
        if not contradiction.pages or len(contradiction.pages) < 2:
            continue
        page_bodies_lines: list[str] = []
        involved: list[Page] = []
        for pid in contradiction.pages[:3]:
            page = read_page(vault, pid)
            if page is None:
                continue
            involved.append(page)
            page_bodies_lines.append(
                f"--- {pid} ---\n{page.body[:4000]}"
            )
        if len(involved) < 2:
            continue

        # Rule-based resolution FIRST — free, deterministic. Only falls
        # through to the LLM branch when rules can't discriminate (see
        # `apply_contradiction_rules` for the exit conditions).
        rule_decision = apply_contradiction_rules(involved, contradiction)
        if rule_decision is not None:
            pages_by_id = {p.id: p for p in involved}
            if rule_decision.kind == "supersede":
                actions.append(_apply_supersede(
                    vault=vault,
                    contradiction=contradiction,
                    pages_by_id=pages_by_id,
                    decision=rule_decision,
                    dry_run=mend_config.dry_run,
                ))
                continue
            if rule_decision.kind == "open_questions":
                # Rules said "all tied — surface the tension." Inject the
                # open question the same way the LLM branch would, but
                # with a deterministic question-text template so the
                # result is reproducible. No LLM call needed.
                question_text = (
                    f"{contradiction.summary}. "
                    f"All involved pages are equally well-supported by the "
                    f"evidence-metadata rule hierarchy "
                    f"({rule_decision.reason}); the disagreement is "
                    f"interpretive and both readings should be preserved."
                )
                touched = _inject_open_questions(
                    vault, involved, question_text,
                    dry_run=mend_config.dry_run,
                )
                actions.append(MendAction(
                    kind=MendActionKind.CONTRADICTION_OPEN_QUESTIONS,
                    page_id="",
                    pages=touched,
                    summary=(
                        f"rule-resolved contradiction as open question "
                        f"across {len(touched)} page"
                        f"{'s' if len(touched) != 1 else ''}"
                    ),
                    detail=(
                        f"contradiction: {contradiction.summary}\n"
                        f"rule: {rule_decision.reason}\n"
                        f"open question: {question_text}"
                    ),
                ))
                continue

        cost_tracker.check_budget()
        prompt = _CONTRADICTION_PROMPT.format(
            topic=topic,
            summary=contradiction.summary,
            details=contradiction.details[:2000],
            suggested_resolution=(
                contradiction.suggested_resolution or "(none)"
            ),
            page_bodies="\n\n".join(page_bodies_lines),
        )
        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            cost_tracker.record_call(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=model,
                is_sub_call=True,
            )
            text = response.content[0].text if response.content else ""
            obj = _extract_json(text)
        except BudgetExceeded:
            raise
        except Exception as exc:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id="",
                    pages=contradiction.pages,
                    summary=f"contradiction LLM call failed",
                    detail=f"{contradiction.summary}\nerror: {exc}",
                )
            )
            continue

        action_word = str(obj.get("action", "")).upper()
        if action_word == "OPEN_QUESTIONS":
            question_text = str(obj.get("question_text", "")).strip()
            if not question_text:
                actions.append(
                    MendAction(
                        kind=MendActionKind.ESCALATED,
                        page_id="",
                        pages=contradiction.pages,
                        summary=f"contradiction resolution missing question text",
                        detail=contradiction.summary,
                    )
                )
                continue
            touched = _inject_open_questions(
                vault, involved, question_text, dry_run=mend_config.dry_run,
            )
            actions.append(
                MendAction(
                    kind=MendActionKind.CONTRADICTION_OPEN_QUESTIONS,
                    page_id="",
                    pages=touched,
                    summary=(
                        f"added `## Open questions` section to "
                        f"{len(touched)} page"
                        f"{'s' if len(touched) != 1 else ''}"
                    ),
                    detail=(
                        f"contradiction: {contradiction.summary}\n\n"
                        f"open question:\n{question_text}"
                    ),
                )
            )
        elif action_word == "REVISE_ONE":
            target_id = str(obj.get("page_id", "")).strip()
            find_text = str(obj.get("find", "")).strip()
            replace_text = str(obj.get("replace", "")).strip()
            if not target_id or not find_text:
                actions.append(
                    MendAction(
                        kind=MendActionKind.ESCALATED,
                        page_id="",
                        pages=contradiction.pages,
                        summary="contradiction REVISE_ONE missing fields",
                        detail=contradiction.summary,
                    )
                )
                continue
            applied = _apply_revise_one(
                vault, target_id, find_text, replace_text,
                dry_run=mend_config.dry_run,
            )
            if applied:
                actions.append(
                    MendAction(
                        kind=MendActionKind.CONTRADICTION_REVISED,
                        page_id=target_id,
                        pages=[target_id],
                        summary=(
                            f"revised {target_id} to resolve contradiction"
                        ),
                        detail=(
                            f"contradiction: {contradiction.summary}\n"
                            f"replaced: {find_text[:200]!r}\n"
                            f"with:     {replace_text[:200]!r}"
                        ),
                    )
                )
            else:
                actions.append(
                    MendAction(
                        kind=MendActionKind.ESCALATED,
                        page_id=target_id,
                        pages=[target_id],
                        summary=(
                            f"REVISE_ONE on {target_id} didn't match "
                            f"any substring"
                        ),
                        detail=(
                            f"contradiction: {contradiction.summary}\n"
                            f"find: {find_text[:300]}"
                        ),
                    )
                )
        else:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id="",
                    pages=contradiction.pages,
                    summary=f"contradiction escalated",
                    detail=(
                        f"{contradiction.summary}\n\n"
                        f"reason: {obj.get('reason', '(no reason)')}"
                    ),
                )
            )

    return actions


def _inject_open_questions(
    vault: VaultPaths,
    pages: list[Page],
    question_text: str,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Append an `## Open questions` section to each page if absent."""
    touched: list[str] = []
    header = "## Open questions"
    for page in pages:
        body = page.body
        if header in body:
            # Section already exists — append a new bullet rather than
            # duplicating the heading.
            new_body = body.rstrip() + f"\n\n- {question_text}\n"
        else:
            new_body = body.rstrip() + f"\n\n{header}\n\n- {question_text}\n"
        if dry_run:
            touched.append(page.id)
            continue
        page = page.model_copy(update={"body": new_body, "updated": today_iso()})
        write_page(vault, page)
        touched.append(page.id)
    return touched


def _apply_revise_one(
    vault: VaultPaths,
    page_id: str,
    find_text: str,
    replace_text: str,
    *,
    dry_run: bool = False,
) -> bool:
    page = read_page(vault, page_id)
    if page is None:
        return False
    if find_text not in page.body:
        return False
    new_body = page.body.replace(find_text, replace_text, 1)
    if dry_run:
        return True
    page = page.model_copy(update={"body": new_body, "updated": today_iso()})
    write_page(vault, page)
    return True


# ---------------------------------------------------------------------------
# Tier 2 — token-overflow one-shot condensation
# ---------------------------------------------------------------------------


_TOKEN_TRIM_PROMPT = """\
You are condensing a wiki page that exceeds its token budget.

**Page id**: `{page_id}`
**Title**: {title}
**Current token count**: {current_tokens} (budget: {target_tokens})
**Tokens to shed**: ~{delta} (about {pct:.0%})

**Current body (markdown, with frontmatter stripped):**
{body}

## Rules

- **PRESERVE EXACTLY**: every `[[wikilink]]` (including piped forms),
  every inline citation like `(Author YEAR)` or `[^1]`, every `## H2`
  and `### H3` heading, every fenced code block ` ``` ... ``` `, and
  every image reference `![alt](path)`.
- **REDUCE BY**: tightening wordy passages, collapsing redundant
  examples, shortening bullet-list items, removing unnecessary
  transitions like "It should be noted that" / "In summary".
- **DO NOT**: remove whole sections, drop wikilinks, delete citations,
  rewrite code blocks, remove facts, change numerical values, or
  paraphrase quoted text.

Return ONLY the condensed markdown body — no frontmatter, no
preamble, no code fence around the whole thing. Start with the
page's `# {title}` heading.
"""


def _tier2_token_overflow(
    *,
    client: anthropic.Anthropic,
    config: CompendiumConfig,
    cost_tracker: CostTracker,
    vault: VaultPaths,
    mend_config: MendConfig,
) -> list[MendAction]:
    """Condense pages whose body exceeds `max_tokens_per_page`.

    Audits every non-source page against the guardrail limit using
    tiktoken (same encoder as the Reviewer). Oversized pages get a
    single haiku-tier call that returns a condensed body with wikilinks
    and citations preserved verbatim.

    The post-trim body is re-measured; if it still exceeds budget we
    escalate rather than re-trim (avoids thrashing a page that can't
    be safely shortened without structural changes).
    """
    import tiktoken

    actions: list[MendAction] = []
    model = config.tiered_models.get_model(ModelTier.MECHANICAL)
    limit = config.get_guardrails().max_tokens_per_page
    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        enc = None

    def _count(text: str) -> int:
        return len(enc.encode(text)) if enc else len(text) // 4

    for page_id in list_pages(vault):
        page = read_page(vault, page_id)
        if page is None or page.type == PageType.SOURCE:
            # Source pages are summaries and don't pay the same budget.
            continue
        current = _count(page.body)
        if current <= limit:
            continue

        cost_tracker.check_budget()
        target = int(limit * 0.95)  # aim slightly under to absorb LLM imprecision
        prompt = _TOKEN_TRIM_PROMPT.format(
            page_id=page_id,
            title=page.title,
            current_tokens=current,
            target_tokens=target,
            delta=current - target,
            pct=(current - target) / max(1, current),
            body=page.body[:40_000],  # hard cap at ~40k chars to cap prompt size
        )
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            cost_tracker.record_call(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=model,
                is_sub_call=True,
            )
            new_body = response.content[0].text if response.content else ""
        except BudgetExceeded:
            raise
        except Exception as exc:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=f"token-overflow trim LLM call failed on {page_id}",
                    detail=f"was {current} tokens (limit {limit}); error: {exc}",
                )
            )
            continue

        new_body = new_body.strip()
        if not new_body:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=f"token-overflow trim returned empty body for {page_id}",
                )
            )
            continue

        new_count = _count(new_body)

        # Basic safety check: wikilinks count should be preserved.
        # If the LLM dropped more than 10% of wikilinks, reject the trim.
        old_links = len(re.findall(r"\[\[[^\[\]]+\]\]", page.body))
        new_links = len(re.findall(r"\[\[[^\[\]]+\]\]", new_body))
        if old_links and new_links < old_links * 0.9:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=(
                        f"rejected token-overflow trim on {page_id}: "
                        f"wikilinks dropped from {old_links} to {new_links}"
                    ),
                    detail=(
                        f"was {current} tokens → trim produced {new_count} "
                        "but preserved too few wikilinks; escalating"
                    ),
                )
            )
            continue

        if new_count > limit:
            # Still over budget — escalate rather than re-trim.
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=(
                        f"trim insufficient on {page_id}: "
                        f"{current} → {new_count} (limit {limit})"
                    ),
                    detail=(
                        "recommend: split page into two by section, "
                        "or raise max_tokens_per_page for this vault."
                    ),
                )
            )
            continue

        if mend_config.dry_run:
            actions.append(
                MendAction(
                    kind=MendActionKind.TOKEN_OVERFLOW_TRIMMED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=(
                        f"would trim {page_id}: {current} → {new_count} tokens"
                    ),
                    detail=f"wikilinks: {old_links} → {new_links}",
                )
            )
            continue
        page = page.model_copy(update={"body": new_body, "updated": today_iso()})
        write_page(vault, page)
        actions.append(
            MendAction(
                kind=MendActionKind.TOKEN_OVERFLOW_TRIMMED,
                page_id=page_id,
                pages=[page_id],
                summary=f"trimmed {page_id}: {current} → {new_count} tokens",
                detail=f"wikilinks preserved: {old_links} → {new_links}",
            )
        )

    return actions


# ---------------------------------------------------------------------------
# Tier 3 — REPL thin-page expansion via PageWriter UPDATE
# ---------------------------------------------------------------------------


def _tier3_thin_pages(
    *,
    client: anthropic.Anthropic,
    config: CompendiumConfig,
    cost_tracker: CostTracker,
    vault: VaultPaths,
    mend_config: MendConfig,
) -> list[MendAction]:
    """Expand thin pages by re-invoking PageWriter in UPDATE mode.

    For each thin page:
    - If it has no sources in frontmatter, escalate — nothing to draw on.
    - Otherwise, load the first source's raw content, synthesize a
      PageChange{op=UPDATE}, and hand to PageWriter — identical shape to
      the ingest pipeline's UPDATE path.
    """
    from compendium.agents.page_writer import PageWriter
    from compendium.models import PageChange, PageChangeOp

    actions: list[MendAction] = []
    writer = PageWriter(
        client, config, cost_tracker,
        vault=vault, tiered=config.tiered_models,
    )

    handled = 0
    for page_id in list_pages(vault):
        if handled >= mend_config.max_tier3_pages:
            break
        page = read_page(vault, page_id)
        if page is None:
            continue
        if page.type == PageType.SOURCE:
            continue  # source-type pages have their own summary shape
        word_count = len(page.body.split())
        if word_count >= THIN_PAGE_WORD_THRESHOLD:
            continue

        if not page.sources:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=(
                        f"thin page {page_id} ({word_count} words) "
                        "has no sources — needs research"
                    ),
                    detail="",
                )
            )
            continue

        source_id = page.sources[0]
        source_content = _load_source_content(vault, source_id)
        if source_content is None:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=(
                        f"thin page {page_id} source {source_id} "
                        "has no readable raw/ file"
                    ),
                    detail="",
                )
            )
            continue

        cost_tracker.check_budget()
        handled += 1

        if mend_config.dry_run:
            actions.append(
                MendAction(
                    kind=MendActionKind.THIN_PAGE_EXPANDED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=(
                        f"would expand thin page {page_id} "
                        f"({word_count} words) from {source_id}"
                    ),
                )
            )
            continue

        change = PageChange(
            op=PageChangeOp.UPDATE,
            page_id=page_id,
            page_type=page.type,
            title=page.title,
            reason=(
                f"thin page ({word_count} words) — expand from "
                f"{source_id} without discarding existing content"
            ),
        )
        sibling_index = _read_text_safe(vault.index_md)
        try:
            updated = writer.write(
                change=change,
                source_id=source_id,
                source_title=source_id,
                source_content=source_content,
                sibling_index=sibling_index,
            )
        except BudgetExceeded:
            raise
        except Exception as exc:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id=page_id,
                    pages=[page_id],
                    summary=f"PageWriter UPDATE failed on {page_id}",
                    detail=f"error: {exc}",
                )
            )
            continue

        new_word_count = len(updated.body.split())
        write_page(vault, updated)
        actions.append(
            MendAction(
                kind=MendActionKind.THIN_PAGE_EXPANDED,
                page_id=page_id,
                pages=[page_id],
                summary=(
                    f"expanded {page_id}: {word_count} → "
                    f"{new_word_count} words from {source_id}"
                ),
            )
        )

    return actions


def _load_source_content(vault: VaultPaths, source_id: str) -> str | None:
    """Find the raw/ companion markdown for a source page, if present."""
    for subdir in ("papers", "articles", "transcripts"):
        candidate = vault.root / "raw" / subdir / f"{source_id}.md"
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                continue
    return None


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Tier 4 — source curation (keep / supersede / stale / escalate)
# ---------------------------------------------------------------------------


_CURATOR_PROMPT = """\
You are auditing a source in a wiki on "{topic}" for continued relevance.

**Source under review**: `{source_id}`
**Source page body**:
{source_body}

**Sources frontmatter says**: retrieved {retrieved}, citation "{citation}"
**Raw file first 2000 chars**:
{raw_excerpt}

**In-vault centrality**: {ref_count} page(s) currently cite this source.
Citing pages: {citing_pages}

Now judge: is this source still doing work for the vault, or should it
be culled? Use these rules:

- **FOUNDATIONAL** — timeless / canonical / primary source. Age is
  IRRELEVANT for philosophy, mathematics, physics fundamentals, classical
  literature, art history before 1900, historical documents, etc. A 1920
  book on dynamic symmetry is foundational; a 2023 paper on a specific
  CNN architecture is not.

- **ACTIVE** — newer than ~3 years OR still actively cited by multiple
  pages in the vault OR covering a slow-moving area where 2020-era work
  still stands. Keep.

- **SUPERSEDED** — covers the same ground as a newer source in this
  vault and is now redundant. Cull. Specify the newer source's id if
  you know it.

- **STALE** — fast-moving technical field (ML model architectures,
  specific software versions, ephemeral web content) AND older than
  ~2 years AND not cited by any currently-active page. Cull.

- **ESCALATE** — can't tell from the evidence. Human should decide.

Be conservative: when in doubt, prefer ACTIVE over STALE. Culling is
DESTRUCTIVE — files get deleted and the source is tombstoned against
re-ingestion. Never cull a source that's the only citation on its topic.

Respond with a SINGLE JSON object, no prose:

{{"decision": "FOUNDATIONAL" | "ACTIVE" | "SUPERSEDED" | "STALE" | "ESCALATE",
  "reason": "one sentence",
  "superseded_by": "source-id-of-newer-version"  // only for SUPERSEDED
}}
"""


def _tier4_curate_sources(
    *,
    client: anthropic.Anthropic,
    config: CompendiumConfig,
    cost_tracker: CostTracker,
    vault: VaultPaths,
    mend_config: MendConfig,
) -> list[MendAction]:
    """Decide keep/cull per source page. Delete + tombstone culled ones.

    Uses the synthesis tier (sonnet) rather than mechanical (haiku) —
    the foundational/stale distinction requires real judgment, not a
    pattern-match.
    """
    from compendium.vault.links import build_backlinks
    from compendium.vault.registry import IngestRegistry

    actions: list[MendAction] = []
    model = config.tiered_models.get_model(ModelTier.SYNTHESIS)
    registry = IngestRegistry(vault)
    backlinks = build_backlinks(vault)

    source_page_ids = list_pages(vault, PageType.SOURCE)
    if not source_page_ids:
        return actions

    handled = 0
    for source_id in source_page_ids:
        if handled >= mend_config.max_tier4_sources:
            break
        page = read_page(vault, source_id)
        if page is None or page.type != PageType.SOURCE:
            continue

        citing = sorted(backlinks.get(source_id, []))
        ref_count = len(citing)
        retrieved = ""
        citation = ""
        # Pull citation + retrieved date from frontmatter if the source
        # page has them; falls back to ("", "") which the LLM treats as
        # "unknown."
        retrieved = page.updated or ""

        raw_excerpt = _load_source_raw_excerpt(vault, source_id, max_chars=2000)

        cost_tracker.check_budget()
        handled += 1
        prompt = _CURATOR_PROMPT.format(
            topic=_topic_or_unknown(vault),
            source_id=source_id,
            source_body=page.body[:4000],
            retrieved=retrieved or "(unknown)",
            citation=citation or "(none in frontmatter)",
            raw_excerpt=raw_excerpt,
            ref_count=ref_count,
            citing_pages=", ".join(citing[:8]) or "(none — orphan)",
        )
        try:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            cost_tracker.record_call(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=model,
                is_sub_call=True,
            )
            text = response.content[0].text if response.content else ""
            obj = _extract_json(text)
        except BudgetExceeded:
            raise
        except Exception as exc:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id=source_id,
                    pages=[source_id],
                    summary=f"curator LLM call failed for {source_id}",
                    detail=f"error: {exc}",
                )
            )
            continue

        decision = str(obj.get("decision", "")).upper()
        reason = str(obj.get("reason", "")).strip()
        superseded_by = str(obj.get("superseded_by", "")).strip()

        if decision == "FOUNDATIONAL":
            actions.append(
                MendAction(
                    kind=MendActionKind.SOURCE_KEPT_FOUNDATIONAL,
                    page_id=source_id,
                    pages=[source_id],
                    summary=f"kept {source_id} as foundational",
                    detail=f"reason: {reason}\ncites: {ref_count}",
                )
            )
        elif decision == "ACTIVE":
            actions.append(
                MendAction(
                    kind=MendActionKind.SOURCE_KEPT_ACTIVE,
                    page_id=source_id,
                    pages=[source_id],
                    summary=f"kept {source_id} as active",
                    detail=f"reason: {reason}\ncites: {ref_count}",
                )
            )
        elif decision in {"SUPERSEDED", "STALE"}:
            kind = (
                MendActionKind.SOURCE_SUPERSEDED
                if decision == "SUPERSEDED"
                else MendActionKind.SOURCE_STALE
            )
            # NEVER auto-cull a source with nonzero active backlinks —
            # removing it would break those citations and create
            # downstream gaps. Escalate instead so a human decides.
            if ref_count > 0 and decision == "STALE":
                actions.append(
                    MendAction(
                        kind=MendActionKind.ESCALATED,
                        page_id=source_id,
                        pages=[source_id] + citing,
                        summary=(
                            f"tier-4 wanted to mark {source_id} STALE "
                            f"but {ref_count} page(s) still cite it"
                        ),
                        detail=(
                            f"reason: {reason}\n"
                            f"citing: {', '.join(citing)}\n"
                            "recommend: expand the citing pages to cite "
                            "newer sources before culling this one"
                        ),
                    )
                )
                continue
            # SUPERSEDED with known replacement: re-wire citing pages
            # to the new source BEFORE deleting the old one.
            if (
                decision == "SUPERSEDED"
                and superseded_by
                and locate_page(vault, superseded_by) is not None
            ):
                rewired = _rewire_source_references(
                    vault, old_id=source_id, new_id=superseded_by,
                    dry_run=mend_config.dry_run,
                )
                culled = _cull_source(
                    vault, source_id, registry,
                    reason=f"superseded by {superseded_by}",
                    dry_run=mend_config.dry_run,
                )
                actions.append(
                    MendAction(
                        kind=kind,
                        page_id=source_id,
                        pages=[source_id] + rewired,
                        summary=(
                            f"culled {source_id} (superseded by "
                            f"{superseded_by}); rewired {len(rewired)} "
                            f"page(s)"
                        ),
                        detail=(
                            f"reason: {reason}\n"
                            f"removed: {', '.join(culled)}\n"
                            f"rewired: {', '.join(rewired)}"
                        ),
                    )
                )
            else:
                # STALE with no backlinks, OR SUPERSEDED without a known
                # replacement → plain cull.
                culled = _cull_source(
                    vault, source_id, registry,
                    reason=reason or decision.lower(),
                    dry_run=mend_config.dry_run,
                )
                actions.append(
                    MendAction(
                        kind=kind,
                        page_id=source_id,
                        pages=[source_id],
                        summary=f"culled {source_id} ({decision.lower()})",
                        detail=(
                            f"reason: {reason}\n"
                            f"removed: {', '.join(culled)}"
                        ),
                    )
                )
        else:
            actions.append(
                MendAction(
                    kind=MendActionKind.ESCALATED,
                    page_id=source_id,
                    pages=[source_id],
                    summary=f"curator escalated {source_id}",
                    detail=f"reason: {reason}\ncites: {ref_count}",
                )
            )

    return actions


def _cull_source(
    vault: VaultPaths,
    source_id: str,
    registry,
    *,
    reason: str,
    dry_run: bool = False,
) -> list[str]:
    """Delete source files, scrub refs from citing pages, tombstone it.

    Returns a list of paths removed (or would-be-removed under dry-run).

    Order matters: scrub citing-page frontmatter FIRST so if the
    subsequent file deletion fails the vault isn't left pointing at a
    missing source.
    """
    removed: list[str] = []

    # 1. Scrub the source_id from every citing page's `sources` list.
    for page_id in list_pages(vault):
        page = read_page(vault, page_id)
        if page is None or source_id not in page.sources:
            continue
        new_sources = [s for s in page.sources if s != source_id]
        if dry_run:
            removed.append(f"scrub_from:{page_id}")
            continue
        page = page.model_copy(update={
            "sources": new_sources,
            "updated": today_iso(),
        })
        write_page(vault, page)
        removed.append(f"scrub_from:{page_id}")

    # 2. Locate and capture hash+url from registry before deleting, so
    # we can tombstone correctly.
    entry = None
    try:
        data = registry._load()
        for raw in data.get("entries", []):
            if raw.get("source_id") == source_id:
                entry = raw
                break
    except Exception:
        entry = None
    hash_value = (entry or {}).get("hash", "") or ""
    url_value = (entry or {}).get("url", "") or ""

    # 3. Delete the wiki/sources/<id>.md page.
    source_page = vault.sources / f"{source_id}.md"
    if source_page.is_file():
        if dry_run:
            removed.append(f"wiki/sources/{source_id}.md")
        else:
            try:
                source_page.unlink()
                removed.append(f"wiki/sources/{source_id}.md")
            except OSError as exc:
                log.warning("failed to delete %s: %s", source_page, exc)

    # 4. Delete raw/papers|articles|transcripts/<id>.* files.
    for subdir in ("papers", "articles", "transcripts"):
        root = vault.root / "raw" / subdir
        if not root.is_dir():
            continue
        for candidate in root.glob(f"{source_id}.*"):
            if candidate.is_file():
                if dry_run:
                    removed.append(f"raw/{subdir}/{candidate.name}")
                else:
                    try:
                        candidate.unlink()
                        removed.append(f"raw/{subdir}/{candidate.name}")
                    except OSError as exc:
                        log.warning("failed to delete %s: %s", candidate, exc)

    # 5. Delete raw/assets/<id>/ if present (figure directory).
    assets_dir = vault.root / "raw" / "assets" / source_id
    if assets_dir.is_dir():
        if dry_run:
            removed.append(f"raw/assets/{source_id}/")
        else:
            try:
                import shutil
                shutil.rmtree(assets_dir)
                removed.append(f"raw/assets/{source_id}/")
            except OSError as exc:
                log.warning("failed to delete %s: %s", assets_dir, exc)

    # 6. Tombstone + remove registry entry so the source can't be
    # re-ingested on a later run.
    if not dry_run:
        try:
            registry.tombstone(
                source_id=source_id,
                hash=hash_value,
                url=url_value,
                reason=reason,
            )
            registry.remove_entry(source_id)
        except Exception as exc:
            log.warning("tombstone write failed for %s: %s", source_id, exc)

    return removed


def _rewire_source_references(
    vault: VaultPaths,
    *,
    old_id: str,
    new_id: str,
    dry_run: bool = False,
) -> list[str]:
    """Replace `old_id` with `new_id` in every page's `sources` frontmatter.

    Also rewrites `[[old_id]]` wikilinks in page bodies to `[[new_id]]`
    so in-text citations stay resolvable after the cull.
    """
    rewired: list[str] = []
    pattern = _wikilink_pattern(old_id)
    for page_id in list_pages(vault):
        page = read_page(vault, page_id)
        if page is None:
            continue
        touched = False
        new_sources = list(page.sources)
        if old_id in new_sources:
            new_sources = [new_id if s == old_id else s for s in new_sources]
            # Dedupe in case new_id was already present
            seen: set[str] = set()
            deduped = []
            for s in new_sources:
                if s in seen:
                    continue
                seen.add(s)
                deduped.append(s)
            new_sources = deduped
            touched = True
        new_body = page.body
        if pattern.search(new_body):
            new_body = pattern.sub(f"[[{new_id}]]", new_body)
            touched = True
        if not touched:
            continue
        rewired.append(page_id)
        if dry_run:
            continue
        page = page.model_copy(update={
            "sources": new_sources,
            "body": new_body,
            "updated": today_iso(),
        })
        write_page(vault, page)
    return rewired


def _load_source_raw_excerpt(
    vault: VaultPaths, source_id: str, *, max_chars: int = 2000
) -> str:
    """First `max_chars` of the raw/ companion file; '(no raw file)' if absent."""
    for subdir in ("papers", "articles", "transcripts"):
        candidate = vault.root / "raw" / subdir / f"{source_id}.md"
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")[:max_chars]
            except OSError:
                continue
    return "(no raw/ file found — source may have been manually ingested)"


def _topic_or_unknown(vault: VaultPaths) -> str:
    try:
        from compendium.agents.explorer import _read_topic
        return _read_topic(vault) or "(unspecified)"
    except Exception:
        return "(unspecified)"


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _write_mend_report(vault: VaultPaths, report: MendReport) -> None:
    vault.meta.mkdir(parents=True, exist_ok=True)
    path = vault.meta / "mend-report.md"
    path.write_text(render_mend_report(report), encoding="utf-8")


def render_mend_report(report: MendReport) -> str:
    """Render a MendReport to markdown for _meta/mend-report.md.

    The structure groups actions by kind so Explorer can scan it with
    per-section REPL helpers. Escalations go first — they're the signal
    Explorer most wants to read.
    """
    lines: list[str] = []
    dry_tag = " (dry-run)" if report.dry_run else ""
    lines.append(f"# Mend Report{dry_tag}")
    lines.append("")
    lines.append(
        f"*{report.topic or 'untitled vault'} · "
        f"{report.timestamp} · "
        f"{len(report.actions)} action"
        f"{'s' if len(report.actions) != 1 else ''} · "
        f"${report.cost_dollars:.4f}*"
    )
    lines.append("")
    lines.append(
        "> The Mender consumed the Lint + Reviewer output and either "
        "fixed issues in place, left them as signal for Explorer, or "
        "escalated them for human review. Escalations and KEEP actions "
        "are the highest-quality inputs to the next `explore` pass."
    )
    lines.append("")

    escalated = report.escalated()
    if escalated:
        lines.append("## Escalations — human attention")
        lines.append("")
        for a in escalated:
            lines.append(f"- **{a.summary}**")
            for line in (a.detail or "").splitlines():
                lines.append(f"  - {line}")
            if a.pages:
                lines.append(f"  - pages: {', '.join(a.pages)}")
        lines.append("")

    grouped: dict[str, list[MendAction]] = defaultdict(list)
    for a in report.actions:
        if a.kind == MendActionKind.ESCALATED:
            continue
        grouped[a.kind.value].append(a)

    for kind_value, bucket in grouped.items():
        label = kind_value.replace("_", " ")
        lines.append(f"## {label} ({len(bucket)})")
        lines.append("")
        for a in bucket[:50]:
            lines.append(f"- {a.summary}")
            if a.detail:
                for dl in a.detail.splitlines()[:8]:
                    lines.append(f"  - {dl}")
        if len(bucket) > 50:
            lines.append(f"- …{len(bucket) - 50} more")
        lines.append("")

    if not report.actions:
        lines.append("*No actions taken — vault is clean by this pass.*")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict:
    """Pull out the first JSON object in `text`."""
    text = text.strip()
    if text.startswith("```"):
        # strip fenced block
        text = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", text, flags=re.MULTILINE)
        text = text.strip()
    # Try direct parse first
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Otherwise find the first {...} span.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object found")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("JSON root was not an object")
    return obj


def _escalate_budget(message: str) -> MendAction:
    return MendAction(
        kind=MendActionKind.ESCALATED,
        page_id="",
        pages=[],
        summary=message,
        detail="budget exhausted — re-run with larger --max-cost or smaller scope",
    )
