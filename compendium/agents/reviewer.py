"""Reviewer — post-write sanity check on newly-written pages.

Runs after PageWriter. Batch-reviews every page written in an ingest
with one LLM call (cheap, mechanical tier by default) plus local
mechanical checks (token count, missing source citations).

Issues are advisory. IngestOrchestrator records them in the IngestReport
and surfaces high-severity issues to the user, but doesn't block the
ingest.
"""

from __future__ import annotations

import json

import anthropic
import tiktoken

from compendium.config import CompendiumConfig
from compendium.guardrails.cost_tracker import CostTracker
from compendium.models import (
    ModelTier,
    Page,
    PageReviewResult,
    PageType,
    ReviewIssue,
    ReviewSeverity,
    TieredModelConfig,
)


_ENCODER_NAME = "cl100k_base"


REVIEWER_PROMPT = """\
You are a Reviewer for a wiki page that was just written.

I will give you one or more pages (title + body). For each page,
identify at most two of the most important of the following issues,
if any:

- contradiction: a claim that clearly disagrees with another page's
  claim in the set (name both pages).
- unsupported_claim: a strong factual claim with no source cited
  (neither inline nor in a frontmatter sources list).
- thin: the page is too sparse to be useful (fewer than ~120 words of
  substantive content).
- unlinked: the page has fewer than 2 wikilinks out, yet references
  multiple entities/concepts by name.

Skip pages that look fine. Do not fabricate issues.

Respond with ONLY a JSON object of this form:

```json
{
  "reviews": [
    {
      "page_id": "string",
      "issues": [
        {
          "kind": "contradiction | unsupported_claim | thin | unlinked",
          "severity": "warn | error",
          "message": "one sentence",
          "references": ["other-page-id", ...]
        }
      ]
    }
  ]
}
```

An empty issues list per page is fine. Do NOT include any prose
outside the JSON object.
"""


class Reviewer:
    """Batch-review of all pages written during one ingest."""

    def __init__(
        self,
        client: anthropic.Anthropic,
        config: CompendiumConfig,
        cost_tracker: CostTracker,
    ):
        self.client = client
        self.config = config
        self.cost_tracker = cost_tracker
        self.guardrails = config.get_guardrails()
        tiered: TieredModelConfig = config.tiered_models
        self.model = tiered.get_model(ModelTier.MECHANICAL)

    def review(self, pages: list[Page]) -> list[PageReviewResult]:
        """Run mechanical + LLM checks and return one result per page."""
        if not pages:
            return []

        results: dict[str, PageReviewResult] = {
            p.id: PageReviewResult(page_id=p.id) for p in pages
        }

        # Mechanical: token count + overflow
        try:
            enc = tiktoken.get_encoding(_ENCODER_NAME)
        except Exception:
            enc = None
        limit = self.guardrails.max_tokens_per_page
        for p in pages:
            body_tokens = len(enc.encode(p.body)) if enc else len(p.body) // 4
            results[p.id].token_count = body_tokens
            if body_tokens > limit:
                results[p.id].issues.append(
                    ReviewIssue(
                        severity=ReviewSeverity.WARN,
                        page_id=p.id,
                        kind="token_overflow",
                        message=(
                            f"page body is {body_tokens} tokens; "
                            f"limit is {limit}"
                        ),
                    )
                )
            # Mechanical audit of evidence metadata set by PageWriter.
            # These don't call an LLM; they catch drift between what a
            # page claims about its evidence and what the frontmatter
            # structure can actually support. The Mender's rule-based
            # contradiction resolver trusts these fields, so a bad
            # pairing here (e.g. "high confidence on a tertiary-only
            # page") would cause the Mender to pick the wrong winner.
            for issue in _audit_evidence_metadata(p):
                results[p.id].issues.append(issue)

        # LLM batch review
        llm_issues = self._llm_review(pages)
        for issue in llm_issues:
            if issue.page_id in results:
                results[issue.page_id].issues.append(issue)

        return list(results.values())

    def _llm_review(self, pages: list[Page]) -> list[ReviewIssue]:
        user_blocks = ["Pages under review:\n"]
        for p in pages:
            sources_tag = ", ".join(p.sources) if p.sources else "(none)"
            user_blocks.append(
                f"---\n"
                f"page_id: {p.id}\n"
                f"title: {p.title}\n"
                f"type: {p.type.value}\n"
                f"sources_frontmatter: {sources_tag}\n\n"
                f"{p.body}\n"
            )
        user_msg = "\n".join(user_blocks)

        try:
            self.cost_tracker.check_budget()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=REVIEWER_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            self.cost_tracker.record_call(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=self.model,
                is_sub_call=True,
            )
        except Exception as exc:
            return [
                ReviewIssue(
                    severity=ReviewSeverity.INFO,
                    page_id=pages[0].id,
                    kind="reviewer_error",
                    message=f"Reviewer LLM call failed: {exc}",
                )
            ]

        text = response.content[0].text.strip() if response.content else ""
        return list(self._parse_issues(text))

    @staticmethod
    def _parse_issues(text: str):
        # Strip ```json ... ``` fences if the model added them.
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:]
        # Find the first '{' and last '}' for robustness.
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return
        for review in data.get("reviews", []) or []:
            page_id = review.get("page_id") or ""
            for raw in review.get("issues", []) or []:
                kind = raw.get("kind") or "note"
                message = raw.get("message") or ""
                severity_raw = (raw.get("severity") or "warn").lower()
                try:
                    severity = ReviewSeverity(severity_raw)
                except ValueError:
                    severity = ReviewSeverity.WARN
                yield ReviewIssue(
                    severity=severity,
                    page_id=page_id,
                    kind=kind,
                    message=message,
                    references=list(raw.get("references") or []),
                )


