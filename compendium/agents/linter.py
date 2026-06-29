"""Linter — health-check for a vault.

Runs three passes:

1. **Orphans** (mechanical)   — pages with zero inbound wikilinks
   (excluding source pages). Written to `_meta/orphans.md`.
2. **Broken wikilinks** (mechanical) — wikilink targets that don't
   exist. Also surfaced to Explorer as gap signal. Written to
   `_meta/broken-links.md`.
3. **Contradictions** (LLM)   — pairs of pages making disagreeing
   claims. One batched strategic-tier call across all pages.
   Written to `_meta/contradictions.md`.

The IngestOrchestrator's Reviewer catches *within-ingest* issues.
Lint catches *cross-ingest* drift — stuff that creeps in as the
corpus grows and that no single ingest can see in isolation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import anthropic

from compendium.config import CompendiumConfig
from compendium.guardrails.cost_tracker import CostTracker
from compendium.models import (
    BrokenLinkGroup,
    Contradiction,
    LintReport,
    ModelTier,
    PageType,
    VerificationResult,
)
from compendium.repl.functions import make_web_search_fn
from compendium.sources.verifier import SourceVerifier
from compendium.vault import (
    ContradictionLedger,
    ReconcileResult,
    VaultPaths,
    append_history_entry,
    find_broken_wikilinks,
    find_orphans,
    list_pages,
    read_page,
    short_id,
    timestamp_iso,
    today_iso,
)
from compendium.vault.contradiction_ledger import (
    STATUS_BY_DESIGN,
    STATUS_RESOLVED,
)


_BIBLIOGRAPHY_HEADING_RE = re.compile(
    r"^##+\s+(?:Sources?|References?|Bibliography|Works\s+Cited|Citations?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]\s+|\d+\.\s+)(.+)$")


CONTRADICTION_SYSTEM_PROMPT = """\
You are a Contradiction Scanner for a vault wiki on **{topic}**.

I will give you the full body of every wiki page (except source
summaries). Find pairs or clusters of pages that make DISAGREEING
factual claims. Be strict: a contradiction is a case where both
claims can't be true at once — not merely different emphasis or
incompatible stylistic choices.

Watch especially for:
- Direction-of-influence claims between historical figures
  (A influenced B vs. B predated A)
- Disagreements on dates, authorship, or attribution
- Mutually exclusive interpretive claims where both pages assert
  theirs as settled fact
- Numerical / taxonomic mismatches (categories on one page
  contradicting categories on another)

Respond with a SINGLE JSON object, no prose or code fences:

{{
  "contradictions": [
    {{
      "pages": ["page-id-1", "page-id-2"],
      "summary": "one-line: what disagrees",
      "details": "direct quotes or close paraphrases of the
                  disagreeing claims, with (page-id) labels",
      "suggested_resolution": "how to resolve (revise one, flag as
                              open question, cite authority, etc.)"
    }}
  ]
}}

