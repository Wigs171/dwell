# Compendiums as Agent Skills: Architecture Reference

## Overview

A compendium — a systematic, comprehensive collection of information and analysis on a topic — maps naturally onto the Agent Skills architecture. The Skills progressive disclosure model (metadata → instructions → resources) aligns with how an agent should navigate a compendium: know what exists, understand the structure, then load only the entries needed for the current task.

Combined with the RLM/REPL approach, this creates a system where agents can build, maintain, and reason over deep topic expertise without context rot.

---

## Architecture: How Compendiums Map to Skills

### Progressive Disclosure Alignment

| Skills Level | Compendium Equivalent | What It Contains | Token Cost |
|---|---|---|---|
| **Level 1: Metadata** | Topic scope & trigger | Name, description, when to use this compendium | ~100 tokens |
| **Level 2: SKILL.md** | Overview & dependency graph | Topic structure, major categories, navigation instructions, how entries relate | Under 5K tokens |
| **Level 3: Resources** | Individual compendium entries | Detailed entries per concept/sub-topic, loaded on demand | Effectively unlimited |

### Why This Works

The agent **never loads the entire compendium into context**. The workflow is:

1. Metadata tells the agent this compendium is relevant to the current task
2. SKILL.md gives the agent the structure map — what entries exist and how they connect
3. The agent programmatically navigates to only the entries it needs
4. Cross-references between entries are followed as needed via the dependency graph
5. The agent synthesizes from the loaded entries to complete the task

This is the REPL loop (Read → Evaluate → Print → Loop) running on top of the Skills progressive disclosure architecture. The compendium can be enormous — hundreds of entries — without any context penalty for unused content.

---

## File Structure

```
compendium-topic-name/
├── SKILL.md                    # Overview, dependency graph, navigation instructions
├── connections.json            # Dependency graph as structured data
├── entries/                    # Individual compendium entries
│   ├── concept-one.md
│   ├── concept-two.md
│   ├── concept-three.md
│   └── ...
├── scripts/
│   └── traverse.py             # Programmatic graph traversal utility
└── sources/                    # Optional: raw source material or citations
    ├── source-index.md
    └── ...
```

### File Roles

**SKILL.md** — The brain of the compendium. Contains the topic overview, the full list of entries with brief descriptions, the dependency graph in human-readable form, and instructions for how to navigate the compendium. This is the only file loaded when the skill triggers.

**connections.json** — The dependency graph as machine-readable data. Encodes which entries reference which other entries, enabling programmatic traversal. The agent can query this to find related concepts without loading every entry.

