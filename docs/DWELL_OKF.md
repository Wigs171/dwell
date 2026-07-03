# Dwell × Open Knowledge Format (OKF) — align the substrate, keep the reader

> **PLANNED / direction — 2026-07-01.** Google's **Open Knowledge Format** (OKF, v0.1 —
> `GoogleCloudPlatform/knowledge-catalog/okf`, and the Cloud blog "how the open knowledge
> format can improve data sharing") standardizes almost exactly the substrate Dwell already
> uses. **Decision (user, 2026-07-01):** converge toward OKF via a thin **compatibility
> layer** — read *both* link styles and alias the frontmatter — **not a rewrite.** Cheap,
> because the formats are ~90% identical already. The reader/transform layer is the moat and
> sits *above* the substrate; a format war can't reach it.

## TL;DR
OKF **is the Dwell vault, standardized**: a directory of Markdown files with YAML
frontmatter, cross-linked into a knowledge graph, optional `index.md`/`log.md`, only `type`
required, explicitly *"a format, not a platform … not a knowledge-graph service,"* with a
producer/consumer split (enrichment agents write, consumption agents read). It "formalizes
the LLM-wiki pattern" (Karpathy) — the exact lineage Dwell comes from. Dwell independently
arrived at the same design: **the CREED reads like OKF's rationale written twice.**

So the move is positioning, not architecture: **let Google commoditize the substrate; Dwell
owns the diffusion reading experience on top of it** — and render *any* OKF bundle (a
BigQuery data catalog, an exported second brain, the `coleam00/cole-medin-ai-coding` bundle),
not just Dwell-authored vaults. Independent convergence on the substrate is the strongest
signal our foundation is right.

## What OKF is (v0.1)
- **Storage:** a directory of Markdown + YAML frontmatter "concept" files. "If you can `cat`
  a file you can read OKF; if you can `git clone` a repo you can ship it." No schema
  registry, no central authority, no required tooling.
- **Frontmatter:** only `type` is required (a free string naming the concept kind — "BigQuery
  Table", "metric", "runbook"). Recommended: `title`, `description`, `resource` (canonical
  URI), `tags`, `timestamp`.
- **Graph:** ordinary Markdown links `[text](/path.md)` between concepts; *"the specific kind
  of relationship is conveyed by the surrounding prose, not by the link itself."*
- **Files:** optional `index.md` and `log.md`; body conventions like `# Schema`,
  `# Examples`, `# Citations`.
- **Roles:** enrichment agents (write) vs consumption agents (read); minimally opinionated —
  no fixed taxonomy of concept types, no domain schema.

## The mapping (Dwell ↔ OKF)
| | Dwell vault | OKF | reconcile |
| --- | --- | --- | --- |
| Substrate | Markdown + YAML frontmatter | same | ✓ nothing to do |
| Graph link | `[[slug]]` wikilink | `[text](/path.md)` | **parse both** (the one real engine change) |
| Identity | frontmatter `id` (slug) | file path | path-as-id when `id` absent |
| Required field | `type` (+id/title/summary/…) | `type` only | ✓ superset |
| Summary | `summary` | `description` | alias |
| Timestamp | `updated` | `timestamp` | alias |
| Provenance | `sources: [ids]` | `resource: URI` | keep both (different semantics) |
| Aliases | `aliases` | — | additive (fine) |
| Top-level | `index.md`, `log.md`, `CLAUDE.md` | `index.md`, `log.md`, *(no marker)* | make the marker optional |
| Edge typing | untyped links + `_meta/enrichment-graph` | untyped, prose-conveyed | ✓ same; sidecar stays additive |
| Concept types | small enum: entity·concept·source·synthesis | free string | map unknown → navigable "concept"; graceful defaults |