An empty list is a valid answer. Do not fabricate contradictions.
"""


class Linter:
    """Performs orphan / broken-link / contradiction checks."""

    def __init__(
        self,
        client: anthropic.Anthropic,
        config: CompendiumConfig,
        cost_tracker: CostTracker,
        vault: VaultPaths,
    ):
        self.client = client
        self.config = config
        self.cost_tracker = cost_tracker
        self.vault = vault
        self.model = config.tiered_models.get_model(ModelTier.STRATEGIC)

    # ------------------------------------------------------------------

    def lint(self, ground: bool = False) -> LintReport:
        topic = _read_topic(self.vault)
        page_ids = list_pages(self.vault)

        orphans = find_orphans(self.vault)
        broken = _grouped_broken_links(self.vault)
        scanned = self._scan_contradictions()
        citation_stats = self._verify_citations_pass(topic)

        # Reconcile the fresh scan against the durable ledger: silence
        # by-design tensions, flag regressions, remember resolution state.
        ledger = ContradictionLedger(self.vault)
        recon = ledger.reconcile(scanned, today=today_iso())

        self.vault.meta.mkdir(parents=True, exist_ok=True)
        self.vault.orphans_md.write_text(
            _render_orphans_md(orphans, topic), encoding="utf-8"
        )
        (self.vault.meta / "broken-links.md").write_text(
            _render_broken_links_md(broken, topic), encoding="utf-8"
        )
        self.vault.contradictions_md.write_text(
            _render_contradictions_md(recon, topic), encoding="utf-8"
        )

        # Optional claim-grounding pass (mechanical, no LLM cost).
        g_ran = g_grounded = g_loose = g_nf = g_unver = 0
        if ground:
            try:
                from compendium.agents.grounding import (
                    ground_vault,
                    render_grounding_md,
                )

                greport = ground_vault(self.vault)
                self.vault.grounding_md.write_text(
                    render_grounding_md(greport), encoding="utf-8"
                )
                g_ran = 1
                g_grounded = greport.grounded
                g_loose = greport.loose
                g_nf = greport.not_found
                g_unver = greport.unverifiable
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "grounding pass failed: %s", exc
                )

        cost = self.cost_tracker.get_summary()["estimated_cost_usd"]
        report = LintReport(
            timestamp=timestamp_iso(),
            topic=topic,
            orphan_pages=orphans,
            broken_links=broken,
            contradictions=recon.needs_attention,
            pages_inspected=len(page_ids),
            citations_checked=citation_stats["total"],
            citations_verified_high=citation_stats["high"],
            citations_verified_medium=citation_stats["medium"],
            citations_unverified=citation_stats["unverified"],
            citation_verification_skipped=citation_stats["skipped"],
            contradictions_new=len(recon.new),
            contradictions_regressed=len(recon.regressions),
            contradictions_by_design=len(recon.by_design),
            grounding_ran=bool(g_ran),
            grounded=g_grounded,
            grounding_loose=g_loose,
            grounding_not_found=g_nf,
            grounding_unverifiable=g_unver,
            cost_dollars=cost,
        )
        self._append_history(report, broken)
        return report

    def _append_history(
        self, report: LintReport, broken: list[BrokenLinkGroup]
    ) -> None:
        """Persist a metrics snapshot for the `health` trend command."""
        entry = {
            "timestamp": report.timestamp,
            "pages": report.pages_inspected,
            "orphans": len(report.orphan_pages),
            "missing_targets": len(broken),
            "broken_refs": sum(g.ref_count for g in broken),
            "contradictions_open": len(report.contradictions),
            "contradictions_new": report.contradictions_new,
            "contradictions_regressed": report.contradictions_regressed,
            "contradictions_by_design": report.contradictions_by_design,
            "citations_high": report.citations_verified_high,
            "citations_medium": report.citations_verified_medium,
            "citations_unverified": report.citations_unverified,
            "cost": round(report.cost_dollars, 4),
        }
        if report.grounding_ran:
            entry["grounded"] = report.grounded
            entry["grounding_loose"] = report.grounding_loose
            entry["grounding_not_found"] = report.grounding_not_found
            entry["grounding_unverifiable"] = report.grounding_unverifiable
        append_history_entry(self.vault, entry)

    # ------------------------------------------------------------ scan

    # ---------------------------------------------------- citation verify

    def _verify_citations_pass(self, topic: str) -> dict:
        """Run SourceVerifier across every bibliographic entry found in raw/.

        Skipped gracefully when no search provider is configured.
        """
        stats = {
            "total": 0,
            "high": 0,
            "medium": 0,
            "unverified": 0,
            "skipped": False,
        }
        provider = self.config.search_provider
        jina_key = getattr(self.config, "jina_api_key", "")
        # Citation verification runs default-on at lint: if no explicit
        # search provider is set but a Jina key is configured (user
        # already has one for fetch_url), Jina Search is used as a
        # free-tier fallback. Skip only when NEITHER is available.
        has_any_backend = (
            (provider and provider != "none")
            or bool(jina_key)
        )
        if not has_any_backend:
            stats["skipped"] = True
            self._write_citations_md(
                topic=topic,
                per_source={},
                skipped_reason=(
                    "No search provider configured. Set COMPENDIUM_SEARCH_PROVIDER "
                    "and COMPENDIUM_SEARCH_API_KEY (or COMPENDIUM_JINA_API_KEY) "
                    "to enable citation verification."
                ),
            )
            return stats

        by_source = _extract_citations_from_raw(self.vault)
        if not by_source:
            self._write_citations_md(topic=topic, per_source={})
            return stats

        search_fn = make_web_search_fn(
            provider or "none",
            self.config.search_api_key,
            jina_api_key=jina_key,
        )
        verifier = SourceVerifier(search_fn=search_fn)

        per_source: dict[str, list[VerificationResult]] = {}
        for source_id, citations in by_source.items():
            results = verifier.verify_citations(citations)
            per_source[source_id] = results
            for r in results:
                stats["total"] += 1
                if r.verified and r.confidence == "high":
                    stats["high"] += 1
                elif r.verified and r.confidence == "medium":
                    stats["medium"] += 1
                else:
                    stats["unverified"] += 1

        self._write_citations_md(topic=topic, per_source=per_source)
        return stats

    def _write_citations_md(
        self,
        *,
        topic: str,
        per_source: dict[str, list[VerificationResult]],
        skipped_reason: str | None = None,
    ) -> None:
        path = self.vault.meta / "unverified-citations.md"
        path.write_text(
            _render_citations_md(per_source, topic, skipped_reason),
            encoding="utf-8",
        )

    # ------------------------------------------------------------ scan

    # Soft ceiling on bytes passed to one contradiction-scan call.
    # Sonnet handles ~200K tokens; keep the batch well under that so
    # the prompt + response fits with budget for reasoning.
    _CONTRADICTION_BATCH_BYTES = 120_000

    def _scan_contradictions(self) -> list[Contradiction]:
        """Scan for cross-page contradictions, chunked for scale.

        Concatenating every page into one LLM call breaks around 100+
        pages (exceeds context window). Instead we:

        1. Gather all non-source page bodies with their metadata.
        2. Group them by tag overlap — pages that share a tag are more
           likely to contradict each other; unrelated pages rarely do.
        3. Split each tag-cluster into token-bounded batches.
        4. Run a per-batch scan, plus one "central-pages cross-scan"
           across the most-referenced pages (since central pages are
           the ones whose contradictions matter most and are most
           likely to span clusters).
        5. Merge and de-duplicate the contradictions found.
        """
        from compendium.vault.links import build_backlinks

        pages_with_meta = []
        for page_id in list_pages(self.vault):
            page = read_page(self.vault, page_id)
            if page is None or page.type == PageType.SOURCE:
                continue
            block = (
                f"---\npage_id: {page.id}\ntitle: {page.title}\n"
                f"type: {page.type.value}\n\n{page.body}\n"
            )
            pages_with_meta.append((page, block))
        if len(pages_with_meta) < 2:
            return []

        topic = _read_topic(self.vault)

        # Compact path: everything fits in one call — do it the old way.
        total_bytes = sum(len(b) for _, b in pages_with_meta)
        if total_bytes <= self._CONTRADICTION_BATCH_BYTES:
            return self._scan_batch(
                [b for _, b in pages_with_meta], topic=topic
            )

        # Scaled path: cluster + chunk + cross-scan
        clusters = _cluster_by_tags(pages_with_meta)
        batches: list[list[str]] = []
        for cluster in clusters:
            batches.extend(
                _split_to_size(
                    [b for _, b in cluster],
                    byte_limit=self._CONTRADICTION_BATCH_BYTES,
                )
            )

        # Central-pages cross-scan: top-10 most-referenced pages in one batch
        backlinks = build_backlinks(self.vault)
        centrality = sorted(
            pages_with_meta,
            key=lambda pm: len(backlinks.get(pm[0].id, [])),
            reverse=True,
        )
        central_blocks = [b for _, b in centrality[:10]]
        if central_blocks and len(central_blocks) >= 2:
            batches.append(central_blocks)

        valid_batches = [b for b in batches if b and len(b) >= 2]
        if not valid_batches:
            return []

        # For multi-batch scans, the Message Batches API halves the cost
        # and stacks with the cached system prompt — it's the same N
        # independent Opus calls either way. For a single batch there's
        # no point paying the batch-API polling overhead.
        if len(valid_batches) >= 2:
            try:
                return self._scan_batches_via_api(valid_batches, topic=topic)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "batch contradiction scan failed (%s); falling back "
                    "to sequential scan", exc,
                )

        all_contradictions: list[Contradiction] = []
        seen_sigs: set[tuple] = set()
        for batch in valid_batches:
            try:
                batch_results = self._scan_batch(batch, topic=topic)
            except Exception as exc:
                all_contradictions.append(
                    Contradiction(
                        pages=[],
                        summary=f"contradiction batch failed: {exc}",
                        details="",
                    )
                )
                continue
            for c in batch_results:
                sig = (tuple(sorted(c.pages)), c.summary.lower().strip())
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)
                all_contradictions.append(c)
        return all_contradictions

    def _scan_batches_via_api(
        self, batches: list[list[str]], *, topic: str
    ) -> list[Contradiction]:
        """Submit N contradiction-scan chunks via the Message Batches API.

        Each chunk becomes one batch request. They share the same system
        prompt — which we mark with `cache_control` so a cache-read on
        subsequent chunks applies on top of the batch 50% discount
        (stacks to ~95% off on the shared prefix).
        """
        from compendium.guardrails.batch import submit_batch

        system_text = CONTRADICTION_SYSTEM_PROMPT.format(
            topic=topic or "(unspecified topic)"
        )
        requests: list[dict] = []
        for idx, batch in enumerate(batches):
            user_content = "\n".join(batch)
            requests.append({
                "custom_id": f"scan-{idx:03d}",
                "params": {
                    "model": self.model,
                    "max_tokens": 3072,
                    "system": [
                        {
                            "type": "text",
                            "text": system_text,
                            "cache_control": {
                                "type": "ephemeral", "ttl": "1h",
                            },
                        }
                    ],
                    "messages": [
                        {"role": "user", "content": user_content}
                    ],
                },
            })

        self.cost_tracker.check_budget()
        results = submit_batch(
            client=self.client,
            cost_tracker=self.cost_tracker,
            model=self.model,
            requests=requests,
            is_sub_call=False,
        )

        all_contradictions: list[Contradiction] = []
        seen_sigs: set[tuple] = set()
        for cid in sorted(results.keys()):
            res = results[cid]
            if res["type"] != "succeeded":
                all_contradictions.append(
                    Contradiction(
                        pages=[],
                        summary=(
                            f"contradiction scan {cid} "
                            f"{res['type']}: {res.get('error', '')}"
                        ),
                        details="",
                    )
                )
                continue
            text = res.get("text") or ""
            for c in _parse_contradictions(text):
                sig = (tuple(sorted(c.pages)), c.summary.lower().strip())
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)
                all_contradictions.append(c)
        return all_contradictions

    def _scan_batch(
        self, bodies: list[str], *, topic: str
    ) -> list[Contradiction]:
        """One contradiction-scan LLM call against a batch of page bodies."""
        user_content = "\n".join(bodies)
        try:
            self.cost_tracker.check_budget()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=3072,
                system=CONTRADICTION_SYSTEM_PROMPT.format(
                    topic=topic or "(unspecified topic)"
                ),
                messages=[{"role": "user", "content": user_content}],
            )
            self.cost_tracker.record_call(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=self.model,
                is_sub_call=False,
            )
        except Exception as exc:
            # Best-effort: a failed contradiction scan yields no issues.
            return [
                Contradiction(
                    pages=[],
                    summary=f"contradiction scan failed: {exc}",
                    details="",
                )
            ]

        text = response.content[0].text if response.content else ""
        return list(_parse_contradictions(text))


# ---------------------------------------------------------- mechanical


def _cluster_by_tags(
    pages_with_meta: list,
) -> list[list]:
    """Group pages by shared tags. Pages with no tag overlap land in
    their own singleton cluster; pages that share 2+ tags cluster.

    A cluster is a list of (Page, body_block) tuples. Cross-cluster
    contradictions tend to be rare (pages that share no tags aren't
    on the same topic), so scanning within clusters catches most
    real contradictions at a fraction of the context cost.

    Fallback: if a page has no tags, it gets grouped with pages of
    the same `type` so we still get some thematic clustering.
    """
    if not pages_with_meta:
        return []
    # Build tag -> pages map
    tag_to_items: dict[str, list] = {}
    no_tag_by_type: dict[str, list] = {}
    page_to_item: dict[str, tuple] = {}
    for page, block in pages_with_meta:
        page_to_item[page.id] = (page, block)
        if page.tags:
            for t in page.tags:
                tag_to_items.setdefault(t, []).append(page.id)
        else:
            no_tag_by_type.setdefault(page.type.value, []).append(page.id)

    # Union-find over page IDs, linking those that share a tag
    parent: dict[str, str] = {pid: pid for pid in page_to_item}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for ids in tag_to_items.values():
        if len(ids) < 2:
            continue
        first = ids[0]
        for other in ids[1:]:
            union(first, other)
    for ids in no_tag_by_type.values():
        if len(ids) < 2:
            continue
        first = ids[0]
        for other in ids[1:]:
            union(first, other)

    # Gather by root
    clusters_by_root: dict[str, list] = {}
    for pid in parent:
        root = find(pid)
        clusters_by_root.setdefault(root, []).append(page_to_item[pid])

    return list(clusters_by_root.values())


def _split_to_size(
    blocks: list[str], *, byte_limit: int
) -> list[list[str]]:
    """Greedy packing of page-body blocks into batches under `byte_limit`.

    Each batch is a list of body-text blocks whose total length fits.
    If a single block exceeds the limit on its own, it gets its own
    singleton batch (the scan will still run but the user's page is
    unusually large — flagging it via review issues is downstream's
    problem).
    """
    out: list[list[str]] = []
    current: list[str] = []
    running = 0
    for b in blocks:
        bl = len(b)
        if current and running + bl > byte_limit:
            out.append(current)
            current = [b]
            running = bl
        else:
            current.append(b)
            running += bl
    if current:
        out.append(current)
    return out


def _extract_citations_from_raw(vault: VaultPaths) -> dict[str, list[str]]:
    """Walk raw/ source files and extract bibliographic entries.

    Looks for markdown headings like `## Sources`, `## References`,
    `## Bibliography`, or `## Works Cited`, then parses bullet/numbered
    list items following them (until the next heading).

    Returns a mapping from raw filename stem → list of citation strings.
    Filename stems match the `source_id` of the corresponding wiki/sources/
    page (both are derived from the same slug).
    """
    out: dict[str, list[str]] = {}
    raw_dirs = [
        vault.raw_articles,
        vault.raw_papers,
        vault.raw_transcripts,
    ]
    for raw_dir in raw_dirs:
        if not raw_dir.is_dir():
            continue
        for path in sorted(raw_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in (".md", ".txt"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cites = _parse_bibliography(text)
            if cites:
                out[path.stem] = cites
    return out


def _parse_bibliography(text: str) -> list[str]:
    """Extract list items from the first bibliography-like section in a file."""
    heading = _BIBLIOGRAPHY_HEADING_RE.search(text)
    if not heading:
        return []
    after = text[heading.end() :]
    next_h = _NEXT_HEADING_RE.search(after)
    if next_h:
        after = after[: next_h.start()]
    cites: list[str] = []
    for line in after.splitlines():
        stripped = line.strip()
        m = _LIST_ITEM_RE.match(stripped)
        if not m:
            continue
        cite = m.group(1).strip()
        if len(cite) < 10:
            continue
        cites.append(cite)
    return cites


def _grouped_broken_links(vault: VaultPaths) -> list[BrokenLinkGroup]:
    raw = find_broken_wikilinks(vault)
    by_target: dict[str, list[str]] = {}
    for referrer, target in raw:
        by_target.setdefault(target, []).append(referrer)
    return sorted(
        (
            BrokenLinkGroup(
                target=target,
                ref_count=len(refs),
                referrers=sorted(set(refs)),
            )
            for target, refs in by_target.items()
        ),
        key=lambda g: (-g.ref_count, g.target),
    )


# -------------------------------------------------------------- parse


def _parse_contradictions(text: str):
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return
    try:
        data = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return
    for raw in data.get("contradictions", []) or []:
        pages = [str(p) for p in (raw.get("pages") or [])]
        if len(pages) < 2:
            continue
        yield Contradiction(
            pages=pages,
            summary=(raw.get("summary") or "").strip(),
            details=(raw.get("details") or "").strip(),
            suggested_resolution=(raw.get("suggested_resolution") or "").strip(),
        )


# ------------------------------------------------------------- render


def _render_orphans_md(orphans: list[str], topic: str) -> str:
    lines = ["# Orphan Pages", ""]
    header = f"updated {today_iso()} · {len(orphans)} orphan{'s' if len(orphans) != 1 else ''}"
    if topic:
        header = f"{topic} · " + header
    lines.append(f"*{header}*")
    lines.append("")
    lines.append(
        "> Pages with zero inbound wikilinks (excluding source summaries). "
        "An orphan is either a page that should be linked to from somewhere "
        "else in the corpus — or a page that doesn't belong."
    )
    lines.append("")
    if not orphans:
        lines.append("_No orphans detected._")
    else:
        for page_id in orphans:
            lines.append(f"- [[{page_id}]]")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_broken_links_md(groups: list[BrokenLinkGroup], topic: str) -> str:
    lines = ["# Broken Wikilinks", ""]
    header = (
        f"updated {today_iso()} · {len(groups)} missing target"
        + ("s" if len(groups) != 1 else "")
    )
    if topic:
        header = f"{topic} · " + header
    lines.append(f"*{header}*")
    lines.append("")
    lines.append(
        "> Wikilinks pointing at pages that don't exist. Each row shows "
        "the missing target and how many pages reference it."
    )
    lines.append("")
    if not groups:
        lines.append("_No broken wikilinks._")
    else:
        for g in groups:
            refs = ", ".join(f"`{r}`" for r in g.referrers)
            lines.append(f"- **{g.target}** ({g.ref_count}) — {refs}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_contradictions_md(recon: ReconcileResult, topic: str) -> str:
    needs = recon.needs_attention
    lines = ["# Contradictions", ""]
    header = (
        f"updated {today_iso()} · {len(needs)} need"
        f"{'s' if len(needs) == 1 else ''} attention"
    )
    if recon.by_design:
        header += f" · {len(recon.by_design)} by-design (silenced)"
    if topic:
        header = f"{topic} · " + header
    lines.append(f"*{header}*")
    lines.append("")
    lines.append(
        "> Pairs or clusters of pages making disagreeing factual claims. "
        "Each carries a stable id (e.g. `c-1a2b3c4d`). Resolve by editing a "
        "page (the cluster then drops out on the next scan), or classify it:\n"
        ">\n"
        "> - `python cli.py contradiction --vault . --mark c-XXXX --status by-design --note \"...\"` "
        "— a genuine, preserved disagreement in the sources (silenced from the count).\n"
        "> - `python cli.py contradiction --vault . --mark c-XXXX --status resolved --note \"...\"` "
        "— settled; a later reappearance is flagged as a **regression**."
    )
    lines.append("")

    errors = recon.errors
    if errors:
        for c in errors:
            if c.summary:
                lines.append(f"_Note: {c.summary}_")
        lines.append("")

    if not needs:
        lines.append("_No contradictions need attention._")
        lines.append("")
    else:
        badge = {}
        for c in recon.new:
            badge[id(c)] = "🆕 NEW"
        for c in recon.regressions:
            badge[id(c)] = "⟳ REGRESSION (was resolved)"
        for i, c in enumerate(needs, start=1):
            sid = short_id("||".join(sorted(c.pages)))
            pages = " · ".join(f"[[{p}]]" for p in c.pages)
            tag = badge.get(id(c), "")
            heading = f"### {i}. `{sid}` {c.summary or '(no summary)'}"
            if tag:
                heading += f"  — {tag}"
            lines.append(heading)
            lines.append(f"- **pages**: {pages}")
            if c.details:
                lines.append(f"- **details**: {c.details}")
            if c.suggested_resolution:
                lines.append(f"- **resolution**: {c.suggested_resolution}")
            lines.append("")

    if recon.by_design:
        lines.append("---")
        lines.append("")
        lines.append("## Preserved tensions (by-design)")
        lines.append("")
        lines.append(
            "> Genuine disagreements in the sources, kept on the record and "
            "excluded from the needs-attention count."
        )
        lines.append("")
        for c in recon.by_design:
            sid = short_id("||".join(sorted(c.pages)))
            entry = recon.entries_by_key.get("||".join(sorted(c.pages)))
            note = f" — _{entry.resolution_note}_" if entry and entry.resolution_note else ""
            pages = " · ".join(f"[[{p}]]" for p in c.pages)
            lines.append(f"- `{sid}` {c.summary or '(no summary)'}{note}")
            lines.append(f"    - {pages}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_citations_md(
    per_source: dict[str, list[VerificationResult]],
    topic: str,
    skipped_reason: str | None,
) -> str:
    lines = ["# Citation Verification", ""]
    total = sum(len(v) for v in per_source.values())
    header = f"updated {today_iso()} · {total} citation{'s' if total != 1 else ''} checked"
    if topic:
        header = f"{topic} · " + header
    lines.append(f"*{header}*")
    lines.append("")

    if skipped_reason:
        lines.append(
            "> **Skipped.** " + skipped_reason
        )
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    if not per_source:
        lines.append(
            "> No bibliographic sections (`## Sources`, `## References`, "
            "`## Bibliography`) found in any `raw/` file. Citations that "
            "appear only inline are not yet verified."
        )
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    lines.append(
        "> Citations extracted from `## Sources` sections in `raw/` files, "
        "verified via multi-strategy web search with word-overlap scoring. "
        "Confidence: **high** (title strongly matches a web result), "
        "**medium** (author found OR partial title match), "
        "**low** (no strong match — treat as suspect)."
    )
    lines.append("")

    for source_id, results in sorted(per_source.items()):
        highs = [r for r in results if r.verified and r.confidence == "high"]
        mediums = [r for r in results if r.verified and r.confidence == "medium"]
        lows = [r for r in results if not r.verified or r.confidence == "low"]
        lines.append(f"## `{source_id}`")
        lines.append(
            f"- {len(highs)} high · {len(mediums)} medium · {len(lows)} unverified"
        )
        lines.append("")
        if lows:
            lines.append("**Unverified (low confidence):**")
            for r in lows:
                lines.append(f"- {r.citation}")
                if r.note:
                    lines.append(f"    - _note: {r.note}_")
                if r.matching_url:
                    lines.append(f"    - closest match: {r.matching_url}")
            lines.append("")
        if mediums:
            lines.append("**Medium confidence:**")
            for r in mediums:
                url = f" → {r.matching_url}" if r.matching_url else ""
                lines.append(f"- {r.citation}{url}")
            lines.append("")
        if highs:
            lines.append("<details><summary>Verified (high confidence)</summary>")
            lines.append("")
            for r in highs:
                url = f" → {r.matching_url}" if r.matching_url else ""
                lines.append(f"- {r.citation}{url}")
            lines.append("")
            lines.append("</details>")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ----- helper ---------------------------------------------------------------


def _read_topic(vault: VaultPaths) -> str:
    if not vault.claude_md.exists():
        return ""
    text = vault.claude_md.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("# Vault Schema"):
            _, _, after = line.partition("—")
            return after.strip()
    return ""
