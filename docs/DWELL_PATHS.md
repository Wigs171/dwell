# Dwell Paths — the directed reader (goal-path traversal × gates × dream)

> **PLANNED — 2026-06-30.** Companion to `DWELL_CREED.md` and `DWELL_ROADMAP.md`.
> The creed already names this primitive: `tutorial = form: steps × traversal:
> goal-path × gate: quiz`. This doc specs the **`traversal: goal-path × gate`**
> half — a curated ordering through the graph — and the **dream** extension that
> carries the *same* machinery from tutorials to interactive fiction.
>
> **The one-line test (still the creed's):** a Path is a **recipe, not a vault** —
> an ordered subset + a lens + a traversal policy, rendered live over the untouched
> graph. If a "path" ever bakes content into the vault, it's the anti-pattern.

---

## Thesis
Dwell's transform axes (Form × Level × Lens × Length × Voice) all reshape a *given*
node. A **Path adds the one missing axis — the itinerary**: *which* nodes, in *what*
order, and *how* the reader is carried between them. That completes Tier-2 ("re-
traverse the graph"). One Path system spans a single continuum from **education to
fiction**; the only thing that moves is a dial.

A Path is a **directed** reading mode layered *on top of* the existing free-wander
reader. Free-wander stays exactly as it is — reactive, node-to-node, reader-driven,
no destination. Path mode adds a spine, gates, connective tissue, and an engine that
generates ahead of the reader. Same reader, a mode switch.

---

## The dial: Convey → Dramatize (one system, both domains)
Every Path has a **generative mode** — a continuum with three named detents (store
as `mode: "convey" | "scenario" | "dramatize"`, or a 0..1 `dream` float):

| | **Convey** (education) | **Scenario** (hybrid) | **Dramatize** (fiction) |
| --- | --- | --- | --- |
| A node is… | payload — render faithfully | a case to enact | **canon** — a constraint a scene must honor |
| Interstitials are… | transitions, recaps | mini-cases | the plot itself ("what happens") |
| Variance | low, cached, stable | medium | high, fresh each run (replayable) |
| Continuity carries… | progress, mastery | scenario state | story state, revelations |
| Emergence budget | none | bounded detours | **subplots may bloom** |

The middle isn't a compromise — a *scenario* path (dramatize a concept: role-play an
angry customer, walk a branching ethics dilemma) is fiction machinery serving
education as **simulation-based training**. Building the dream engine upgrades
tutorials too. **One dial correlates node-role + variance + emergence budget**, so
"turn it up" has a single, coherent meaning across the whole span.

---

## Architecture — fixed spine, fluid corridors
The resolved model (the game-design pattern: a fixed critical path with fluid,
optional side-content between required objectives).

### The spine is frozen; only the corridors are alive
At **session start** the spine — the ordered **gates** — is committed **once** (either
authored, or agent-rolled) and then does **not** move for that session. Steering never
re-plans the destination; it only reshapes the corridor the reader is in. Want a
different journey? Start a new session and re-roll the spine.

This is the load-bearing simplification: the **horizon buffer**'s top resolution (the
arc skeleton) is *immutable per session*, so there is no mid-session re-planning of
the destination to reason about. Two clean replay axes fall out: **same spine, endless
corridor variation** (authored content), or **re-roll for a new spine** ("surprise
me").

### Gates guarantee arrival
A gate is a hard checkpoint the reader must pass. Freedom is **in depth and time,
never in direction** — you can linger, explore, take the scenic route, but the
plot/curriculum advances *only* through gates. (This is what makes "they actually
learn the job" rigorous rather than hopeful, and what stops fiction from wandering
forever.) Gate types:

| `gate.type` | Meaning | Pass condition |
| --- | --- | --- |
| `beat` | narrative-mandatory (story) | auto on arrival — you can't "fail" a beat, only pass through it |
| `read` | seen it | page viewed |
| `self-report` | learner attests | "I've got it" |
| `quiz` | retrieval-practice check | ≥ threshold on auto-generated items (reuses the existing quiz system) |
| `task` | demonstrated action | in-app demonstration (tool-wired vaults) or attested |

**Quiz gates need no manual authoring:** items are drawn from the anchor node's
**claims** (the `enrich` claims layer). A **failed gate is a strong dwell signal** →
the corridor expands with remediation (re-explain from a new angle), then re-tests.
Gate-failure and dwell-expansion are the *same* mechanism (below). A per-path
`canon_strictness: strict | loose` decides whether gates *block* (strict — SOPs,
established IP) or are *advisory* (loose — exploratory tutorials, sandbox fiction).

### Corridors — the confluence frame is the synthesis unit
Between gates are **corridors**, and this is where raw material becomes a *format*. A
node is an **ingredient, not a page**: today's engine can only render a page from one
node's facets, but the page a tutorial (or story) actually wants — "to process a refund,
confirm the policy applies, then locate the order and click refund" — is synthesized
from **two or more bracketing anchors + the goal**, and lives in *none* of them. That
synthesized page is the **confluence frame**. Nodes are ingredients; the confluence is
the dish. It is the **core new primitive of Paths** and the CREED thesis at a new scale
— the transform that morphs substrate into a chosen format. (The "imagination" in
*generate a path partly through imagination* is exactly this: on-manifold generation —
the anchors are real, the connective synthesis is composed.)

**This is substance, not plot — do not conflate them.** *Expository substance* — enough
material, synthesized, to actually develop the idea — is load-bearing and **cannot be
skipped**, because a single node rarely carries enough alone. The confluence frame
supplies substance. Plot, we learned, the reader does **not** reliably confabulate: pages
each restated the standing problem and nothing ever *happened*. So since 2026-07-04 the
system supplies plot too — **THE PLOT**: one planning call at path commit turns the
spine's gates into a through-line (a premise — a drive, a counter-pressure, what hangs on
the outcome — plus ONE causally-chained turn per gate, mined from each gate's
tension-bearing sections: relationships, story hooks, limitations, open questions).
Narrative forms *enact* each turn ("write the scene in which X"); expository forms
*arrive* at it ("carry the journey to its next development: X"). Landed turns accumulate
as free story memory ("already happened"). The whole journey state rides the render
prompt as one flat `<journey>` data block explicitly marked as **silent context** —
never quoted, named, or summarized on the page (data without a silence contract gets
narrated; see MERCURY_PROMPT_GUIDE's render-frame lessons).

**How many confluence frames? A material-driven floor; tempo is the modulator above it.**
- **Floor (material):** however many frames the exposition needs to carry the synthesis
  from one anchor's contribution to the next, *for this format + goal*. A tight, obvious
  pair = one bridge; a conceptual leap a tutorial must scaffold = several. You can never
  drop below "enough to actually explain."
- **Ceiling (tempo):** authored + adaptive, sitting *above* the floor. **Authored** sets
  a corridor's baseline (dwell here, montage that). **Adaptive** modulates via the
  reader-behavior signals Dwell already collects: when a reader **dwells** (rereads,
  fails a gate, lingers), the engine **deepens the corridor** — more synthesis, another
  worked example (convey), more atmosphere or a small beat (dramatize) — *without
  advancing to the next gate*. **Dwelling ≠ progress; it's zoom, not travel.** Skimming
  compresses toward **montage** (one frame swallowing several anchors, "weeks passed").

Tempo scale (the ceiling): `brisk` · `measured` · `immersive` · `montage`
(compressive). Free-wander mode has no corridors and no confluence — it's node-to-node.

### Lookahead — near kept, far deferred
A confluence frame **must** know its two bracketing anchors + the goal to synthesize the
bridge. That one-step lookahead already exists in the engine: `pending_plan` + `hint_for`
lean the current page toward the predicted next. **Keep it** — it's cheap, load-bearing,
and it's all the "generate ahead" a corridor needs.

The **withholding** (build toward the next anchor without stating it — "plant these
seeds, end on tension") rides on that *near* lookahead: one beat ahead is enough for
setup-and-payoff, which in convey mode is just pedagogical scaffolding (build intuition
before the concept lands).

What survives of the once-planned three-resolution **horizon buffer**: only its top
layer — the **arc skeleton**, i.e. the frozen spine of gates — is in scope now (it's
just the path itself, immutable per session). The lower layers — **committed intent**
(next beats as one-line what-happens) and **committed prose** (several keyframes written
ahead, a "director clock" holding hidden future) — exist to manufacture *global plot*,
which the reader supplies for free (above). So they are **deferred to a later phase** —
earn them in for the fiction end, where global arc-tracking genuinely matters, not
before. Phase 0 through the mid phases run on the near lookahead alone.

### The weaver + resolution tracker — bounded emergence
A Path is a **braid of arcs**, not a line. Each arc is first-class:
`{ role: main | sub, anchors/gates, tension-curve, resolve_by }`. The **main arc**
spans the whole path; **subplots** open and close within it and must *feed* the main
climax.
- The **weaver** schedules which arc gets the next scene (rising-tension interleaving,
  "cut away at the cliffhanger").
- The **emergence budget = the dream dial.** Low → no subplots (lean convey corridors).
  High → the weaver may **spawn** emergent subplots in a corridor.
- The **resolution tracker** pins every opened thread a `resolve_by` deadline (the next
  gate, or the finale) and forces it closed before the reader passes. So emergence
  blooms exactly as the dial rises, and it can *never* dangle a thread or delay
  arrival — **wild in the middle, tidy at the seams.**

This is the education insight too: the **"job" is the main arc**; sub-tasks are
subplots that each open → practice → checkpoint → close, converging on competence. A
curriculum *is* a braid.

### The dream — canon-faithful generation (dramatize mode)
When the dial is up, a node is **canon** and the rendered page is a **scene that uses
it**, not the node re-pitched. Faithfulness comes from conditioning, not free rein:
1. **Canon context pack** — the in-scope anchor(s) + their wikilinked neighbors
   (relationships), so characters/places stay consistent.
2. **Continuity state** — a running story/skill state threaded through frames (who's
   where, what's revealed, who's dead / what's been mastered). *This is the one
   genuinely new persisted primitive* — reading-memory, extended for narrative.
3. **Attractor pull** — the next committed keyframe biases generation toward it (the
   existing "tween toward next page" mechanism), so improvisation still arrives.
4. **Canon-check** (optional) — reuse the **grounding engine** (`ground`) to flag scene
   claims that contradict node facts (a dead character speaking; anachronism via
   `enrich` temporal anchors).

**Does a dream become canon?** By default **no** — a run is an ephemeral
**playthrough** (`_meta/playthroughs/…`), never world-canon; the world stays a stable
stage (CREED-pure). Promoting a dreamed event back into the vault is an **explicit
Learn/ingest action, author-in-the-loop** — never a read-time write.

---

## Three render types
Path mode extends the renderer's two shapes with a third:

| Type | Bound to | Rendered as |
| --- | --- | --- |
| **anchor render** | one node | the node re-pitched (convey) or dramatized (a scene using it as canon) |
| **beat render** | a gate | the keyframe payoff — the moment / the concept lands |
| **confluence render** | **≥2 anchors + the goal** (lives in *neither* node) | the **synthesis unit** — a tutorial step / story beat / FAQ answer composed *across* the bracketing anchors toward the goal; often the *substantive* page, not filler |

The **confluence frame is the core new primitive** (a page whose material is drawn from
several anchors + the goal, not extracted from one node) — it's where
wiki-data-becomes-format. Anchor/beat renders reuse the existing `/repage` +
`page_renders` machinery keyed by the Path's lens; the confluence render extends it:
`PagePlan` gains a `bridge` mode carrying `anchors: list[str]` + the goal, `material` =
selected chunks from the bracketing anchors, and `cache_key` keys on
`{anchor-set, goal, lens}`.

### Arc-aware forms (shipped 2026-07-03)
On a path, the FORM axis composes with arc position via `_FORM_PHASES`, in two kinds:

- **Structurally arc-shaped** (`story`, `tutorial`, `case`, `interview`, `debate`,
  `epistolary`, `chronicle`): beginning/middle/end are different KINDS of page — the
  first beat opens/orients, middle beats develop/build, the last lands/consolidates
  (tutorial adds a `dwell` = practice phase).
- **Continuity-modulated** (`guided`, `qa`, `brief`): the grammar stays position-free
  (a brief always leads with its bottom line) — phases only stop each beat from
  cold-opening like page one (no re-orientation, no re-asked entry questions) and let
  the final beat speak for the whole journey (guided's closing wide-view, qa's closing
  questions, brief's net assessment). `article` stays phase-free on purpose: the
  neutral baseline.

Phase notes land on spine **gates only** (arc == "k of n"); corridor drift and tweens
get none (they're motion, not beats) — tweens instead speak the form's grammar in
miniature via `_FORM_TWEEN` (a Q&A tween is ONE bridging exchange; a story tween is a
beat of the same scene).

### Tween pool + certainty-gated choice (2026-07-03, from live testing)
A tween's material comes from a **corridor pool**: the departing node's unread facets
+ the arriving gate's BACK half (`_tween_pool`) — sourcing only from the departing
node was a bug (small-page vaults exhausted it on the beat page and NO tween ever
fired, in any form). The beat page then renders only the gate's FRONT half when the
approach ran (`_plan_at` trim + full-read marking on commit), so corridor + beat cover
the node exactly once between them. **Choice is certainty-gated:** while the tween run
is in motion, "where next?" offers the normal dwell/drift choices; once the run is
spent the next gate is the ONLY branch (a beat can't be wandered around). **Lean is
certainty-gated the same way** (`plan.next_locked`): the page whose successor is
forced (the last tween of a run) may end leaning into the named arrival — a promise
that can't break — but never pre-tells its substance; mid-run tweens close with the
pull strong and the arrival unspent; keyframes/drift close with the gate's pull felt
but unnamed-as-next (the reader may drift first). **The close is position-aware for ALL forms:** every beat
before the last holds the goalward line open, but the FINAL gate flips to a landing
close — without this every path ended without an ending, and the mid-journey close
directly contradicted any "land it" phase. `plan.key()` folds `arc` in when a goal is
set so the same node at a different beat caches separately; `form_id` hashes
directive+example+phases so edits bust stale pages. This is the tutorial recipe made
real: `form: tutorial × traversal: goal-path` — the first gate promises what you'll be
able to DO, middle gates each add one move standing on the last, dwells are practice,
the final gate consolidates.

---

## Data model
A Path is a tiny reference artifact (no content — CREED-safe):

```jsonc
// _meta/paths/<id>.json
{
  "id": "refund-processing",
  "kind": "path",
  "title": "Processing a Refund",
  "goal": "A new hire can process a standard refund end-to-end.",
  "audience": "new customer-service hire",
  "mode": "convey",                 // convey | scenario | dramatize  (the dial)
  "canon_strictness": "strict",     // strict = gates block; loose = advisory
  "tempo": { "default": "measured", "adaptive": true },
  "lens": { "form": "guided", "level": "new-hire", "voice": "onboarding-mentor" },
  "author": "agent",                // human | agent
  "provenance": { "origin": "authored | session-rolled", "vault_rev": "<hash>" },
  "arcs": [
    { "id": "main", "role": "main", "gates": [
      { "id": "g1", "anchor": "refund-policy", "gate": { "type": "read" } },
      { "id": "g2", "anchor": "locate-order",  "gate": { "type": "task" } },
      { "id": "g3", "anchor": "issue-refund",  "gate": { "type": "quiz", "threshold": 0.8 } },
      { "id": "g4", "anchor": "edge-cases",    "gate": { "type": "quiz" } }
    ]},
    { "id": "fraud-check", "role": "sub", "resolve_by": "g3", "gates": [ /* … */ ] }
  ],
  "created": "…", "updated": "…"
}
```

A **playthrough** is per-reader session state (extends reading-memory; ephemeral
horizon is a regenerable cache, not source of truth):

```jsonc
// _meta/playthroughs/<path-id>/<run-id>.json
{
  "path": "refund-processing",
  "cursor": { "gate": "g2", "corridor_pos": 0.4 },
  "gates_cleared": ["g1"],
  "continuity": { /* story/skill state carried scene → scene */ },
  "open_threads": [ { "arc": "fraud-check", "opened_at": "g1", "resolve_by": "g3" } ],
  "seed": 12345
}
```

---

## Authoring modes
Same two modes as any curated artifact, with the session-start freeze:

- **Hand-author** — the vault-creator's SOP / plotline editor: pick nodes from search /
  the popular-nodes list / the graph, order them into the spine, set the lens + mode +
  gates. Saves a `_meta/paths/*.json`.
- **Agent-generated (once at session start, then frozen)** — the default, especially on
  a **cold open** where the reader knows nothing about the vault yet. Runs the **spine
  generator** (next section) and commits the result. A user goal is optional — it just
  sets the walk's heading explicitly; with no goal the generator wanders and *names what
  it found*.

---

## The spine generator — wander → narrativize (the diversity engine)
The generator's job is **not** "pick the important nodes for the goal" — that prompt,
run over a centrality-ranked digest, opens on the top hub and reproduces the same arc
every session. The failure is **determinism, not centrality.** So invert the usual
assignment: **diversity comes from a stochastic graph walk; coherence comes from the
LLM.** The model is the *worst* place to source variety (it converges on the canonical)
and the *best* place to check that a thread reads as an arc and to name it.

**The unit.** An arc = *a coherent thread between two sampled endpoints.* On a cold open
this runs **path → goal**: wander first, then have the model **title what the walk turned
out to be about** ("this thread is really about how energy moves through a cell"). A
user-supplied goal is the *same* mechanism with the **heading set explicitly** instead of
sampled — "no direction" is the default, "I have a direction" the optional override, one
code path.

**Pipeline:**
1. **Sample the endpoints** (start + arrival) — **sample, never argmax.** Centrality
   stays a *positive* weight (hubs are frequent because they earn it), discounted by
   recent use (`_surprise_seed` already does `centrality − k·seen_count` + random-pick;
   generalize it to pairs). No hub penalty — see *On hubs*.
2. **Route between them by theme, not hops** — walk real edges, ranking each step by
   semantic continuity toward the arrival (`_rank_candidates`: `heading =
   blend(cur_vec, dest_vec)`, `cos` + `LINK_BONUS` − visited), **Boltzmann-sampled**
   (temperature τ), not argmax. Theme routing (vs shortest-hop) is what stops hubs being
   *mechanically* over-used: shortest paths funnel through high-betweenness nodes;
   semantic routing includes a hub only when it's genuinely on-thread.
3. **Keep the most arc-like of k candidate walks**, then a **light LLM pass**
   narrativizes: trim incoherent jumps, maybe reorder, write the title/goal.

### The diversity engine — a diffusion in the vault's latent space
"Every session collapses to one arc" is the same problem joint-embedding models face
("every input collapses to one point"), and the same regularizer family transfers.
Safety principle: **noise in the continuous latent, projection onto the graph manifold**
— perturb the *intent* (seed, heading), but every realized step **snaps to a real node.**
The graph is the manifold, so noise chooses *which* coherent arc, never *whether* it's
coherent, at any noise level.

| Anti-collapse tool (SSL / JEPA-family) | Equivalent here |
| --- | --- |
| latent/input Gaussian noise | perturb the **seed point** (fuzzy start-ball) + **heading** |
| temperature / stochastic sampling | **Boltzmann-sample** each step (τ), not argmax |
| VICReg variance hinge | a **floor on the spread of recent arcs** → raise ε if it drops |
| VICReg covariance / decorrelation | **repel** the new heading from the recent-arc centroid + vary the **angle** |
| on-manifold constraint | every step **snaps to a real node** (coherence is structural) |

Two distinct jobs:
- **Noise (open-loop)** — passive variety: a different arc each run in expectation.
- **Variance floor + centroid repulsion (closed-loop)** — the actual **anti-collapse
  guarantee**: it *measures* whether recent arcs are clustering and pushes back (the
  direct VICReg analog). This is what stops the slow drift back to one attractor that
  open-loop noise alone can still suffer.

**Anneal the noise within a walk** (a diffusion schedule): high ε early (explore the
direction), decayed late (commit to a coherent landing) → arcs that **open adventurously
and resolve cleanly** — a good narrative shape, free.

**Implementation nuance (build-real):** do **not** add raw ambient Gaussian noise — in a
~384-dim embedding a random Gaussian is near-orthogonal to every vector (concentration of
measure) and doesn't map to the TF-IDF fallback space. The on-manifold way to inject the
same thing is to **blend toward a randomly-sampled real node**: `heading =
space.blend(heading, random_node_vec, ε)` — exactly what `apply_steering` does with a
user's steer, but toward a *random* attractor.

Conceptually this makes spine generation a **diffusion in the vault's own latent space**
(seed a noised point → "denoise" by walking toward coherent neighbors → land on an arc) —
the *same* noise→denoise family Mercury runs at the prose level. Path selection and page
rendering become one idea at two scales.

### On hubs (settled)
**No hub penalty, anywhere.** Centrality is a **positive** signal. A hub is central for a
real reason — a character who overlaps many places, a location where the history happens —
and excluding it produces conspicuously-wrong arcs (a WWII history that never mentions the
war). The goal is to **vary the hub's *role*, not its *frequency*.** The same central node
is the **protagonist** in one arc, a **mentioned force** in another, the **foil** the arc
pushes against in a third; the same location is **setting**, **origin**, or
**destination**. "Often but **not always**" comes from the **recency discount**
(session-relative), never a permanent down-weight.

### The coherence governor
Randomness lives in *which* thread, never in whether steps connect: steps follow only
**real edges**; the **heading is fixed for the whole walk** (one thread, not a drunk
stagger); a **similarity floor** rejects jarring adjacencies; **length is bounded**; and
the LLM has a **veto** on any arc that doesn't hold together.

### The angle axis (decorrelation)
Even from the same endpoints, one graph yields distinct coherent arcs by *angle* —
**chronological** (if `enrich` has dates), **causal / dependency** (typed edges),
**contrast / debate**, **biographical**, or **follow-a-surprising-link** (seed one
unexpected-but-real hop from `missed_connections`). Sampling the angle decorrelates arcs
along an axis *orthogonal* to position, so they differ in kind, not just in jitter.

### Goal override + gap → Learn
A user goal sets the heading explicitly (and can bias endpoint sampling toward the goal
region). While scoping, if the goal needs a concept the vault lacks (a broken wikilink, a
thin/missing node), flag it and offer to run **Learn/ingest**, then re-generate — reusing
the topic-resolution + gap detection already built for prompt-driven ingest. Paths thus
*drive* vault growth.

### Knobs
**ε** (noise scale = adventurousness; ≈ the existing `wander` slider) · **τ** (step
temperature) · **variance-floor + repulsion strength** (the collapse guard) · **angle** ·
optional **anneal schedule**. All default to *sampled / middle* so a cold first-open just
wanders well. Reuses: `_surprise_seed`, `_rank_candidates`, `space.{blend,cos,neighbors,
vec}`, `apply_steering`/`steer_vec`, `missed_connections`, reading-memory (recent-arc
memory), `enrich` (edges / temporal / angles).

---

## Dependencies (what this consumes / reuses)
- **`enrich` sidecar** (`DWELL_ENRICH_PLAN.md`) — the planner's substrate (typed edges,
  temporal anchors, claims, terms, salience). Upgrades planning from "good" to
  "rational"; degrades gracefully to wikilinks + embeddings + level tags without it.
- **Form axis** — a Path's per-node lens (`form = guided` = the tutorial shape).
- **Voices** (`DWELL_VOICES.md`) — narrator / POV; a character node can *be* a voice
  (first-person, unreliable narrator).
- **Retrieval-practice quizzes** — competency gates + fail→remediate→retry.
- **Mercury + `/repage` + `page_renders`** — the render engine; anchor/beat renders ride
  the existing in-place re-pitch.
- **Reading-memory** — extended into continuity + playthroughs; launch modes → *Resume a
  playthrough* / *Surprise* (improvise a spine).
- **Speculative prefetch** — extended into the rendering clock.
- **Centrality, grounding (`ground`), missed-connections** — planner priors; canon-check;
  "meanwhile" / subplot seeds.

---

## Phasing (cheapest-provable → richest)
0. **Path runtime** — the `_meta/paths` model + path-mode traversal walking a **frozen
   spine** with a global lens; gates = `read` only; **no tweens yet**. Hand-write one
   JSON; proves spine + gate + lens end-to-end on the existing `/repage`.
1. **Authoring** — hand-author UI + agent-plan-once-at-session-start (spine generation,
   gap→Learn).
2. **Corridors + tempo** — the confluence render type; the horizon buffer (3
   resolutions) + attractor pull; authored + adaptive tempo (dwell-expands-in-place,
   skim→montage).
3. **Competency gates** — quiz (via retrieval-practice + `enrich` claims) / task /
   self-report; fail → remediate → retry; `canon_strictness`.
4. **Dream up** — dramatize mode (node-as-canon scenes), continuity/story-state,
   canon-check (reuse `ground`), emergent subplots (weaver + emergence budget +
   resolution tracker).
5. **Reach** — branching gates (DAG / choose-your-own), curricula (paths of paths),
   contextual entry points, and the business layer (approval/freshness/pin, provenance,
   telemetry, static export).

---

## Open questions
- **Competency-gate authority** — quiz vs. demonstrated task vs. self-report as the
  *default* gate for auto-generated tutorial spines? (Quiz-from-claims is the most
  automatic; task needs tool-wiring.)
- **Emergent subplots** — planned up front only, or may the weaver spawn one mid-run
  when it notices an unresolved tension (a character introduced but unused)? Emergent
  is magical but leans hard on the resolution tracker.
- **Playthrough persistence** — save every run, or only on request? Where does story
  state vs. skill-mastery state live relative to reading-memory?
- **Static export** — a portable recipe (needs the vault to render) vs. a baked,
  version-pinned HTML/PDF for compliance/audit (breaks single-source freshness — but
  auditors need frozen artifacts).
- **Generator defaults** — ε / τ / variance-floor / recency-window want empirical tuning
  per vault size (a 40-node demo behaves differently from a 400-node corpus); expose the
  ε / τ / angle knobs and ship sensible middles.

---

## Related
- Creed / roadmap: `DWELL_CREED.md` (the `traversal × gate` formula), `DWELL_ROADMAP.md`.
- Substrate: `DWELL_ENRICH_PLAN.md`. Engine: `MERCURY_PROMPT_GUIDE.md`. Personas: `DWELL_VOICES.md`.
- Memory: [[project_the_current]] (Dwell project log) · [[project_dwell_education]]
  (education-mode + vault economics — Paths is the "course" over the neutral "textbook").

*Authored 2026-06-30. Revised 2026-07-01 — the confluence frame is the **synthesis
unit** (material from ≥2 anchors + goal, lives in neither node), its count is
material-driven with tempo as the modulator, the near one-step lookahead is kept and the
far horizon buffer deferred, and coherence is scoped to *plot* (reader-supplied) vs
*substance* (confluence-supplied). PLANNED — nothing built yet. Update as slices land.*

---

## StreamDiffusion V2 borrowings (2026-07-03, arXiv 2511.07399)

The creed borrowed Dwell's frame from StreamDiffusion v1 (pages = keyframes, tweens =
in-between frames). V2's live-streaming upgrades map onto our tested pain points:

1. **Canon sink** (← sink tokens in the rolling KV cache). The rolling window (tail +
   recap) drifts: tested story paths rotated protagonists every beat. Fix: a small
   ESTABLISHED ledger pinned into every path page beside the goal — figures/elements
   extracted mechanically from rendered pages, first-seen order, capped — never rolled
   out. Pages reuse established identities instead of inventing replacements.
2. **Distance-aware corridors** (← motion-aware noise controller). "Motion" = semantic
   distance between adjacent gates. Distant gates get more tween frames, near gates
   fewer — density per corridor from embedding similarity, $0.
3. **Narration-clocked prefetch** (← SLO-aware scheduling). The karaoke timeline IS the
   per-frame deadline: the next page must be ready when the current one finishes
   narrating. Under deadline pressure, degrade gracefully — prefetch at
   reasoning_effort=low (our analog of cutting denoising steps) rather than stutter.
4. **Corridor pipelining** (← pipeline parallelism). On a firm spine the remaining
   sequence is known: speculate 2 pages deep on paths (tween k+1 while k narrates),
   bounded because our "GPU" is API dollars and steering invalidates speculation.
5. **Narrate-while-diffusing** (← sub-0.5s time-to-first-frame). Start TTS once the
   opening of a refining page stabilizes across frames. Riskiest (diffusion revises
   early text); gated behind a stability check.

### Beat functions — the dramatic spine (2026-07-03, from live listening)
Paths repeated ONE problem for 12 pages (the sqrt-2 quandary; the ledger and the tide)
because the prompts instructed it: every page was told to "end with the goalward line of
thought still open" (= re-invoke the same tension), the story middle phase said "deepen
what is at stake" (= wallow), and nothing required the situation to CHANGE. Fix: each
gate now carries a DRAMATIC FUNCTION from a compressed story circle (three-act /
hero's-journey shape) scaled to the spine length — ESTABLISH-THEN-DISRUPT (the ONLY page
allowed to introduce the problem) → FIRST ENGAGEMENT (act on it, produce a RESULT) →
THE TURN (the problem changes shape) → THE COMMITMENT (the key/price) → RESOLVE AND
GROW. `PathNavigator._beat_job(j)` → `plan.beat`. *(Since 2026-07-04 the beat functions
are the PLOTLESS FALLBACK only: THE PLOT plans one concrete turn per gate up front, and
the turn IS the page's task — see "This is substance, not plot" above. The abstract
beat shapes proved insufficient alone — a shape with no assigned content still renders
as description.)* Tweens carry the CONSEQUENCE of what just happened. Form-neutral: for
a tutorial the same circle reads as engage → misconception breaks → mastery. Verified
live (nonfiction story path): adjacent-page content overlap ~2%, every page ends moved
on, events actually occur. Path cache re-keyed g2→g3 (later g3→g4, the frame rebuild).
