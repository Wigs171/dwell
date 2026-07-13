# THE HAND — one per-path authorial signature, drawn not asked for

*Design doc, 2026-07-11. Synthesized from a 3-lens design fan-out (card/palette,
sampling, planner-structure) + 3 adversarial verifications against dwell.py
(integration, tell-coverage, Goodhart). Status: PLANNED — nothing here is built.*

## The problem, measured

The humanlikeness instrument (StoryScope bridge, pinned rater, joint-encode)
puts Dwell at P(human) ≈ 0.54 — off the frontier-AI pole (0.00) but far from
the human band (0.98) — and FLAT across five engine generations while judge
craft rose 76→86 (r = +0.08: craft and humanlikeness are different axes). Our
measured tells clump into four cause families plus a meta-cause:

| family | top tells (signed SHAP) | verdict on levers tried |
|---|---|---|
| Mercury's figurative TEXTURE | STY_FIG_001 −0.94 · AGENT_ATTR_024 −0.51 · STY_FIG_004 | exhortation line: markers −30%, instrument FLAT, judge −3.8 → benched |
| plot TIDINESS | PLT_MOR_007 −0.81 · PLT_MOR_006 · EVT_SCH_002 | finale-page lever: feature UNMOVED both arms ("extended aftermath" is multi-page structure) |
| narratorial COMMENTARY | SIT_MET_303/501/102 | coda lever WON (−25pp); mid-story residual by-design open |
| REGISTER uniformity | STY_ALL_015 −0.42 · STY_ALL_016 | consistency itself is the tell — untreated |
| **meta: THE CLUSTER** | AI models cluster; humans are diverse | consistency machinery (staged −0.23, cards) makes it WORSE |

Two laws dominate everything measured: **exhortation fails where structure
decides** (three same-seed demonstrations), and **matching human per-feature
averages with one fixed frame would still be one fingerprint — a cluster**.
Cross-story VARIANCE is itself the target.

## The core abstraction

Every story is currently written by the same invisible hand: one prompt frame,
one sampling config. The engine already varies each story's emotional identity
(mood palette), people (cast cards), and narrator (voice card) — the missing
identity object is the AUTHOR.

**THE HAND: one seeded draw per path, from an isolated rng stream, of a compact
authorial signature — materialized downward as DATA at the layer that can
enforce each axis, never as instruction.** The same grammar that already works
three times over: randomness picks the signature, never the page; the planner
schedules it; the frame carries it; the cache keys it; a flag referees it.
Because the signature is drawn fresh per path from human-calibrated marginals,
cross-story diversity is produced by the same mechanism that treats the
families — not by a fifth lever.

## The axes (what one Hand specifies)

| axis | covers | materialized as |
|---|---|---|
| GRAIN (human cloth) | texture, Latinate flavor | 2–3 short public-domain excerpts in a disjoint SYSTEM channel ("this grain, not this content") |
| sampling profile | texture below the prompt | per-PATH temperature offset (±0.1 band) |
| register seam plan | STY_ALL_015 (uniformity) | per-turn shading slots from the planner (two poles, planned drift) |
| image ration | STY_FIG_001 placement | per-turn `texture:` slot — plain vs one-placed-flourish, mood-slot grammar |
| ending mode + budget | PLT_MOR_007/006, ambiguity | stamped on the FINALE GATE: mode draw + post-climax PAGE budget (planner data, not finale prose) |
| relationship quota | SOC_REL_007 | shapes cast-card generation upstream (bond kinds must differ) |
| scene-mode slots | PER_DIA_001 | per-turn spoken/interior/acted assignment |
| named intertext | vague-allusion tell | the vault's `sources:` named on-page (sanitize colons) — a structural advantage no frontier model has |

**Kept OUT of the draw (verified):** the coda exit-on-image win stays ALWAYS-ON
as the shared tail of every ending mode — re-drawing a shipped readability fix
gives back a measured win (verifier-refuted). Fact holds (canon ledger, cast
cards, syllabus) are ABOVE the Hand and untouched by it.

## Verified integration points (and the traps the verifiers caught)

