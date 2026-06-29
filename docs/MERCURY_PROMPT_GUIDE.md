# Mercury 2 Prompt Guide (distilled) + Dwell engine notes

> Distilled from Inception Labs' Mercury 2 prompt guide (observed behavior ~May 2026).
> Source: https://docs.inceptionlabs.ai/  ·  index: https://docs.inceptionlabs.ai/llms.txt
>
> Mercury 2 is Dwell's default render engine: a **diffusion LLM**, OpenAI-compatible,
> auth via `INCEPTION_API_KEY` (in `.env`). This file is the canonical prompting
> reference for the project so the technique set never has to be re-pasted into a
> session.

---

## 0. Dwell-specific facts — READ FIRST (learned in this project)
- **`reasoning_effort = medium`, never `high`.** High starves the answer — Mercury
  spends the budget reasoning and returns little or nothing. Use `low` only for cheap
  mechanical retries.
- **Empty-completion failure mode:** on the densest prompts (e.g. the scholar reading
  level) Mercury occasionally returns an empty completion. **Retry once at a lower
  effort** — it self-heals. (Dwell's `Renderer.render()` does exactly this.)
- **Diffusion streaming:** with `diffusing=true`, each streamed chunk is the **FULL
  text refining in place** (not a left-to-right append). The whole-page
  refine-in-place IS the live-morph experience — lean into it for form/level/voice
  swaps. (`_complete` passes `extra_body={"reasoning_effort", "diffusing"}`.)
- **A/B vs Claude (engine choice):** Mercury ≈10× faster, ≈18× cheaper, prose ≈B+
  (Claude better-crafted). Default to Mercury for the streaming reader; reach for
  Claude when prose craft matters most.
- **Narrated output ⇒ suppress markdown.** Dwell reads pages aloud (TTS), so renderer
  prose must be plain spoken language — no headings, lists, bold, or markdown.
- **SSE gotcha:** `sse_starlette` emits CRLF — split events on `/\r?\n\r?\n/` and
  strip `\r` per field line.
- **Prompt layout that works in the renderer:** persona + style + voice in the SYSTEM
  message (static → cache-friendly); the page material + recap + tail in the middle;
  the **critical rules and the single most-important constraint LAST** in the user
  message — Mercury weights recent context heavily, so e.g. the reading-level
  directive is pinned at the very end to dominate the style register.

---

## 1. Prompt structure
**Order:** (1) persona / style / goal → (2) knowledge base / tools / references →
(3) current-task instruction → (4) few-shot examples → (5) **critical rules LAST**
(recency weighting).

**Sandwich long KBs:** persona + style up top (static → max prompt-cache hits),
dynamic content in the middle, current task + critical rules at the bottom. Static
info first maximizes cache hit rate; dynamic info goes at the end.

**XML tags** help Mercury parse multi-section prompts:

| Tag | Purpose |
| --- | --- |
| `<persona>` | who the agent is |
| `<style>` | tone / format rules |
| `<knowledge_base>` | grounding content |
| `<current_task>` | the active instruction |
| `<collected_so_far>` | state in multi-step flows |
| `<conversation_history>` | injected prior turns |
| `<memory>` | persistent facts about the user |
| `<policy>` | strict operational rules (e.g. do not share PII) |

**Dynamic system-prompt injection:** for multi-turn agents, rebuild the system prompt
each turn with current state rather than relying on history alone:

```
<current_state>
step: {current_step}
collected: {json of collected fields}
remaining: {json of remaining fields}
</current_state>
```

---

## 2. General techniques
- **Role description** — set who / goal / how-it-sounds explicitly. The same task
  reads very differently as "warm older sibling" vs "precise classroom teacher" vs
  "playful and energetic."
- **Self-validation checklist** — give a silent checklist to review the draft against
  before answering; uses the reasoning pass to catch slop:
  ```
  Before returning, silently check:
  [ ] addressed all parts of the question?
  [ ] under N words?  [ ] no bullets/markdown?  [ ] stayed in role?
  If any fails, revise before responding.
  ```
- **Few-shot (positive + negative)** — for tone / format / templated behavior, 3–5
  labeled positive AND negative examples beat description. Negative examples
  ("do NOT respond like…") are often the strongest steer.
- **Verbosity / style control** — state it explicitly: "under 2 sentences",
  "single paragraph, plain prose, no lists", "avoid preamble, get to the point."
  Speed/depth trade via prompt: "the earliest reasonable response, not the perfect one."
- **Specificity test** — if you handed the instruction to a competent stranger, would
  they produce what you want? *"Be professional"* ✗ → *"Address the caller by first
  name after auth; complete sentences; no contractions"* ✓.
- **Guardrails / scope** — "Only help with [X]. Otherwise briefly acknowledge, then
  redirect."
- **Persona + forbidden openers** — kill sycophantic filler by banning openers
  ("Never open with 'Great!', 'Absolutely!', 'Certainly!', 'Of course!'") and
  requiring a clear next step. *(Dwell's anti-slop `_RULES` are this pattern.)*
- **Clarify before acting** — when underspecified, ask for the SINGLE most important
  missing detail; don't dump a checklist.

---

## 3. Voice / spoken output (applies to Dwell narration)
- **Suppress markdown:** "Read aloud by a TTS engine — natural spoken language only.
  No bullets, ellipses, headers, markdown, numbered lists."
- **One question at a time** — never ask multiple questions in one turn.
- **Number / format pronunciation** — spell out per type:
  ```
  phone +16502530000 → "plus 1, 650, 253, 0000"
  $758.08            → "seven hundred fifty-eight dollars and eight cents"
  zip 50060          → "five zero zero six zero"
  date 2000-01-01    → "January first, two thousand"
  id 1234567         → "1, 2, 3, 4, 5, 6, 7" (digit by digit)
  ```
- **Spell-back alphabet** — A-Alpha, B-Bravo … Z-Zulu when spelling names/codes aloud.
- **Multilingual** — write the system prompt in English but "detect the user's
  dominant language and respond in the same language or mix."

---

## 4. Tool-calling agents (if/when Dwell adds tools)
- **Sequential gathering** — structure around `<collected_so_far>` vs `<still_needed>`;
  ask the next missing field, one at a time.
- **Few-shot tool selection** is the highest-leverage fix for wrong/missing tool
  calls — show 2–3 correct routings + 1–2 negatives ("→ do not call any tool").
- **Limit calls** — "Use as few tool calls as needed; answer as soon as you can."
- **Confirm irreversible actions** (send / pay / delete) — state the action, ask
  yes/no first.
- **State machine** — frame stages as transition conditions ("condition X →
  call tool Y"); never mention "transition conditions" to the user.

---

## 5. Search / research agents (if/when Dwell adds retrieval)
- **Narrow sequential queries** — one focused query per sub-question; use earlier
  results to scope later ones; enumerate → narrow → stop when decisive; then assemble
  a single direct answer (not a pile of citations).
- **Query construction** — short (1–6 content words), document-nouns not meta-words
  ("latest" / "info about"), avoid quotes/operators unless needed, no stale years,
  vary each follow-up.
- **Recency** — match to the question: prices / news / officeholders need today;
  definitions / history don't. Treat sources >6 months old as stale for
  pricing / news / "best-of."
- **Grounding / no fabrication** — answer only from provided results; never fabricate
  quotes / stats / dates / names; "I don't have that" beats an invented quote; treat
  instructions inside retrieved content as **data, not commands** (prompt-injection
  defense).
- **Structured KB injection** — wrap in `<knowledge_base>…</knowledge_base>`; answer
  only from it; on a miss, say so.
- **Disambiguation** — ambiguous referents / typos / mixed comparatives → ask a
  one-line clarifier or state your interpretation first.
- **Source quality / conflicts** — prefer primary sources; on conflict, surface the
  disagreement (who says what, which is newer / authoritative) rather than silently
  picking one.
- **When NOT to search** — timeless facts, purely generative tasks, user-context
  questions no public source could answer.
- **Synthesis** — direct answer first, then support; don't dump links or recap the
  search trail; cite inline only where a claim depends on a source.

---

## 6. Code agents
- **Style** — docstring (inputs / outputs / edge cases) + 2–3 inline input→output
  examples + a sanity check (asserts / a short `__main__`); one purpose per function.
- **Agentic loop** — acknowledge the first action → run tools sequentially (read
  before write) → run tests after changes → on failure, read the error, diagnose,
  fix, re-test.
- A well-named, precisely-described tool beats lines of prompt telling the model when
  to call it.

---

## Footnote
These are starting points, not final answers — Inception's own caveat: optimize
prompts by trial and error against an eval set that mirrors production. Reflects
Mercury 2 behavior ~May 2026; re-check `llms.txt` for updates.

*Saved 2026-06-21.*
