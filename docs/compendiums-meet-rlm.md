# AI-Generated Compendiums via RLM/REPL: Connecting the Concepts

## The Core Idea

An AI agent that can generate **compendiums** — systematic, comprehensive collections of information and analysis on any given topic — enabling AIs to more efficiently become experts on topics they need to execute against or deeply understand.

The main blocker has always been **retrieval and synthesis of complex, interrelated information**. The RLM (Recursive Language Model) paper's REPL approach offers a direct solution.

---

## Why Previous Approaches Failed for Compendium Generation

### RAG
- Can find semantically similar chunks, but **can't follow logical threads** across sources
- A compendium on transcendental arguments requires tracing connections from medieval philosophers → Kant's deductions → analytic philosophy — that's relational, not just keyword proximity
- Chunking strategies would need to vary per source type, and a compendium draws from many source types

### Naive Context Stuffing
- Source material for a comprehensive compendium easily exceeds useful context limits
- Context rot degrades performance well before the window fills, especially given the **high task complexity** of cross-referencing and synthesizing multiple sources

### Summarization / Compaction
- Lossy by nature — discards the **nuanced details** that make a compendium valuable vs. a surface-level overview
- Agent drifts off-topic as accumulated summaries lose fidelity
- A compendium's whole point is preserving important specifics, which summarization actively works against

---

## Why REPL Is a Natural Fit

### The Dependency Graph Alignment

A well-structured compendium **is** a dependency graph:
- **Nodes** = individual entries/concepts
- **Edges** = how those concepts reference, build on, or relate to each other

This maps directly to the RLM paper's mental model for complex documents. The REPL approach was designed to build and navigate exactly this kind of structure.

### How REPL Primitives Map to Compendium Creation

| REPL Primitive | Compendium Creation Role |
|----------------|--------------------------|
| **Read** | Ingest source material — articles, papers, encyclopedia entries, books |
| **Evaluate** | Apply programmatic functions — extract key concepts, identify cross-references, classify information by sub-topic |
| **Print** | Return structured entries back to the system, building the compendium incrementally |
| **Loop** | Continue until all identified sub-topics are covered and cross-references resolved |

### The Recursion Factor

This is where it gets powerful for compendium generation:

1. **Top-level agent** identifies the scope and major categories (mirrors the "find an overview first" step)
2. **Sub-agents** receive handoffs to do deep dives on individual entries/sections
3. Sub-agents can **cross-reference other sections** through REPL primitives as needed
4. Results synthesize back up into the complete compendium structure

This essentially automates the entire compendium creation workflow:
- Research the broad scope → identify sub-topics → systematically work through each → make connections along the way

---

## Two Use Cases

### 1. Compendium as Deliverable (Human-Facing)
Generate a structured, comprehensive reference document on any topic for a human to study and reference. Replaces the manual notebook-based process with AI-assisted research and synthesis while maintaining the systematic, comprehensive qualities that make compendiums valuable.

### 2. Compendium as Intermediate Representation (Agent-Facing)
When an AI agent needs expertise on a topic before executing a task, it builds a **compendium-style dependency graph as its first step** — not as a document for humans, but as a structured knowledge artifact it then reasons over. This is fundamentally different from stuffing context and hoping for the best. The compendium becomes the bridge between raw sources and reliable task execution.

---

## Considerations and Limitations

### Scope Definition Is Critical
- The quality of the compendium depends heavily on how well the initial prompt defines scope and structure
- Poorly scoped requests could trigger expensive recursion loops (the 95th percentile cost spike from the RLM paper)
- Mirrors the video's advice: "find an overview of the topic so you know what you need to be looking for"

### Model Capability Requirements
- The RLM paper showed performance degradation between GPT-5 and Qwen 340B
- Compendium generation requires strong reasoning — small models likely won't produce quality results
- The synthesis and cross-referencing steps are especially demanding

### Not Every Topic Needs This
- Simple or narrow topics may be better served by a single-pass LLM response
- The REPL + recursion approach shines when:
  - The topic has **high internal complexity** (many interrelated sub-topics)
  - Source material spans **multiple documents/sources**
  - The goal is **comprehensive coverage**, not just a quick answer

### Guardrails Needed
- Recursion depth limits to prevent runaway costs
- Scope boundaries to keep sub-agents on topic
- Quality checks on individual entries before they're added to the compendium
- Async recursion (not yet tested in the paper) could parallelize section generation for large compendiums

---

## Next Steps to Explore

- Prototype a REPL-based compendium generator on a well-defined topic
- Test the "compendium as intermediate representation" pattern — have an agent build a topic compendium, then use it to execute a downstream task, and compare quality vs. direct execution
- Experiment with async sub-agent handoffs for parallel section generation
- Define a compendium schema/structure that works well as both a human reference and an agent knowledge artifact
