# Vault Schema — Biology 101 (Demo): an AI-generated demonstration knowledge base on first-year college biology (cells, energy, genetics, evolution, ecology)

This vault holds a compounding knowledge base on **First-year college biology — cells, energy, genetics, evolution, and ecology**, maintained
incrementally by LLM agents as sources are ingested. Source documents are
immutable; the wiki is the compiled, cross-linked synthesis.

*Initialized 2026-06-28.*

## Structure

```
./
├── CLAUDE.md               # this file — schema and conventions
├── index.md                # content-oriented catalog (auto-maintained)
├── log.md                  # chronological record of operations
├── raw/                    # immutable source documents
│   ├── articles/
│   ├── papers/
│   ├── transcripts/
│   └── assets/             # images, attachments
└── wiki/                   # LLM-owned pages
    ├── entities/           # people, works, organizations, places
    ├── concepts/           # ideas, theories, frameworks
    ├── sources/            # one summary page per ingested source
    ├── syntheses/          # comparisons, overviews, filed query answers
    └── _meta/
        ├── expansion.md    # Explorer's living list of gaps and leads
        ├── contradictions.md
        └── orphans.md
```

## Page conventions

Every page begins with YAML frontmatter:

```yaml
---
id: kebab-case-slug            # matches filename sans .md
title: Human Readable Title
type: entity | concept | source | synthesis
summary: One-line summary for the index
tags: [tag-a, tag-b]
aliases: []                    # alternate names that resolve here
sources: [source-id-1]         # source page IDs this draws from
updated: YYYY-MM-DD
---
```

Below the frontmatter, the body starts with `# Title` and uses Obsidian
wikilink syntax:

- `[[Page Title]]` — link by title or ID
- `[[Page Title|inline alias]]` — piped display text
- `![[Page Title]]` — transclude

Filenames are kebab-case, always end in `.md`, and the filename sans
extension equals the frontmatter `id`.

## Operations

**Ingest** (`cli.py ingest <source> --vault .`)
A new source is read, routed against the existing index, and integrated
into N page changes. Every ingest writes a `sources/<source-id>.md`
summary page, may create new entity/concept pages, and may update any
existing pages the source informs. The log records every page touched.

**Query** (`cli.py query "..." --vault .`)
Read the index to locate relevant pages, drill in, synthesize an answer.
Optionally file the answer back as a `syntheses/...md` page so the
exploration compounds into the corpus.

**Lint** (`cli.py lint --vault .`)
Health check: orphan pages, broken wikilinks, stale citations,
cross-page contradictions. Produces `_meta/orphans.md`,
`_meta/contradictions.md`, and feeds signals into Explorer.

**Explore** (`cli.py explore --vault .`)
Propose where the vault should grow next: gaps (broken links are
requested pages), open questions (unresolved contradictions), missed
connections (entities co-occurring but not cross-linked), source
suggestions (reads that would resolve gaps), thesis drift (how recent
sources are pulling the synthesis). Output is ranked and lives in
`_meta/expansion.md`. Also runs automatically after every Ingest and
Lint.

## Writing style — these rules apply to every page

- **Reference-grade, not essay-grade.** Dense with facts and wikilinks.
  No throat-clearing, no "in conclusion."
- **Cite sources explicitly.** When stating a non-trivial claim, either
  end the sentence with `(see [[source-id]])` or list sources in the
  frontmatter. Claims without attribution are a red flag.
- **Cross-link generously.** A page with no wikilinks to other pages is
  almost always wrong. Every entity and concept mentioned that could be
  its own page *should* be wikilinked, even if the target doesn't yet
  exist — Explorer uses broken links as gap signals.
- **Flag tensions, don't overwrite.** When a new source contradicts an
  existing claim, do NOT silently replace the old text. Add an
  `## Open questions` section describing the tension and which sources
  disagree. Reviewer will file it to `_meta/contradictions.md`.
- **Keep pages focused.** If a page grows past roughly 1000 words, it
  probably wants to be split. Prefer many narrow pages to a few wide
  ones — the wiki's value is in the edges, not the nodes.
- **Update `updated` whenever the body changes.** It's a cheap recency
  signal for Lint.

## Topic focus

This is the **Biology 101 demo vault** that ships with Dwell — a small,
self-contained, original knowledge base covering first-year college biology
(cells, energy, genetics, evolution, ecology).

- **Original, source-free content.** Every page was written from general
  textbook knowledge specifically for this demo, so pages intentionally carry
  `sources: []` and make no `(see ...)` citations. There are no `raw/` source
  documents and no `sources/` pages — that is by design for the demo, not a gap.
- **Entities** are people (scientists); **concepts** are ideas, structures,
  and processes; **syntheses** tie several concepts into one overview.
- **Narrator voice.** `the-voice-of-the-naturalist` (tagged `voice`) is the
  default reading persona Dwell uses for this vault.
- Scope is deliberately ~40 pages to keep the demo light; grow it with the
  Learn tab or `cli.py ingest` / `cli.py research`.