# ---------------------------------------------------------------------------
# Evidence-metadata audit (mechanical, no LLM)
# ---------------------------------------------------------------------------


def _audit_evidence_metadata(page: Page):
    """Yield ReviewIssues where `source_tier` / `confidence` look suspect.

    Design note: these checks only see the Page itself. Cross-page audits
    (e.g. verifying a concept page's `source_tier == "primary"` claim by
    loading each cited source and checking ITS tier) require vault access
    and are deferred to a future pass. The checks here catch local
    inconsistencies only:

    - unknown vocabulary values (caught earlier by PageWriter but may leak
      through if a page was hand-edited or round-tripped through yaml);
    - `confidence: high` with zero cited sources (no evidence base);
    - `confidence: high` with only one cited source AND `source_tier`
      below "primary" (over-confident on weak evidence);
    - non-SOURCE page with `source_tier` set but no sources listed
      (can't derive a tier from nothing);
    - SOURCE-type page with no `source_tier` (writer forgot to assign).
    """
    tier = (page.source_tier or "").strip().lower()
    conf = (page.confidence or "").strip().lower()
    src_count = len(page.sources)

    if tier and tier not in {"primary", "secondary", "tertiary"}:
        yield ReviewIssue(
            severity=ReviewSeverity.WARN,
            page_id=page.id,
            kind="evidence_metadata",
            message=(
                f"source_tier={tier!r} is not one of "
                "'primary'|'secondary'|'tertiary'"
            ),
        )
    if conf and conf not in {"high", "medium", "low"}:
        yield ReviewIssue(
            severity=ReviewSeverity.WARN,
            page_id=page.id,
            kind="evidence_metadata",
            message=(
                f"confidence={conf!r} is not one of 'high'|'medium'|'low'"
            ),
        )

    if conf == "high" and src_count == 0:
        yield ReviewIssue(
            severity=ReviewSeverity.WARN,
            page_id=page.id,
            kind="confidence_unsupported",
            message=(
                "confidence=high but no sources are cited — high confidence "
                "requires at least one primary source on the page"
            ),
        )
    elif conf == "high" and src_count == 1 and tier and tier != "primary":
        yield ReviewIssue(
            severity=ReviewSeverity.WARN,
            page_id=page.id,
            kind="confidence_over_stated",
            message=(
                f"confidence=high on single-source {tier} page — downgrade "
                "to medium or corroborate with a second source"
            ),
        )

    if page.type == PageType.SOURCE and not tier:
        yield ReviewIssue(
            severity=ReviewSeverity.INFO,
            page_id=page.id,
            kind="evidence_metadata",
            message=(
                "source page is missing source_tier; Mender's rule-based "
                "contradiction resolver will fall back to LLM for any "
                "contradiction that touches this source"
            ),
        )

    if (
        page.type != PageType.SOURCE
        and tier
        and src_count == 0
    ):
        yield ReviewIssue(
            severity=ReviewSeverity.WARN,
            page_id=page.id,
            kind="evidence_metadata",
            message=(
                f"source_tier={tier!r} set but no sources cited — tier "
                "on non-source pages should derive from cited sources"
            ),
        )
