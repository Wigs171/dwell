# Dwell — Productionization Plan (tkinter prototype → real app)

> Detailed implementation plan. Pair with `DWELL_HANDOFF.md` (orientation for a
> fresh context) and the project memory (`MEMORY.md`, `project_the_current.md`).
> Decision date: 2026-06-18.

## 1. What Dwell is
A streaming, steerable, self-narrating reader that wanders a knowledge **vault**
(a cross-linked Obsidian-style markdown wiki built by the Compendium pipeline).
The reader is passive ("recliner, not cockpit"); the content does the walking.
Pages (~5 paragraphs) are generated live by a **diffusion LLM** and *diffuse into
view* (each frame is the full text refining in place). The reader can dwell, let
it flow, steer with a phrase, take a branch, follow a "✦ unexpected link" (a
semantically-near but unlinked node), select any passage to **expand/simplify/go
deeper** in place, and optionally hear it narrated.

## 2. The decision
**Web frontend + Python (FastAPI) backend, SSE streaming, `pretext` for the text
layer; ship as a PWA (tablet/phone) and optionally Tauri (desktop).**

Why: tkinter was the right *prototyping* shell but the wrong *product* surface.
The diffusing/streaming text, the endless scroll, and select-to-expand are all
native to the browser; the engine is already UI-agnostic so it ports behind an
API; web/PWA is the only realistic path to the tablet dream. This matches the
original design memo's promotion path ("`cli.py read` → browser surface").

## 3. Carries over vs. rewritten
- **Carries over — the engine (`prototypes/dwell.py`), almost as-is behind the API:**
  `Brain` (vault load), `DenseSpace`/`TfidfSpace` (embeddings / fallback),
  `Navigator` (page planning, steering, dwell/move rhythm, missed-connection
  leaps, launch-menu seeding), `Renderer` (provider-agnostic Mercury|Anthropic;
  streaming + diffusing; `expand()`), `ReadingHistory`, `TweenCache`, `PagePlan`,
  `missed_connections()`, `migrate_meta()`. Plus `dwell_tts.py` (Kokoro `Narrator`).
- **Rewritten — the surface (`prototypes/dwell_ui.py` / tkinter):** becomes a web
  frontend. The tkinter mark-surgery streaming → DOM/pretext. The tkinter
  selection→popup → the browser Selection API + a positioned popover.

This split is the big asset: ~70% (engine) is reused; only the presentation
layer — the part that *should* change for a real app — is rebuilt.

## 4. Architecture (layers)
```
Surface     Web frontend (Svelte / SolidJS / React / vanilla):
            reading canvas, launch menu, branch chips, steer box,
            select→expand popover, voice/engine/diffuse controls.
            → PWA (installable on tablet/phone) and/or Tauri desktop bundle.
  │         └ text layer: pretext (measure / virtualize / no-layout-shift)
  │                       + DOM (simple) or Canvas (fancy diffuse) rendering
Transport   SSE for page + expand streams (Mercury itself streams SSE — the
            server just relays chunk.choices[0].delta.content); plain POST/GET
            for control (menu, branches, steer, voices, missed).
  │
Engine      Existing Python behind a thin FastAPI adapter (see §5).
            Keys stay server-side. Session state server-side (in-memory dict
            keyed by session id) to start; ReadingHistory persists to disk.
  │
Data        Vault = markdown on disk (unchanged). Per-vault meta: consider a
            small SQLite (history + tween cache + embeddings) instead of the
            current loose JSON. Embedding vectors: numpy is fine at hundreds–
            thousands of pages; sqlite-vec / LanceDB only if vaults get huge.
  │
Audio       Kokoro server-side → stream audio to the browser (or Web Speech API
            client-side as a lighter fallback).
```

## 5. API surface (FastAPI) — first cut
- `GET  /vaults` → available vaults (name, node count, has-voice).
- `POST /session` `{vault, start: resume|new|surprise, voice?, engine?}` → opens a
  session (loads `Brain`, builds `Navigator`), returns `{session_id, menu, voices}`.
