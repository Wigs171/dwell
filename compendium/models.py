"""Pydantic data models for the compendium wiki system."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Wiki pages
# ---------------------------------------------------------------------------


class PageType(str, Enum):
    """Canonical page types. Each lives in its own `wiki/<type>s/` directory."""

    ENTITY = "entity"        # a person, place, work, organization
    CONCEPT = "concept"      # an idea, theory, framework, principle
    SOURCE = "source"        # summary page for an ingested source
    SYNTHESIS = "synthesis"  # comparison, overview, or filed query answer


class Page(BaseModel):
    """A single wiki page. The serialized form is markdown with YAML frontmatter.

    The `id` matches the filename without `.md`. The `type` determines the
    subdirectory under `wiki/`.
    """

    id: str = Field(description="kebab-case slug; matches filename (sans .md)")
    title: str
    type: PageType
    summary: str = Field(
        default="",
        description="one-line summary; shown in index.md",
    )
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(
        default_factory=list,
        description="alternate names that resolve to this page",
    )
    sources: list[str] = Field(
        default_factory=list,
        description="source page IDs this page draws from",
    )
    updated: str = Field(
        default="",
        description="ISO date YYYY-MM-DD of most recent update",
    )
    # Evidence / confidence metadata — used by the Mender's rule hierarchy
    # for contradiction resolution. All three are optional (empty string
    # or empty list means "unspecified"); older pages written before these
    # fields existed will not have them and the Mender will fall back to
    # its LLM-judgment branch for those contradictions.
    source_tier: str = Field(
        default="",
        description=(
            "'primary' | 'secondary' | 'tertiary' | '' — the evidence tier "
            "this page rests on. On SOURCE pages this is the tier of the "
            "source itself (primary = original work / documentation; "
            "secondary = analysis/review of a primary; tertiary = "
            "aggregation or summary-of-summary). On non-source pages this "
            "is the effective tier (PageWriter sets it to the best tier "
            "among cited sources). Empty = unspecified."
        ),
    )
    confidence: str = Field(
        default="",
        description=(
            "'high' | 'medium' | 'low' | '' — PageWriter's confidence in "
            "this page's core claims, based on source quality, specificity, "
            "and corroboration. Reviewer audits this against the cited "
            "sources. NOTE: unrelated to VerificationResult.confidence "
            "(citation-verification), which is a separate concept."
        ),
    )
    superseded_by: list[str] = Field(
        default_factory=list,
        description=(
            "Page IDs whose claims supersede this page's claims (set by "
            "the Mender when rule-based contradiction resolution picks a "
            "winner). Loser pages are never deleted; they stay as "
            "provenance with this backref. Empty = this page is current."
        ),
    )
    body: str = Field(default="", description="markdown body (wikilinks allowed)")

    def frontmatter_dict(self) -> dict:
        """Frontmatter fields as a plain dict (excludes body).

        Evidence metadata (`source_tier`, `confidence`, `superseded_by`) is
        omitted when empty to keep legacy pages diff-clean on first
        round-trip — only pages that have been touched by the new
        PageWriter or Mender gain these keys on disk.
        """
        fm: dict = {
            "id": self.id,
            "title": self.title,
            "type": self.type.value,
            "summary": self.summary,
            "tags": self.tags,
            "aliases": self.aliases,
            "sources": self.sources,
            "updated": self.updated,
        }
        if self.source_tier:
            fm["source_tier"] = self.source_tier
        if self.confidence:
            fm["confidence"] = self.confidence
        if self.superseded_by:
            fm["superseded_by"] = self.superseded_by
        return fm


# Ordered best-to-worst so rule evaluation can compare by index.
# Empty string ("") means unspecified and is treated as worse than any
# named tier/level when one side has a value and the other doesn't.
SOURCE_TIER_ORDER: tuple[str, ...] = ("primary", "secondary", "tertiary")
CONFIDENCE_ORDER: tuple[str, ...] = ("high", "medium", "low")


def source_tier_rank(value: str) -> int:
    """Return the rank of a source tier (lower index = better). -1 if unknown."""
    try:
        return SOURCE_TIER_ORDER.index(value)
    except ValueError:
        return -1


def confidence_rank(value: str) -> int:
    """Return the rank of a confidence level (lower index = better). -1 if unknown."""
    try:
        return CONFIDENCE_ORDER.index(value)
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# Ingest plan / changes
# ---------------------------------------------------------------------------


class PageChangeOp(str, Enum):
    CREATE = "create"
    UPDATE = "update"


class PageChange(BaseModel):
    """A single page-level change proposed by IngestRouter.

    PageWriter consumes this plus the source content (and, for updates,
    the existing page) to produce the final page content.
    """

    op: PageChangeOp
    page_id: str = Field(description="kebab-case slug")
    page_type: PageType
    title: str
    reason: str = Field(
        description="why this source triggers this change — fed to PageWriter"
    )


class IngestPlan(BaseModel):
    """IngestRouter's output: which pages to create/update for a given source."""

    source_id: str
    source_title: str
    source_summary: str
    changes: list[PageChange] = Field(default_factory=list)
    implied_wikilinks: list[str] = Field(
        default_factory=list,
        description="wikilink targets mentioned by the source that have no page "
        "yet — Explorer treats these as gaps",
    )
    rationale: str = Field(
        default="",
        description="one-paragraph explanation of the plan as a whole",
    )


