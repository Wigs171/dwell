# Vault Schema — Cael Morren — a secondary-world fiction vault: the drowned-and-resurfacing tidal city of Cael Morren, its Long Tide and the ancient Deepworks that drives it, its three quarrelling factions, and the Quickening that is breaking the tide

This vault is an original **fiction world-bible**, not a knowledge base of real facts. It holds the standing canon of an invented world — places, people, factions, lore, and tensions — as cross-linked wiki pages. It exists as a Dwell test substrate: a content-neutral world through which the reader and the Paths engine dream narratives at runtime. Nothing here is a plotted story; every page is a canonical, atmospheric entry about one piece of the world.

*Initialized 2026-07-02. All content is original fiction (no external source, no license constraint).*

## The world in one breath

Cael Morren is a city that lives with the **Long Tide** — a sea that rises and falls across ~40 years. At low ebb the drowned **Undercity** surfaces; at full flood the city floods to its upper tiers. The tide is driven by the **Deepworks**, an ancient machine beneath the Undercity. Three factions answer the drowning differently — the **Tidewright Guild** (read and rebuild), the **Anchorites** (stop it), the **Ferrymen** (ride it) — and the **Quickening**, the tide now arriving early, is breaking the truce between them.

## Structure

```
./
├── CLAUDE.md               # this file — schema + world summary
├── raw/                    # (empty — this world has no external sources)
└── wiki/                   # the world-bible
    ├── entities/           # people, places, factions
    ├── concepts/           # lore, phenomena, events (the tide, the Deepworks, the craft)
    ├── syntheses/          # world overview, the faction quarrel, and the narrator voice
    └── _meta/              # engine state (embeddings, history, paths) — auto-created
```

## Conventions

- Every page: YAML frontmatter (`id`, `title`, `type`, `summary`, `tags`, `aliases`, `sources`, `updated`) + `# Title` + atmospheric prose in `## ` sections, cross-linked with `[[wikilinks]]`.
- `type` is one of `entity` / `concept` / `synthesis`. `sources: [world-bible]` is a placeholder (there is no external source).
- **Narrator voice:** `syntheses/the-voice-of-the-tidewright.md` (tag `voice`) is the vault's default reading persona — a weathered guild chronicler.
- This is the recommended vault for exercising **Guided Paths + the dream dial** together: a densely linked world with a live central tension (the Quickening) that a generated spine can move toward.