## Strategic frame — the reader on a standard substrate
OKF standardizes *storage and exchange*. Dwell is the *experience*: Form × Level × Lens ×
Length × Voice × Language re-pitch, Paths, confluence, quizzes. **Nobody in the OKF ecosystem
has that.** Being an OKF *reader* turns every OKF bundle into Dwell content, and makes Dwell
the natural front-end for the format Google is pushing. Economics unchanged and reinforced:
the substrate is a portable capital good (now an industry-shared one); Dwell monetizes the
rendering. See [[project_dwell_education]].

## The compatibility layer (the bounded work — additive, no rewrite)
1. **Dual link parsing** — in `Brain.load`, add a Markdown-link regex beside `_WIKILINK_RE`:
   extract the `(path.md)` target, resolve it (relative, stem, lowercased) to a node id, add
   to `out_links`. Wikilinks keep working; OKF links now also populate the graph.
2. **Frontmatter aliasing + path-as-identity** — in the page parser, accept `description`→
   `summary`, `timestamp`→`updated`, `resource` (store alongside `sources`); when `id` is
   absent, derive it from the file path. Only `type` is required to load.
3. **OKF import / read mode** — point Dwell at any OKF directory and render it. `_meta/`
   sidecars (enrichment, paths, ledger) remain Dwell-specific and additive — legal under
   OKF's minimally-opinionated rule.
4. **Optional vault marker** — OKF has *no* marker; a folder of frontmatter'd Markdown *is*
   the format. Relax `VaultPaths.is_initialized()` to also accept "has `index.md`, or ≥1
   Markdown file with frontmatter." **This also settles the old `CLAUDE.md`→`AGENTS.md`
   question**: the marker becomes optional, not renamed.
5. **(later) OKF export** — emit a Dwell vault as an OKF bundle: rewrite `[[slug]]` →
   `[title](slug.md)`, map frontmatter field names, drop `_meta/` (or keep as extras).

## What OKF does NOT change
Everything above the substrate. **Paths, the confluence frame, the generator, and every
transform operate on `Brain` (nodes + `out_links`) — populated identically whichever link
style produced it.** If anything, Paths get *more* valuable: a curated path over a company's
OKF data catalog ("onboard to the warehouse") is exactly the business-tutorial use case, now
over a standardized, industry-shared substrate.

## What NOT to do
- **No rewrite** for a **v0.1** spec with unproven adoption — a compat layer only. Bet on
  interop, not on OKF "winning."
- Don't drop `[[wikilinks]]` — keep them; add OKF links alongside.
- Don't force Dwell's `type` enum onto imported bundles — default unknown types to navigable
  concepts; degrade gracefully (voice-page detection just finds none → preset voices).

## Open questions
- **`type` semantics:** Dwell's enum drives behavior (source pages excluded from navigation,
  `the-voice-of-*` → narrator, explorer's entity/concept split). Arbitrary OKF types need a
  default lane. What maps to "source" (excluded) vs "navigable"?
- **`resource` vs `sources`:** OKF `resource` is the concept's canonical URI; Dwell `sources`
  is a provenance list. Keep both fields; don't conflate.
- **Round-trip fidelity:** does import→export preserve enough (aliases, sources, `_meta`)?
- **Adoption risk:** OKF is v0.1. Revisit convergence depth as (if) it gains traction.

## Related
- Substrate philosophy: `DWELL_CREED.md` (the CREED == OKF's rationale). Roadmap:
  `DWELL_ROADMAP.md`. Paths (operate on the graph regardless of format): `DWELL_PATHS.md`.
- External: OKF spec `GoogleCloudPlatform/knowledge-catalog/okf/SPEC.md`; the Cloud blog on
  OKF for data sharing; `coleam00/cole-medin-ai-coding` (an OKF bundle in the wild + a tiny
  `okf-cli.py` navigator — a candidate thing for Dwell to render).
- Memory: [[project_the_current]] (Dwell log) · [[reference_dwell_creed_and_mercury]].

*Authored 2026-07-01. PLANNED — compat layer not built yet. Update as slices land.*