# ---------------------------------------------------------------------------
# Review + ingest report
# ---------------------------------------------------------------------------


class ReviewSeverity(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class ReviewIssue(BaseModel):
    severity: ReviewSeverity = ReviewSeverity.WARN
    page_id: str
    kind: str = Field(description="'contradiction' | 'token_overflow' | 'thin' | ...")
    message: str
    references: list[str] = Field(
        default_factory=list,
        description="other page IDs implicated in the issue",
    )


class PageReviewResult(BaseModel):
    page_id: str
    token_count: int = 0
    issues: list[ReviewIssue] = Field(default_factory=list)


class IngestReport(BaseModel):
    """Summary of a single ingest operation."""

    source_id: str
    source_title: str
    timestamp: str = ""
    pages_created: list[str] = Field(default_factory=list)
    pages_updated: list[str] = Field(default_factory=list)
    implied_wikilinks: list[str] = Field(default_factory=list)
    review_issues: list[ReviewIssue] = Field(default_factory=list)
    cost_dollars: float = 0.0
    expansion_proposal_count: int = 0


# ---------------------------------------------------------------------------
# Explore
# ---------------------------------------------------------------------------


class ExpansionKind(str, Enum):
    GAP = "gap"                          # a page that should exist but doesn't
    OPEN_QUESTION = "open_question"      # unresolved tension / contradiction
    MISSED_CONNECTION = "missed_connection"  # two pages that should cross-link
    SOURCE_SUGGESTION = "source_suggestion"  # external read to fetch
    THESIS_DRIFT = "thesis_drift"        # direction the corpus is pulling


class ExpansionProposal(BaseModel):
    """A single actionable expansion suggestion from the Explorer."""

    kind: ExpansionKind
    title: str = Field(description="one-line title; may contain [[wikilinks]]")
    priority: int = Field(
        default=3, ge=1, le=5, description="1 highest, 5 lowest"
    )
    signal: str = Field(
        default="",
        description="one sentence naming the mechanical signal that triggered this",
    )
    rationale: str = Field(
        default="",
        description="why this matters — 1-3 sentences, reference pages",
    )
    related: list[str] = Field(
        default_factory=list,
        description="related page IDs",
    )


class ExpansionReport(BaseModel):
    timestamp: str = ""
    topic: str = ""
    proposals: list[ExpansionProposal] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


class BrokenLinkGroup(BaseModel):
    target: str
    ref_count: int
    referrers: list[str] = Field(default_factory=list)


class Contradiction(BaseModel):
    pages: list[str] = Field(
        description="page IDs in tension — at least two"
    )
    summary: str = Field(
        description="one-line description of what conflicts"
    )
    details: str = Field(
        default="",
        description="the specific disagreeing claims, with quoted excerpts",
    )
    suggested_resolution: str = ""


class LintReport(BaseModel):
    timestamp: str = ""
    topic: str = ""
    orphan_pages: list[str] = Field(default_factory=list)
    broken_links: list[BrokenLinkGroup] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(
        default_factory=list,
        description="needs-attention contradictions (new + regressed + open); "
        "excludes by-design tensions silenced via the ledger",
    )
    pages_inspected: int = 0
    citations_checked: int = 0
    citations_verified_high: int = 0
    citations_verified_medium: int = 0
    citations_unverified: int = 0
    citation_verification_skipped: bool = False
    # Contradiction-ledger breakdown (subsets of `contradictions` plus the
    # silenced by-design count). See compendium.vault.contradiction_ledger.
    contradictions_new: int = 0
    contradictions_regressed: int = 0
    contradictions_by_design: int = 0
    # Claim-grounding stats (populated only when lint is run with ground=True).
    grounding_ran: bool = False
    grounded: int = 0
    grounding_loose: int = 0
    grounding_not_found: int = 0
    grounding_unverifiable: int = 0
    cost_dollars: float = 0.0


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class SourceReference(BaseModel):
    """A tracked source. Backs a `wiki/sources/<id>.md` summary page."""

    id: str = Field(description="kebab-case slug of the source")
    title: str
    url: Optional[str] = None
    citation: str = ""
    retrieved_date: str = ""
    raw_path: Optional[str] = Field(
        default=None,
        description="relative path within vault's raw/ directory",
    )


# ---------------------------------------------------------------------------
# REPL bookkeeping
# ---------------------------------------------------------------------------


class REPLTurnMetadata(BaseModel):
    """Metadata appended to LLM history each REPL turn. Never raw content."""

    turn_number: int
    code_executed: str
    stdout_length: int = 0
    stdout_prefix: str = Field(
        default="", description="first ~200 chars of stdout"
    )
    variables_changed: list[str] = Field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Verification (used by Lint)
# ---------------------------------------------------------------------------


class VerificationResult(BaseModel):
    """Result of verifying a citation via web search."""

    citation: str
    verified: bool
    confidence: str = Field(
        default="low",
        description="'high' | 'medium' | 'low'",
    )
    matching_url: Optional[str] = None
    note: str = ""


# ---------------------------------------------------------------------------
# Mend (janitor) — consume Lint/Reviewer diagnostics and act on them
# ---------------------------------------------------------------------------


class MendActionKind(str, Enum):
    """What the Mender did to an issue.

    Tier 1 (mechanical) actions are pure-Python rewrites that don't call
    an LLM. Tier 2 is one-shot LLM-assisted fixes per issue. Tier 3 is
    a REPL-driven page-body expansion. ESCALATED items are kept in the
    report as signal for Explorer.
    """

    # Tier 1 — mechanical
    ALIAS_REDIRECT = "alias_redirect"                 # broken link slug matched a known alias
    FRONTMATTER_FILLED = "frontmatter_filled"         # filled missing `updated` or default list fields
    WIKILINK_NORMALIZED = "wikilink_normalized"       # `[[Title Case]]` → `[[slug|Title Case]]` for Obsidian resolution
    # Tier 2 — one-shot LLM
    BROKEN_LINK_REDIRECTED = "broken_link_redirected" # LLM picked an existing target
    BROKEN_LINK_KEPT = "broken_link_kept"             # LLM judged it a legitimate gap; leave as signal
    CONTRADICTION_REVISED = "contradiction_revised"    # one page was revised to resolve
    CONTRADICTION_OPEN_QUESTIONS = "contradiction_open_questions"  # added `## Open questions` section
    # Rule-based (pre-LLM) contradiction resolution — see Mender's
    # apply_contradiction_rules. No LLM call; deterministic from frontmatter.
    CONTRADICTION_RULE_SUPERSEDED = "contradiction_rule_superseded"  # rule hierarchy picked a winner; loser got superseded_by
    TOKEN_OVERFLOW_TRIMMED = "token_overflow_trimmed"  # oversized page condensed while preserving wikilinks + citations
    # Tier 3 — REPL expansion
    THIN_PAGE_EXPANDED = "thin_page_expanded"
    # Tier 4 — source curation (keep / cull / escalate)
    SOURCE_KEPT_FOUNDATIONAL = "source_kept_foundational"  # timeless, keep regardless of age
    SOURCE_KEPT_ACTIVE = "source_kept_active"              # currently useful, keep
    SOURCE_SUPERSEDED = "source_superseded"                # newer source covers same ground — culled
    SOURCE_STALE = "source_stale"                          # fast-moving field, obsolete — culled
    SOURCE_CULLED = "source_culled"                        # generic culling record (files removed, tombstone set)
    # Terminal states
    ESCALATED = "escalated"                           # punted; emits signal for Explorer
    SKIPPED = "skipped"                               # not actionable this pass


class MendAction(BaseModel):
    """One Mender action — success, skip, or escalation.

    `page_id` is the primary page affected (empty for vault-wide actions).
    `pages` lists all pages involved (useful for contradictions and
    many-to-one broken-link redirects).
    """

    kind: MendActionKind
    page_id: str = ""
    pages: list[str] = Field(default_factory=list)
    summary: str = Field(description="one-line description of what happened")
    detail: str = Field(
        default="",
        description="multi-line details (before/after excerpts, LLM reasoning)",
    )
    cost_dollars: float = 0.0


class MendReport(BaseModel):
    """Output of one mend run. Written to `wiki/_meta/mend-report.md`."""

    timestamp: str = ""
    topic: str = ""
    dry_run: bool = False
    issues_considered: int = 0
    actions: list[MendAction] = Field(default_factory=list)
    cost_dollars: float = 0.0

    def by_kind(self, kind: MendActionKind) -> list[MendAction]:
        return [a for a in self.actions if a.kind == kind]

    def escalated(self) -> list[MendAction]:
        return self.by_kind(MendActionKind.ESCALATED)


# ---------------------------------------------------------------------------
# Domain vocabulary (used by PageWriter for practical topics)
# ---------------------------------------------------------------------------


class DomainProfile(BaseModel):
    """Domain-specific vocabulary and style guidance."""

    domain_name: str = Field(description="short domain name, e.g. 'calisthenics'")
    practitioner_term: str = Field(
        description="what to call the practitioner: 'coach', 'designer', 'developer'"
    )
    authority_sources: str = Field(
        description="key authorities, e.g. 'NSCA, ACSM' or 'WGI, DCI, AIGA'"
    )
    table_description: str = Field(
        description="what structured tables look like in this domain"
    )
    example_description: str = Field(
        description="what practical examples look like in this domain"
    )
    tips_description: str = Field(
        description="what practitioner tips look like in this domain"
    )
    mistake_categories: str = Field(
        description="common mistake types in this domain"
    )
    anti_filler: str = Field(
        description="what vague/unhelpful content looks like in this domain"
    )
    key_terms: dict[str, str] = Field(
        default_factory=dict,
        description="domain glossary: term -> definition",
    )
    example_briefs: list[str] = Field(
        default_factory=list,
        description="2-3 example page briefs showing expected detail level",
    )

    def format_for_prompt(self) -> str:
        lines = [
            "\n\n## Domain Context (auto-calibrated)\n",
            f"**Domain**: {self.domain_name}",
            f"**Practitioner term**: {self.practitioner_term}",
            f"**Authority sources**: {self.authority_sources}",
            f"**What 'structured tables' means here**: {self.table_description}",
            f"**What 'practical examples' means here**: {self.example_description}",
            f"**What 'practitioner tips' means here**: {self.tips_description}",
            f"**Common mistake categories**: {self.mistake_categories}",
            f"**Avoid**: {self.anti_filler}",
            "",
            "**Key terminology**:",
        ]
        for term, defn in self.key_terms.items():
            lines.append(f"- **{term}**: {defn}")
        if self.example_briefs:
            lines.append("")
            lines.append("**Example page briefs at the expected detail level**:")
            for brief in self.example_briefs:
                lines.append(f"- {brief}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Model tiering
# ---------------------------------------------------------------------------


class ModelTier(str, Enum):
    STRATEGIC = "strategic"      # judgment-heavy (routing, contradictions, explore)
    SYNTHESIS = "synthesis"      # prose-heavy (writing pages, query answers)
    MECHANICAL = "mechanical"    # bookkeeping (index updates, formatting, simple reviews)


class TieredModelConfig(BaseModel):
    strategic: str = "claude-opus-4-6"
    synthesis: str = "claude-sonnet-4-6"
    mechanical: str = "claude-haiku-4-5"

    def get_model(self, tier: ModelTier) -> str:
        return getattr(self, tier.value)

    @classmethod
    def from_single_model(cls, model: str) -> "TieredModelConfig":
        return cls(strategic=model, synthesis=model, mechanical=model)


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


class GuardrailConfig(BaseModel):
    """Limits and budgets for wiki operations."""

    max_repl_iterations: int = 50
    max_sub_calls_per_page: int = 10
    max_total_sub_calls: int = 200
    max_cost_dollars: float = 10.0
    max_tokens_per_page: int = 2000
    max_pages_per_ingest: int = 25
    stdout_prefix_length: int = 200
