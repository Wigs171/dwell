"""Vault directory layout and path conventions.

A vault is a filesystem directory that follows this structure:

    <vault>/
    ├── CLAUDE.md               # schema, co-evolved conventions
    ├── index.md                # content catalog
    ├── log.md                  # chronological record of ops
    ├── raw/                    # immutable sources
    │   ├── articles/
    │   ├── papers/
    │   ├── transcripts/
    │   └── assets/             # images, other binaries
    └── wiki/                   # LLM-owned pages
        ├── entities/
        ├── concepts/
        ├── sources/            # one summary page per ingested source
        ├── syntheses/          # comparisons, overviews, query answers
        └── _meta/
            ├── expansion.md    # Explorer output
            ├── contradictions.md
            └── orphans.md
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VaultPaths:
    """Canonical paths within a vault directory.

    All methods return pathlib.Path objects rooted at `root`. No I/O
    happens here — this type is pure path arithmetic.
    """

    root: Path

    @classmethod
    def for_vault(cls, vault: str | Path) -> "VaultPaths":
        return cls(root=Path(vault).resolve())

    # Top-level files
    @property
    def claude_md(self) -> Path:
        return self.root / "CLAUDE.md"

    @property
    def index_md(self) -> Path:
        return self.root / "index.md"

    @property
    def log_md(self) -> Path:
        return self.root / "log.md"

    # Top-level dirs
    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def wiki(self) -> Path:
        return self.root / "wiki"

    # Raw subdirs
    @property
    def raw_articles(self) -> Path:
        return self.raw / "articles"

    @property
    def raw_papers(self) -> Path:
        return self.raw / "papers"

    @property
    def raw_transcripts(self) -> Path:
        return self.raw / "transcripts"

    @property
    def raw_assets(self) -> Path:
        return self.raw / "assets"

    # Wiki subdirs
    @property
    def entities(self) -> Path:
        return self.wiki / "entities"

    @property
    def concepts(self) -> Path:
        return self.wiki / "concepts"

    @property
    def sources(self) -> Path:
        return self.wiki / "sources"

    @property
    def syntheses(self) -> Path:
        return self.wiki / "syntheses"

    @property
    def meta(self) -> Path:
        return self.wiki / "_meta"

    # Meta files
    @property
    def expansion_md(self) -> Path:
        return self.meta / "expansion.md"

    @property
    def contradictions_md(self) -> Path:
        return self.meta / "contradictions.md"

    @property
    def orphans_md(self) -> Path:
        return self.meta / "orphans.md"

    @property
    def history_jsonl(self) -> Path:
        """Append-only time-series of lint metrics — one JSON object per run.

        Each `lint` appends a line here so vault health can be trended over
        time (converging vs sprawling). Read by the `health` command.
        """
        return self.meta / "history.jsonl"

    @property
    def contradiction_ledger_json(self) -> Path:
        """Durable contradiction state: open / resolved / by-design.

        Lets a contradiction be marked a genuine scholarly tension
        (by-design, preserved and silenced) or resolved (reappearance is a
        regression). Reconciled against the live scan every lint.
        """
        return self.meta / "contradiction-ledger.json"

    @property
    def grounding_md(self) -> Path:
        """Claim-grounding report: quotes on pages checked against cited raw."""
        return self.meta / "grounding-report.md"

    # Enrichment sidecars (cli.py enrich — see DWELL_ENRICH_PLAN.md). One file per
    # data point so a consumer loads only what it needs and a vault missing a
    # dimension simply lacks that file (graceful, not broken).
    @property
    def enrichment_graph_json(self) -> Path:
        """Typed/untyped edge graph + per-node salience (centrality)."""
        return self.meta / "enrichment-graph.json"

    @property
    def enrichment_temporal_json(self) -> Path:
        """Time index: dates/periods extracted from page bodies, sorted."""
        return self.meta / "enrichment-temporal.json"

    @property
    def enrichment_claims_json(self) -> Path:
        """Claims layer: propositions/quotes + provenance + grounding verdict."""
        return self.meta / "enrichment-claims.json"

    @property
    def enrichment_terms_json(self) -> Path:
        """Terms → glosses (entity/concept pages + aliases)."""
        return self.meta / "enrichment-terms.json"

    @property
    def enrichment_axes_json(self) -> Path:
        """Semantic axes (Phase B LLM): per-page stance, viewpoints, analogies, symbols,
        procedures, stages, parts, functions, caveats, quantities, places, definitions,
        questions, difficulty. Keyed by page id; `hashes` block gates idempotent re-runs."""
        return self.meta / "enrichment-axes.json"

    @property
    def enrichment_md(self) -> Path:
        """Human-readable enrichment summary."""
        return self.meta / "enrichment-report.md"

    def all_dirs(self) -> list[Path]:
        """Every directory that must exist in a well-formed vault."""
        return [
            self.raw_articles,
            self.raw_papers,
            self.raw_transcripts,
            self.raw_assets,
            self.entities,
            self.concepts,
            self.sources,
            self.syntheses,
            self.meta,
        ]

    def is_initialized(self) -> bool:
        """A vault is considered initialized when CLAUDE.md exists at the root."""
        return self.root.is_dir() and self.claude_md.exists()
