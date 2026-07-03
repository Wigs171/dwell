# The vault format

A **vault** is a folder of cross-linked Markdown that Dwell reads. It is
content-neutral — the reader doesn't care whether it's biology, philosophy, or
your meeting notes. You can build one with the **Learn** tab, the `cli.py`
builder, or by hand (the bundled `vaults/Biology 101 (Demo)` was hand-authored).

## Directory layout

```
<vault>/
├── CLAUDE.md              # schema + conventions for this vault (created by `cli.py init`)
├── index.md              # auto-generated catalog of pages
├── log.md                # operation log
├── cover.jpg             # optional cover image shown in the library
├── raw/                  # immutable source material (optional)
│   ├── articles/  papers/  transcripts/  uploads/
│   └── assets/           # images referenced by pages, grouped by source
└── wiki/                 # the navigable pages
    ├── concepts/         # ideas, structures, processes
    ├── entities/         # people, works, organizations, places
    ├── syntheses/        # overviews that tie several pages together
    ├── sources/          # one page per ingested source (omit for hand-authored vaults)
    └── _meta/            # generated caches + reports (safe to delete; regenerated)
```

A vault is "recognized" by the app when it has a `CLAUDE.md` and at least one
page under `wiki/`.

## Page format

Every page is a Markdown file whose name (minus `.md`) equals its `id`. It begins
with YAML frontmatter:

```yaml
---
id: cell-membrane              # kebab-case; must equal the filename
title: Cell Membrane
type: concept                  # concept | entity | synthesis | source
summary: One-line description used in the index and previews.
tags: [cell, membrane, transport]
aliases: [plasma membrane]     # alternate names that resolve here (optional)
sources: []                    # source-page ids this draws from (optional)
updated: 2026-06-28
# images:                      # optional — figures shown in the reader
# - file: assets/<source>/figure.jpg
#   caption: What the figure shows.
---

# Cell Membrane

Flowing prose. Link generously with Obsidian wikilinks — by **id**
([[diffusion-and-osmosis]]) or with display text ([[atp|ATP]]). The reader
narrates this prose, so prefer connected sentences over bulleted lists.
```

### Conventions that make a good reading experience

- **Cross-link generously.** The graph is the substrate — the reader walks
  wikilinks and ranks neighbors. ~4+ links per page is a healthy density.
- **Narration-friendly prose.** Avoid lists/tables/code in the body; write
  paragraphs. Bold a key term on first use if you like.
- **Keep pages focused** (roughly under ~1000 words). Prefer many small,
  well-linked pages to a few large ones.
- **Broken links are allowed** — the builder treats them as "pages that should
  exist yet" (gap signals). For a hand-authored vault, only link to pages you
  actually create.

## Narrator voice pages

A page tagged `voice` (or named `the-voice-of-…`) is treated as a **narrator
persona** rather than a readable node. Its body describes how the vault should
sound; the reader offers it as a selectable voice and uses it as the default.
See `vaults/Biology 101 (Demo)/wiki/syntheses/the-voice-of-the-naturalist.md`.

## `_meta/` (generated)

These are created/maintained by the tools and are git-ignored:

- `.dwell-embeddings.json` — cached page embeddings (per model).
- `.dwell-tween-cache.json` — cached rendered reading-pages.
- `dwell-history.json` — your reading trail (drives Resume / Somewhere new).
- `enrichment-*.json` — optional layers from `cli.py enrich` (e.g. the timeline).
- `expansion.md`, `contradictions.md`, `orphans.md` — builder reports.

You never edit these by hand; delete them and they regenerate.

## OKF interop (accepted variations)

Dwell also reads [Open Knowledge Format](DWELL_OKF.md) bundles, so the parser accepts
OKF's dialect of the same substrate: `description` is read as `summary`, `timestamp`
as `updated`, `type` may be any free string (unknown types load as navigable concepts),
ordinary Markdown links `[text](slug.md)` count as graph edges beside `[[wikilinks]]`,
and a flat folder of frontmatter'd Markdown (no `wiki/` tree, no `CLAUDE.md`) is a
valid vault. Any vault exports to a flat OKF bundle via `server/okf_export.py` or the
OKF button on the vault card.

## Ghost links and `_meta/proposals/`

A wikilink (or OKF link) with no matching page is not an error — it is an **unwritten
door**: the reader offers it as a `◌` branch and renders a threshold page from its
mentions. Reading one stages a draft in `wiki/_meta/proposals/<slug>.md` (frontmatter
`type: proposal`, `kind: ghost-door`). Proposals appear as pending sources on the
vault card; checking one into a Learn build ingests it (the draft then moves to
`proposals/accepted/`), and deleting the file rejects it — the door stays a ghost.