**entries/** — One markdown file per concept or sub-topic. Each entry is self-contained but includes references to related entries. Loaded individually and only when needed.

**scripts/traverse.py** — A utility script the agent can execute to navigate the dependency graph. Takes a starting concept and a query, returns the relevant entry paths. Output only (the script code never enters context).

**sources/** — Optional directory for citation tracking, raw source references, or provenance metadata. Supports the "proper data provenance" requirement from the RLM paper.

---

## Example: Philosophy of Time Compendium

### SKILL.md

```yaml
---
name: philosophy-of-time
description: >
  Compendium of the philosophy of time — its major theories, arguments,
  and connections across metaphysics, physics, and philosophy of mind.
  Use when the user asks about time, temporal logic, tense, persistence,
  time travel, fatalism, or related metaphysical topics. Also use when
  any task requires deep understanding of temporal concepts.
---
```

```markdown
# Philosophy of Time Compendium

## Purpose

This compendium provides systematic, comprehensive coverage of the
philosophy of time. It is organized as a dependency graph — entries
reference and build on each other rather than following a linear order.

## How to Use This Compendium

1. **Identify the relevant concept(s)** from the entry index below
2. **Check the dependency graph** to understand what foundational
   entries should be read first
3. **Load only the entries you need** from `entries/`
4. **Follow cross-references** within entries as needed
5. For programmatic traversal, run: `python scripts/traverse.py --start <concept> --query "<question>"`

Do NOT load all entries at once. Navigate the dependency graph to find
the minimal set of entries required for the current task.

## Entry Index

| Entry | File | Brief Description |
|-------|------|-------------------|
| Overview | `entries/overview.md` | The philosophy of time as a sub-discipline; major questions and schools |
| A-Theory vs B-Theory | `entries/a-theory-b-theory.md` | The central debate: is temporal passage real or illusory? |
| Presentism | `entries/presentism.md` | Only the present exists; past and future are unreal |
| Eternalism | `entries/eternalism.md` | All times are equally real; the block universe |
| Growing Block | `entries/growing-block.md` | The past and present exist; the future does not yet |
| Fatalism | `entries/fatalism.md` | The argument that the future is already fixed |
| Topology of Time | `entries/topology.md` | What shape does time have? Linear, branching, circular |
| Time Travel | `entries/time-travel.md` | Philosophical problems of backward and forward time travel |
| Temporal Parts | `entries/temporal-parts.md` | Do objects persist by enduring or by having temporal parts? |
| McTaggart's Argument | `entries/mctaggart.md` | The classic argument that time is unreal |
| Four Kinds of Time | `entries/four-kinds.md` | Metaphysical, physical, cosmic, and psychological time |
| Tense Logic | `entries/tense-logic.md` | Formal systems for reasoning about temporal propositions |
| Simultaneity & Relativity | `entries/simultaneity.md` | How special relativity challenges absolute simultaneity |
| Personal Identity & Time | `entries/personal-identity.md` | How persistence through time relates to selfhood |

## Dependency Graph (Human-Readable)

### Foundation Layer (read first when entering the topic)
- Overview → branches to all other entries
- A-Theory vs B-Theory → foundational for Presentism, Eternalism, Growing Block, McTaggart

### Core Theories (build on foundation)
- Presentism ← A-Theory vs B-Theory
- Eternalism ← A-Theory vs B-Theory
- Growing Block ← A-Theory vs B-Theory, Presentism, Eternalism
- McTaggart's Argument ← A-Theory vs B-Theory

### Applied / Extended (build on core)
- Fatalism ← Eternalism, Tense Logic
- Topology of Time ← Eternalism, Presentism
- Time Travel ← Topology of Time, Temporal Parts, Fatalism
- Temporal Parts ← Eternalism, Personal Identity & Time
- Four Kinds of Time ← Overview
- Tense Logic ← A-Theory vs B-Theory
- Simultaneity & Relativity ← Eternalism, A-Theory vs B-Theory
- Personal Identity & Time ← Temporal Parts, Presentism, Eternalism

### Traversal Hint
For most queries, start with **Overview** and **A-Theory vs B-Theory**,
then follow the dependency graph toward the specific concept needed.
```

### connections.json

```json
{
  "nodes": [
    { "id": "overview", "file": "entries/overview.md", "label": "Overview", "layer": "foundation" },
    { "id": "a-b-theory", "file": "entries/a-theory-b-theory.md", "label": "A-Theory vs B-Theory", "layer": "foundation" },
    { "id": "presentism", "file": "entries/presentism.md", "label": "Presentism", "layer": "core" },
    { "id": "eternalism", "file": "entries/eternalism.md", "label": "Eternalism", "layer": "core" },
    { "id": "growing-block", "file": "entries/growing-block.md", "label": "Growing Block", "layer": "core" },
    { "id": "mctaggart", "file": "entries/mctaggart.md", "label": "McTaggart's Argument", "layer": "core" },
    { "id": "fatalism", "file": "entries/fatalism.md", "label": "Fatalism", "layer": "applied" },
    { "id": "topology", "file": "entries/topology.md", "label": "Topology of Time", "layer": "applied" },
    { "id": "time-travel", "file": "entries/time-travel.md", "label": "Time Travel", "layer": "applied" },
    { "id": "temporal-parts", "file": "entries/temporal-parts.md", "label": "Temporal Parts", "layer": "applied" },
    { "id": "four-kinds", "file": "entries/four-kinds.md", "label": "Four Kinds of Time", "layer": "applied" },
    { "id": "tense-logic", "file": "entries/tense-logic.md", "label": "Tense Logic", "layer": "applied" },
    { "id": "simultaneity", "file": "entries/simultaneity.md", "label": "Simultaneity & Relativity", "layer": "applied" },
    { "id": "personal-identity", "file": "entries/personal-identity.md", "label": "Personal Identity & Time", "layer": "applied" }
  ],
  "edges": [
    { "from": "overview", "to": "a-b-theory" },
    { "from": "overview", "to": "four-kinds" },
    { "from": "a-b-theory", "to": "presentism" },
    { "from": "a-b-theory", "to": "eternalism" },
    { "from": "a-b-theory", "to": "growing-block" },
    { "from": "a-b-theory", "to": "mctaggart" },
    { "from": "a-b-theory", "to": "tense-logic" },
    { "from": "a-b-theory", "to": "simultaneity" },
    { "from": "presentism", "to": "growing-block" },
    { "from": "presentism", "to": "topology" },
    { "from": "presentism", "to": "personal-identity" },
    { "from": "eternalism", "to": "growing-block" },
    { "from": "eternalism", "to": "fatalism" },
    { "from": "eternalism", "to": "topology" },
    { "from": "eternalism", "to": "temporal-parts" },
    { "from": "eternalism", "to": "simultaneity" },
    { "from": "eternalism", "to": "personal-identity" },
    { "from": "tense-logic", "to": "fatalism" },
    { "from": "topology", "to": "time-travel" },
    { "from": "temporal-parts", "to": "time-travel" },
    { "from": "temporal-parts", "to": "personal-identity" },
    { "from": "fatalism", "to": "time-travel" }
  ]
}
```

### Example Entry: entries/fatalism.md

```markdown
# Fatalism

## Summary
Fatalism is the view that the future is already fixed — that future events
are determined and inevitable regardless of human action or deliberation.

## Core Argument
1. Every proposition about the future is either true or false (bivalence)
2. If it is true now that event E will occur tomorrow, then E will occur
   necessarily
3. Therefore, the future is fixed and deliberation is futile

## Key Distinctions
- **Logical fatalism**: derives from bivalence and the principle of
  excluded middle applied to future contingents
- **Theological fatalism**: derives from divine foreknowledge — if God
  knows the future, it must be fixed
- **Causal determinism**: distinct from fatalism; holds that future events
  are caused by prior events, but does not claim deliberation is futile

## Dependencies
- Builds on: [Eternalism](eternalism.md) (the block universe supports
  fatalism by treating future events as equally real)
- Builds on: [Tense Logic](tense-logic.md) (formal treatment of future
  contingent propositions)
- Leads to: [Time Travel](time-travel.md) (if the future is fixed,
  backward time travel raises consistency paradoxes)

## Key Thinkers
- Aristotle (the sea battle argument, De Interpretatione Ch. 9)
- Richard Taylor (modern formulation of logical fatalism)
- Peter van Inwagen (responses to fatalist arguments)

## Open Questions
- Does the growing block theory escape fatalism while preserving an
  objective present?
- Can libertarian free will be reconciled with the logical argument
  for fatalism?

## Sources
- Aristotle, *De Interpretatione*, Chapter 9
- Taylor, R. (1962). "Fatalism." *The Philosophical Review*, 71(1).
- van Inwagen, P. (1983). *An Essay on Free Will*.
```

---

## Compendium Builder Skill

A meta-skill that generates new compendium skills using the REPL approach.

### Workflow

1. **Scope definition** — User provides a topic; the builder agent researches the broad landscape and proposes a structure (entry index + dependency graph)
2. **User approval** — User reviews and adjusts the proposed structure
3. **Entry generation (REPL loop)** — For each entry:
   - **Read**: Load source material relevant to this concept
   - **Evaluate**: Extract key information, cross-references, distinctions
   - **Print**: Write the entry in compendium format with dependency links
   - **Loop**: Move to the next entry, informed by what's already been written
4. **Recursion** — Sub-agents handle individual entries or sub-topics in parallel (async when supported)
5. **Assembly** — Compile all entries, generate `connections.json`, write `SKILL.md` with the complete index and dependency graph
6. **Output** — A fully formed Skill directory, ready to install

### Key Guardrails (from RLM paper)
- Recursion limited to one layer deep (builder → entry generators)
- Scope boundaries enforced by the approved structure map
- Entry quality checks before adding to the compendium
- Cost controls on total REPL iterations per entry

---

## Two Usage Modes

### Mode 1: Compendium as Deliverable
Generate a compendium Skill that a human expert reviews, edits, and uses as a personal reference. The physical notebook equivalent from the video, but in a format that also makes the agent smarter.

### Mode 2: Compendium as Agent Expertise
An agent builds a compendium Skill as a prerequisite to executing a complex task. The compendium becomes an intermediate knowledge artifact — a structured understanding of the problem domain that the agent then navigates using REPL primitives during task execution.

In both modes, the same Skill structure is produced. The difference is who the primary consumer is.

---

## Implementation Notes

### For Claude.ai (Custom Skills)
- Package the compendium directory as a zip file
- Upload via Settings > Features
- Available on Pro, Max, Team, and Enterprise plans
- Individual to each user (not shared org-wide)

### For Claude API
- Upload via `/v1/skills` endpoints
- Workspace-wide availability
- Requires beta headers: `code-execution-2025-08-25`, `skills-2025-10-02`, `files-api-2025-04-14`

### For Claude Code
- Place the compendium directory in `~/.claude/skills/` (personal) or `.claude/skills/` (project)
- Automatically discovered — no upload needed
- Full network access available for source material retrieval during generation

### Constraints to Plan For
- No network access in API runtime (source material must be bundled or pre-fetched)
- Skills don't sync across surfaces — manage separately per platform
- Entry files should be concise enough to be useful when loaded individually (aim for under 2K tokens per entry)
