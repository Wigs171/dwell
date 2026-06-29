# Dwell Voices — redesign proposal + build log

> Status 2026-06-27: **Phase 1 BUILT** (voice cards + axis-composition fix, in `dwell.py`).
> Phases 2–4 specified below, not yet built. Read this before touching the voice system.

---

## The problem (diagnosed)

Two complaints, one root cause:

1. **"Only `clean` feels real; the other voices wash out."**
2. **"Form overrides voice (or vice-versa) — they don't work in unison."**

**Root cause — the generic-mean attractor + a recency ladder.** LLM prose collapses
toward a narrow, mid-formal default register (measured: an aggressive "be creative" prompt
moved style diversity only 0.46→0.58 vs a 0.74 human baseline — [arXiv:2501.19361](https://arxiv.org/html/2501.19361v1)).
`clean` felt distinct only because it *coincides* with that default — it got a free ride.
Voices far from the mean (noir, storyteller) feel the strongest pull back to generic.

On top of that, the three style axes were on a **recency ladder**, not separate channels.
In the old `render()` the positions were:

| Axis | Old position | Effective weight |
|---|---|---|
| Voice | very top of system message, no end reinforcement | weakest |
| Form | reinforced near the end (`form_tail`) | strong |
| Level | pinned dead-last, *"the single most important constraint… honour above every other style note"* | dominant |

Since both Mercury and Claude weight recency, the order was literally **level > form > voice**.
The voices were also thin — a 1–2 sentence adjective blurb with no exemplars, no cadence rule,
no "never" list. Description loses to the model's prior; **coordinates** beat it.

> Note: Dwell renders **each page as a fresh, stateless completion** (the whole system+user
> prompt is rebuilt every page from current voice/form/level). So conversational "persona
> drift across turns" does **not** apply here — every page already re-anchors. The wash-out
> was purely (a) weak prompt position and (b) thin specs. Both are what Phase 1 fixes.

---

## Phase 1 — BUILT (voice cards + disjoint style channels)

### Voice cards (replaces the adjective blurbs)
`VOICES` is now a dict of structured `VoiceCard`s ([dwell.py](dwell.py), search `class VoiceCard`):

```
essence   — one-line identity
diction   — word choice / lexicon
cadence   — a CONCRETE rhythm rule (e.g. noir: "after a long sentence, cut to a short one")
stance    — POV / relationship to the reader
moves     — characteristic rhetorical moves
never     — voice-specific banned tells
exemplars — 2 short passages IN THE VOICE (neutral content; few-shot texture targets)
purpose   — who/what it's for (UI + self-doc)
tts       — paired spoken voice {voice: <kokoro id>, speed, hint}  ← karaoke coupling data
```

Two render helpers: `_voice_full(card)` (the full block → system message, cache-friendly) and
`_voice_anchor(card)` (a compact essence+cadence+1-exemplar reminder → end of user message).

**The single biggest fix is the exemplars.** A 400-author study found ~5 labeled examples
beat zero-shot description on every fidelity metric ([arXiv:2509.14543](https://arxiv.org/html/2509.14543v1));
examples carry sentence-length/punctuation/rhythm the model can't infer from the word "noir."

### The curated set (6 purposeful voices)
Replaces the 8 demo blurbs. Each has a stated audience:

| Voice | Purpose | Paired Kokoro voice |
|---|---|---|
| `clean` | trustworthy general reading (default) | am_michael |
| `plain` | accessibility — new readers / English learners | af_heart |
| `storyteller` | engagement, audio-first, bedtime, younger readers | bm_george |
| `noir` | make dry/grim material gripping | am_onyx |
| `mentor` | relatable on-ramp — informal spoken register for readers alienated by textbook prose | am_adam |
| `scholar` | specialist texture (precision over warmth) | bm_lewis |

(The retired demo voices — `old-novel`, `surfer`, `beat` — can be re-authored to the card
template anytime; free-text voices like *"a gravelly 1940s radio announcer"* still work verbatim.)

`mentor` is deliberately a **general informal/conversational register**, NOT an inferred ethnic
dialect. The dialect/vernacular case is Phase 3 and is opt-in only — see the ethics firewall.

### Disjoint style channels (fixes the override)
The override-style tails (`form_tail`, the *"single most important constraint"* `level_tail`)
are gone. In their place, a labeled **style-channels block** at the end of the user message,
plus voice now **brackets** the prompt (full card leads the system message for primacy; a
compact anchor closes the user message for recency):

```
— STYLE CHANNELS (blend these independent axes; do not let one override another) —
<voice>VOICE (hold this): …essence + cadence + one exemplar…</voice>
<form>FORM — render this whole page …</form>            (only when non-default)
<reading_level>…</reading_level>                          (only when non-default)
Keep the channels separate: READING LEVEL governs sentence length and vocabulary and is
non-negotiable; FORM governs structure; VOICE governs diction, imagery, rhythm and stance
ONLY — never raise vocabulary or complexity to fit the voice. If they pull apart, hold the
reading level, keep the form, and let the voice flex within them.
```

Each axis gets its own XML-tagged channel with a **disjoint job** (Anthropic reports XML
structure cuts "mixing up" prompt parts 20–40% — [docs](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).
Critically, **VOICE is forbidden from setting vocabulary/complexity** — that's level's channel —
which is what stops the two from fighting. Reading-level entanglement is real and measured: the
same "9th grade" target drifts ~6 grades depending on the requested tone ([readability study](https://seantrott.substack.com/p/measuring-the-readability-of-texts)),
so level is bracketed first (system) + last (channels block).

Voice content changes are hashed into `voice_id`, so the new cards invalidate stale render caches.

### Validated (2026-06-27)
`rlm-vault`, Mercury, same opening node, three configs:
- noir produced *"a digital back-alley where researchers trade benchmarks like contraband… the
  streets grow longer, the shadows deep"* — distinct, not washed out.
- noir **+ QA form** kept the Q&A structure **and** the noir diction (*"each a case file"*,
  *"enough to drown a city in paper"*) — axes blend, neither dominates.

---

## Phase 2 — Karaoke coupling (spec; next to build)
The card already carries `tts: {voice, speed, hint}`, and `/voices` now returns `cards[name].tts`
plus `current_tts`. Remaining work is **frontend**: when the text voice changes, auto-switch the
`AudioNarrator` to the paired Kokoro voice + speed (user can still override). Keep Kokoro as the
default narrator — it's the only no-torch, CPU-friendly, conflict-free engine (the reason
Chatterbox was rejected: its torch/numpy pins would break the sentence-transformers install).
`hint` is a natural-language voice description usable later by a *promptable* engine (Qwen3-TTS
voice-design / Parler-style) without hardcoding ids.

## Phase 3 — "Voice from a sample" (spec)
For the teacher/learner use case: derive a voice from an example of a real speaker. Use the
**describe-then-imitate** two-step ([STYLL](https://arxiv.org/html/2212.08986v3) / [CAT-LLM](https://arxiv.org/pdf/2401.05707)):
1. **Extract** (once, reasoning-heavy → Claude): distill the sample into a structured Text Style
   Definition matching the `VoiceCard` schema (diction / sentence-length distribution / syntax /
   cadence / figurative density / idiosyncratic tics). Persist as a `the-voice-of-*` vault page —
   Dwell **already** ingests those via `_voice_directive_from_page` ([dwell.py](dwell.py)).
2. **Apply** (per page, cheap → Mercury): the card supplied as the rules-LAST channel block.
3. **Acceptance gate:** counterfactual LLM-judge A/B (did the *voice* change?) **+** SBERT
   content-fidelity check (did the *knowledge* survive?).

Optional audio: clone the speaker's *delivery* (accent/prosody, not just timbre) with
**Qwen3-TTS** (Apache-2.0, ~3s clone, promptable accents) or **VoxCPM2** (best prosody) — run
**out-of-process** behind the existing `/tts` boundary to dodge the torch conflict.

## Phase 4 — Banned-lexicon validator (spec)
A prompt-level "avoid X" is ~80% reliable. Add a code-side post-render scan (global slop list +
each card's `never`) that triggers a **bounded repair re-render** before TTS — catches the ~20%
the prompt misses without a visible reset.

---

## ⚠ The ethics firewall (load-bearing — read before building Phase 3)

Register-adaptation and vernacular-generation are **two different features** with different risk:

- **Register / complexity** (plainer, shorter, less jargon — `plain`, reading levels): ship freely.
  Comprehension-positive, low-risk.
- **Vernacular / dialect generation**: gated, high-risk. Off-the-shelf LLMs carry *covert* dialect
  prejudice (Hofmann et al., **Nature 2024** — matched-guise AAE prompts yield lower-prestige jobs,
  higher conviction/death-sentence rates; [Stanford HAI](https://hai.stanford.edu/news/covert-racism-ai-how-language-models-are-reinforcing-outdated-stereotypes)).
  **Naming the dialect amplifies the bias** (some models +51% — [arXiv:2605.24384](https://arxiv.org/html/2605.24384v1)),
  and dialect in training corpora is disproportionately performative/mocking, so a stock model
  generating e.g. AAVE tends toward minstrelsy, not authentic speech ([Data Caricatures, arXiv:2503.10789](https://arxiv.org/pdf/2503.10789)).

**The rule for Dwell:** *register is adapted for everyone, but vernacular is always the reader's
own opt-in choice, presented alongside the source — never inferred from name/location/sample,
never imposed, never LLM-improvised.* Concretely:
1. Vernacular is **opt-in only**, never auto-applied from inferred identity.
2. **Offer contrast, don't replace** — show source + a same-meaning rephrasing side by side
   (the reading-achievement lever is bidialectal *awareness*, not substitution).
3. **Self-cloning is the clean default** for audio — the learner records *themselves*; never ship
   generic "[region/ethnic] accent" presets built from one voice.
4. Stigmatized dialects, if shipped at all, use **community-authored, human-reviewed exemplar
   voices** (fits the `the-voice-of-*` mechanism), validated with speakers — never stock-model
   instruction.
5. Never feed dialect-tagged output into any ranking/scoring/comparison path.
6. Voice cloning requires **specific, informed, revocable consent**; label AI-generated audio.

---

## Research appendix (key sources)
- Style homogeneity / generic-mean attractor — [arXiv:2501.19361](https://arxiv.org/html/2501.19361v1)
- Few-shot exemplars beat description (~5 is the knee) — [arXiv:2509.14543](https://arxiv.org/html/2509.14543v1)
- Reverse-prompt few-shot (voice as demonstrated prior turns) — [Panickssery](https://blog.ninapanickssery.com/p/how-to-make-an-llm-write-like-someone)
- Multi-attribute control / one axis swamps others — [MAGIC, ACL 2024](https://aclanthology.org/2024.acl-long.500/) · [C³TG, arXiv:2511.09292](https://arxiv.org/pdf/2511.09292)
- Reading-level × tone entanglement — [Trott](https://seantrott.substack.com/p/measuring-the-readability-of-texts)
- Describe-then-imitate style transfer — [STYLL](https://arxiv.org/html/2212.08986v3) · [CAT-LLM](https://arxiv.org/pdf/2401.05707)
- Covert dialect bias — [Hofmann/Nature via Stanford HAI](https://hai.stanford.edu/news/covert-racism-ai-how-language-models-are-reinforcing-outdated-stereotypes) · label-amplification [arXiv:2605.24384](https://arxiv.org/html/2605.24384v1) · [Data Caricatures](https://arxiv.org/pdf/2503.10789)
- TTS landscape / cloning (2026) — Kokoro default; Qwen3-TTS & VoxCPM2 for cloning, subprocess-isolated. See the TTS research notes in this session and [reference_open_source_tts](../../.claude/projects/C--Users-user-Downloads-Compendium/memory/reference_open_source_tts.md).