- `GET  /page` (SSE) `?session=&action=first|auto|plan&plan_id=&diffusing=` →
  streams the page; each event carries the full text-so-far (diffusing) or a
  delta (append). Terminal event includes node id, mode, marker (live/coast),
  cost. Server forwards Mercury's SSE chunks.
- `POST /steer` `{session, text}` → applies steering (next `/page` bends).
- `GET  /branches?session=` → `propose()` directions (dwell / move / ✦ leap),
  each with a `plan_id` for `/page?action=plan`.
- `POST /expand` (SSE) `{session, selected, before, after, mode}` → streams the
  in-place reworking (`Renderer.expand`).
- `GET  /voices?session=` → vault voices + presets; `POST /voice {session,name}`.
- `POST /level {session,name}` → reading level (general/elementary/middle/high/college/
  scholar); `/session` also takes `level`, returns `level`/`levels`. `POST /repage
  {session,index}` (SSE) → re-render a composed page in place at the current level.
- `POST /quiz {session,pages[],count,types[]}` → a mixed-format retrieval-practice quiz.
- `GET  /missed?session=&n=` → missed-connections report.
- `POST /tts` (SSE) — Kokoro audio per sentence (DONE). *(New endpoints must be added to
  the Vite proxy allowlist in `dwell-web/vite.config.ts`.)*

## 6. Text layer — `pretext` (Phase 3)
`pretext` (Cheng Lou, MIT, ~15KB) = pure-JS text **measurement & layout** without
DOM reflow. Born at Midjourney for "streaming AI tokens into hundreds of text
blocks in real time" — Dwell's exact domain. Use it for:
1. **Virtualizing the endless stream** — measure each page's height *without*
   rendering, so a long session windows the visible pages (no thousand-node DOM).
2. **Zero layout-shift** as a page streams in (reserve correct height ahead of paint).
3. **Canvas-grade diffuse animation** (optional) — diff successive full-text frames
   and animate the changed spans instead of hard-replacing.
Not strictly required for a single streaming block (plain DOM survives that); the
payoff scales with session length and polish. Adopt at Phase 3.

**Adopted 2026-06-19** — pretext now powers the reader's **fit-to-page** sizing:
`prepare()` once per page, then binary-search the font scale via `layout()` so the whole
page fits the card with zero per-frame reflow. The deck's 3-card window covers the
"virtualize the stream" goal; the remaining uses (zero-layout-shift, animated diffuse)
are optional.

## 7. Decisions already locked (from the prototype — do not re-litigate)
- **Engine:** Mercury `mercury-2` via Inception OpenAI-compatible API
  (`https://api.inceptionlabs.ai/v1`, `openai` SDK, `INCEPTION_API_KEY` in `.env`,
  `DWELL_PROVIDER=mercury` makes it default). **`reasoning_effort=medium` for
  pages — NEVER `high` (it starves the answer to empty); `instant` for expand.**
  `MERCURY_MAX_TOKENS=8192`. ~$0.001/page, 10M free tokens. Anthropic (Claude
  MECHANICAL tier) is the fallback engine.
- **Diffusing streaming default ON** (`diffusing:true` in `extra_body`; each chunk
  is the full refining text → overwrite-in-place).
- **Embeddings:** sentence-transformers `all-MiniLM-L6-v2` (cached per vault),
  TF-IDF fallback. Powers navigation + missed-connections + leaps.
- **Voices:** a vault-shipped synthesis page tagged `voice` (or id `the-voice-of-…`)
  becomes the default narrator persona; built-in presets + free-text custom;
  resolution order vault → preset → custom.
- **TTS:** Kokoro (`kokoro-onnx`, ONNX/no-torch); models in `~/.cache/kokoro-onnx`.
- **Prompt shape:** anti-slop craft + voice persona; persona/style first in the
  system, material in `<material>`, critical rules LAST (Mercury recency weight),
  explicit no-markdown. **No "self-check" step** (it starves the reasoning model).