1. **Isolated rng stream** — `hash(path_seed + "hand")`, never the shared
   `self.rng` (telling is drawn at ~1186 before the palette at ~1276; one extra
   draw on the shared stream silently shifts every later draw and invalidates
   every same-seed referee).
2. **The gate is threefold**: `dream ≥ 0.34` AND `form in _PLOT_ENACTED` AND
   `plot_kind == "narrative"`. Tutorials run at MID dream — dream-clamp alone
   does NOT protect them (verifier-refuted assumption). Low-dream factual
   tours: Hand collapses to neutral (no grain, no jitter, no slots).
3. **Exemplars live in a SYSTEM channel like voice exemplars** ("match their
   texture and rhythm, not their subject"), NEVER the journey frame — frame
   imagery replicates verbatim into prose (the p16 mood-gloss law). Add a
   mechanical 6-gram bleed gate to `_detect_flaws`.
4. **Channel order reality**: style channels come AFTER rules at the very end
   of the user message (voice recency) — the grain channel joins them there.
   Arbitration: the voice channel owns "diction, imagery, rhythm and stance";
   the grain channel must yield explicitly ("beneath the voice: the prose
   grain"). Precedence ladder (top wins): form/telling legality → fact holds
   (canon/cast) → syllabus (didactic) → VOICE → hand → mood.
5. **Sampling jitter is per-PATH only** (per-page jitter breaks cache-key
   correctness — temp isn't keyed) and must thread to BOTH render paths
   (`render()` ~3995 AND `_render_staged` ~4862) plus repairs, or the staged
   pipeline re-renders at a different temperature than the page it repairs.
6. **Hash the Hand id into `PagePlan.key()`** like `|md` mood / `|in`
   instrument.
7. **Nonlinearity is PARKED**: the "skeleton transformer" (permute gates before
   planning) has no carrier — `adopt_plot` maps numbered lines to spine
   positions; reordering breaks event↔gate alignment, corridor tweens, and the
   brief's own invariant. Needs its own design (frame devices, not permutation).

## Honest coverage gaps (recorded, not hidden)

- `SET_TIM_015` (temporal setting specificity — WHEN the story is anchored):
  no mechanism in any design. Candidate small axis later: a planner "era
  anchor" line. **Uncovered.**
- `AGENT_MOT_012` (motivation rendering): no mechanism reaches it. **Uncovered.**
- `STY_ALL_006` (genre pastiche): WATCH metric only — grain excerpts curated
  for sentence-grain (not period costume) or they move it the WRONG way.
- Mid-story SIT_MET_303 residual: by-design open (gate halves the mechanical
  tell; instrument flat).

## Build + referee plan (phased, cheapest falsification first)

**Phase A — traction probes (before building the sampler).** Same-seed pairs,
one axis flipped extreme-to-extreme with everything else frozen; the axis's
TARGETED instrument must move or the axis is benched before it ships:
- grain channel: plainest-cloth card vs no-card → STY_FIG_001 / STY_ALL_016
- sampling: temp −0.10 vs +0.10 → STY_FIG_001 (+ dispersion of style features)
- ending stamp: swift-budget vs lingering → PLT_MOR_007 (the failed finale
  lever proves prose can't move it; the page-budget stamp is the structural
  successor and this probe is its test)
- image ration: 0 vs max → STY_FIG_001; scene-mode: spoken-heavy vs interior →
  PER_DIA_001

**Phase B — assemble the card** from surviving axes; `DWELL_HAND` master flag +
an AXES bitmask (one flag, per-axis ablation — bench a harmful axis without
dismantling the card).

**Phase C — the distribution referee.** ≥20 paths/arm, same seeds, isolated
streams (baseline replays byte-identical). PRIMARY gate: P(human) distribution
shift + judge floor (no story below its baseline band) + human read
spot-checks (the anchor). Dispersion in feature space is SECONDARY — it is
Goodhart-able (degradation-spread maximizes it; verifier-refuted as primary).
Low-dream arm must be byte-identical (Law 5 regression check).

**Cost note:** the whole card rides the existing planner call (slot grammar) +
one system channel + a sampling parameter: zero new API calls per page.

## PHASE A RESULTS (2026-07-11, 10 arms × 4 configs, same-seed pairs) — ALL FIVE AXES SHOW TRACTION

| axis | targeted instrument | composite P(human) | verdict |
|---|---|---|---|
| GRAIN (human cloth) | STY_FIG flat; pastiche IMPROVED 1 story; zero 6-gram bleed | ctrl 0.39 → **0.59**, floor 0.00→0.25 | **earns slot** |
| sampling temp | — | tlo 0.35 ≈ ctrl; **thi 0.05 (CRATERS)** | **earns slot, one-sided: never hotter** |
| ending budget | **PLT_MOR_007 moved (first time all season)** + closure e1>e3 | e1 0.53 vs e3 0.39 | **earns slot; direction map needs calibration** (1-page finale ≠ "brief aftermath" to the rater) |
| ration slot-data | STY_FIG flat (scale too coarse) | **rpl 0.73 — best arm in the battery** vs rmx 0.30 | **earns slot** — assigned-plain is NOT the refuted free-choice class |
| scene mode | **PER_DIA_001 clean separation (3,3,3,3 vs 2,2,2,2)** + quoted% 13.4 vs 3.3 | 0.38 / 0.27 | **earns slot** — works exactly as a dial |

Notes that reshape Phase B: (1) STY_FIG_001's 0–4 scale saturates at 4 — the
composite P(human) is the sensitive instrument for texture axes; (2) the
`dream*0.30` sampling warm-up is now a SUSPECT for the texture tell (hotter =
more figurative slop, measured): Phase B should probe high-dream at base temp;
(3) the ending axis has traction but the rater's "aftermath" doesn't map to
finale page count linearly — calibrate against climax-position instead.
Caveat: n=4/arm, directional; Phase C's ≥20/arm distribution referee is the gate.

## PHASE C RESULTS (2026-07-12, 2 arms × 20 same-seed configs, 5 vaults)

**PRIMARY GATE — P(human) distribution: PASSED.** Hand-ON mean **0.515** /
median **0.489** vs hand-OFF 0.364 / 0.160 (paired Δ mean **+0.150**, median
+0.117; 11/20 up — gains to +0.97 — 5/20 down). The first measured
humanlikeness gain of any lever in the program; for scale, five engine
generations (p10→p25a) had moved the metric zero. Hand diversity across the
arm: 19 distinct cards in 20 paths.

**SECONDARY — cross-story feature dispersion: flat** (0.444 → 0.440). The gain
came from the tell families moving, not from spread; the cluster meta-goal is
NOT yet demonstrated by this (coarse) metric — revisit with a better
dispersion measure and wider marginals once the primary effect is banked.

**Judge craft floor (full 20 pairs): small real tax, craters exonerated.**
Judge Δ mean −3.0 (83.5→80.5); 5 pairs ≤−12 BUT the forensics + base-rate
control cleared them: every crater was a CONTINUITY failure, and continuity
zeros run ~50% in BOTH arms (10/20 off, 11/18 on) — the corpus's pre-existing
continuity lottery landing unevenly across a paired draw, not hand damage.
(Continuity remains the reader's #1 defect overall — its own roadmap: ghost
figures, object state.) The literal "no story below its baseline band" floor
is unusable in a 50%-zero regime; the population mean (−3.0) is the honest
craft price of +0.150 humanlikeness.

**Status: awaiting the user's read + default-on decision (p29).**
Recommendation: flip ON — the tax is small, the gain is the program's first,
and the crater cause is orthogonal with its own fix queue.

## What this absorbs (the flag inventory shrinks)

DWELL_FIG_VARIETY (benched) → image ration + grain channel, structural form.
DWELL_DENOUEMENT (failed as prose) → ending-budget stamp, structural form.
Future one-off levers for dialogue proportion / relationship range / intertext
→ axes, not new flags. DWELL_CODA_FIX stays independent and always-on.

## Why this beats thirty per-feature levers

One mechanism class (draw → materialize → hold) instead of N rules that
collide; every family treated at the layer with measured traction; the cluster
meta-cause solved by construction (the signature IS the random variable); one
precedence ladder stated once; one referee harness; and every axis
individually falsifiable before it ships.