- **Per-vault meta files** (in `<vault>/wiki/_meta/`): `.dwell-tween-cache.json`,
  `dwell-history.json`, `.dwell-embeddings.json`.
- **Vault mode (decided 2026-06-18, to implement):** each vault declares
  `mode: academic | narrative` (extensible) in its `CLAUDE.md`; default
  **academic** if absent. Read at load; it **gates which behaviors are legal** —
  *academic*: strict grounding, surface-don't-reconcile contradictions (⚡ tension
  marker), attribute claims, optional quizzes, semantic-graph wander, **never
  dream/fabricate**; *narrative*: sequence/character-aware navigation + clearly-
  marked "dream the interim" interpolation, spoiler-aware, no reconcile concept.
  The mode is the **hard boundary** between never-invent (academic) and
  invent-bounded (fiction) — enforce it at the vault level, not per-prompt.
  **(SHELVED 2026-06-20** — the user finds the ⚡ tension half not compelling right now;
  vault-mode gating waits with it. Revisit later.)
- **Reading level (locked 2026-06-19):** a fixed ladder `general / elementary / middle /
  high / college / scholar`, an axis **ORTHOGONAL to voice** (voice = how it sounds,
  level = how complex). One vault is the source of truth; the renderer re-pitches the
  same material. The level directive is placed LAST in the render prompt (Mercury
  recency weight) and is part of `cache_key` (default level omitted → old caches stay
  valid; each level keeps its own pages). Changing the level re-renders the current page
  in place.
- **Quizzes (locked 2026-06-19):** retrieval practice every N pages over the cached page
  text, generated by `Renderer.quiz` in 5 formats (choice/true-false/cloze/recall/
  matching); **open-book** (non-blocking draggable window + each answer's verbatim
  evidence highlighted in the pages); fully tunable (count 3–25 / frequency / per-type).
  The quiz inherits the current reading level.

## 8. Phases (implementation order)
1. ✅ **Engine API — DONE (2026-06-18).** `prototypes/dwell_server.py`: FastAPI
   wrapping `dwell.py` with `/session`, `/page` (SSE), `/branches`, `/steer`,
   `/expand` (SSE), `/voices`, `/voice`, `/missed`, `/state`, `/wander`, `/vaults`.
   Verified by `dwell_smoke.py` (dry + live Mercury pass, all green) and a real
   browser run of `dwell_web.html` (streaming render, branches incl. ✧ leap,
   select→expand, voices, cost). Tkinter demo untouched; engine wrapped, not forked.
   *Note:* `/page` and `/expand` are POST (one fetch-stream client path); reads are
   GET. SSE event `data` is always a JSON object. See `DWELL_HANDOFF.md` "Phase 1".
2. ✅ **Web reading surface — DONE (2026-06-18).** `prototypes/dwell-web/`: a
   Svelte 5 + Vite (TypeScript) SPA on the Phase 1 API. Runes store
   (`dwell.svelte.ts`) + typed client (`api.ts`) + components. **Endless-scroll**
   (pages append; prefetch → free replay, shown as `· Dwell`). Dev = two processes
   (backend :8000 + `npm run dev` :5173 proxy); see `dwell-web/README.md`.
   *Gotcha:* Vite 8 rolldown native binding skipped by npm — pinned in package.json.
2.5 ✅ **Odysseus UI port + audio narration — DONE (2026-06-18→19).** Faithful port
   of the user's own Odysseus app UI: 16 themes + a **tabbed Settings window**
   (Themes swatch grid / Customize color editor / Dwell) + **custom theme editor**,
   **animated backgrounds** (14 canvas + 3 WebGL2 shaders + dots, per-theme
   defaults, intensity/size/effect-color), **frosted glass**, density, a
   node-focused sidebar (search · reading trail · popular nodes · gear). **Audio
   narration**: server-side Kokoro streamed per sentence (`/tts`), played gaplessly
   via Web Audio, with **word-level karaoke highlight (accent) + auto-scroll** and a
   single **▶ Play/⏸ Pause recliner** (reads a page → follows a queued direction,
   else autoplays the default flow path; Web Speech fallback). Node **source shown
   in the title bar**. (Replaced "Auto" with "Play"; the ✧ leap is now a normal path.)
   *Done from the old "remaining polish" list:* audio.
2.6 ✅ **Reading deck + zoom + quizzes + reading levels — DONE (2026-06-19/20).** The
   reader was rebuilt from endless scroll into a **PDF-style 3-card deck** (prev ·
   current · next + a "compose next" ghost) with **fit-to-page zoom** powered by
   **`@chenglou/pretext`** (analytic font-size fit, no DOM reflow) and **justified +
   hyphenated** text; deck spacing is modular (`--peek`, card sized to the stage).
   **Select-to-clarify** redesigned (Simpler / ✦ More, draggable non-blocking popover,
   re-narration of the changed passage). **Quizzes** (retrieval practice): a checkpoint
   every N pages over the previous N, **5 formats** (choice/true-false/cloze/recall/
   matching) in a varied mix, an **open-book draggable window** with answer-evidence
   highlights in the pages, full Settings (count 3–25, every-N, per-type toggles); the
   quiz prompt was rebuilt to the **Mercury 2 prompt guide**. **Reading levels**
   (`Renderer.set_level`, general→scholar): the SAME vault re-pitched to the reader's
   level — an axis **orthogonal to voice**, level baked into `cache_key`, the directive
   placed LAST in the prompt; changing the level **re-renders the page in place**
   (`/repage`) with a **narration handover** (resume near your spot). Settings + quiz
   windows made non-blocking/undimmed; **Dwell tab moved to the front/default**.
   `render()` retries once at `effort="low"` on Mercury's empty-completion. New endpoints
   `/quiz`, `/level`, `/repage`. *Shelved by the user (2026-06-20):* the ⚡ tension marker
   + vault-mode gating (§7).
3. **`pretext` text layer — PARTLY DONE (2026-06-19/20).** Adopted `@chenglou/pretext`
   for the analytic **fit-to-page** measurement (binary-search the font scale from
   `prepare()`/`layout()`, no DOM reflow), and the card deck only mounts ~3 cards (the
   windowing the original plan wanted). Remaining/optional: virtualised long content +
   zero-layout-shift diffuse streaming.
4. **Package & polish.** PWA (installable on tablet/phone); optional Tauri desktop;
   settings/persistence already largely done (localStorage); FastAPI serves `dist/`.

## 9. Open questions / risks
- **On-device vs server:** near-term the engine runs on a PC/home server; the
  tablet is a thin browser client over the LAN. Fully on-device (engine + a small
  diffusion model on the tablet) awaits cheap on-device diffusion (~2027 per the
  earlier research; see `reference_open_source_tts.md` sibling notes).
- **Local vs hosted:** local-first (localhost FastAPI + browser) is simplest and
  fits the user's ethos; a hosted multi-user version needs accounts, per-user
  vault storage, and cost/rate controls.
- **Frontend framework:** TBD — Svelte or SolidJS are lean and well-suited; vanilla
  is viable for a first cut.
- **Vault distribution / rights** — see the vault-business notes (separate); shapes
  what content can ship.
- Class is still named `CurrentApp` in `dwell_ui.py` (cosmetic leftover from the
  rename); fine to retire when the UI is rebuilt.

## 10. Pointers
- Code: `prototypes/dwell.py`, `dwell_ui.py`, `dwell_tts.py`; launcher `Launch Dwell.bat`.
- Memory: `MEMORY.md`, `project_the_current.md` (full evolution + every decision),
  `reference_open_source_tts.md`, `reference_2026_pricing.md`.
- Test vaults under `C:\Users\user\Downloads\`: `Example Vault` (150
  nodes, embeddings cached), `Large Vault` (521 nodes, ships a voice
  page), `art-critique-vault`, `music-affect-vault`, `shoegaze-vault`.
