# Dwell — Handoff Brief (read this first in a fresh context)

## What Dwell is
A streaming, steerable, self-narrating reader for a Compendium **vault** (a
cross-linked markdown wiki). Pages (~5 paragraphs) are generated live by a
diffusion LLM (Mercury) and **diffuse into view**. The reading surface is a
**PDF-style deck of page cards** — previous · current · next — flipped with
swipe/arrows/keys, with **fit-to-page zoom** (the whole page shows as a card; zoom
in to enlarge + scroll). The reader stays passive but can steer, branch, dwell,
wander to a near-but-unlinked node, **select a passage to clarify (Simpler / ✦
More) in place**, hear it **narrated with word-level karaoke** (a single Play
button reads + auto-advances — the "recliner"), get **retrieval-practice quizzes**
every N pages, and **re-pitch the same vault to any reading level** (elementary →
scholar). One vault is the source of truth; **voice** (how it sounds) and **level**
(how complex) are two orthogonal axes the renderer re-pitches on the fly.

## Current state
**The real app is the Svelte web frontend (`dwell-web/`) on the FastAPI server
(`dwell_server.py`).** Everything below is validated in-browser on live Mercury
(session of 2026-06-19/20). The engine (`dwell.py`) stays pure and still has a
**tkinter reference** (`Launch Dwell.bat`); the web app drives the SAME engine.
- **Reader = a 3-card deck (NO LONGER endless scroll).** `Reader.svelte` renders
  previous (left, out of focus) · current (centre) · next (right) + a dashed
  "compose next" ghost at the live edge; only 2 cards at the very start. Flip with
  **hover-arrows · swipe · ←/→**; **pinch / Ctrl-wheel / +/-/0** zoom. **Fit-to-page**
  (font-size IS the zoom): at zoom 1 the whole page fits the card with NO scroll;
  zoom>1 enlarges and the card scrolls (narration autoscroll follows the spoken word
  only when zoomed). The fit is **analytic via chenglou/pretext** (`@chenglou/pretext`)
  — `prepare()` once/page, binary-search the scale, zero per-frame reflow. Text is
  **justified + hyphenated** (book feel; DOM-native, keeps selection/karaoke). Deck
  spacing is modular (`--peek` sliver; card sized to the STAGE, not the viewport).
- **Select-to-clarify** — select a passage → a draggable, NON-blocking popover:
  **Simpler** / **✦ More** (the old expand/deeper merged). Re-pitches the passage in
  place and treats it as a **re-narration event** (stops stale audio, rebuilds the
  karaoke timeline, reads the changed passage back from there).
- **Quizzes (retrieval practice)** — a checkpoint every `quizEvery` pages, gated
  inside `advance()` so it fires however you wander (flow, branch, ✦ leap, steer) and
  the held move resumes after. **5 formats** in a varied mix: multiple-choice ·
  true/false · cloze (fill-in-the-blank) · free-recall · matching; each grades
  inline. A **draggable, non-blocking window** (open-book — flip back through the
  pages for answers) with each question's **answer evidence highlighted in the pages**
  (amber) while it's open. **Settings → Dwell → Quizzes**: on/off · every-N (2–20) ·
  questions (3–25) · per-type toggles. Quiz prompt follows Mercury 2's prompt guide;
  the quiz inherits the current reading level.
- **Reading levels** — `Renderer.set_level` (general/elementary/middle/high/college/
  scholar), an axis ORTHOGONAL to voice; the **same vault material re-pitched** to the
  level (verified: same node → "a tiny triangle made of dots" vs "a triangular lattice
  of four rows"). Each level caches its own pages (level in `cache_key`). Changing the
  level **re-pitches the page you're on IN PLACE** (`/repage`); if narration was
  playing it resumes at the sentence nearest your spot. **Settings → Dwell → Reading →
  Level.**
- **Audio narration (Kokoro)** — server-side TTS per sentence, played **gaplessly**
  via Web Audio; **word-level karaoke** + **auto-scroll** to the spoken word; ▶ Play /
  ⏸ Pause = the recliner (reads → follows a queued pick, else autoplays the flow path).
  Web Speech fallback.
- **Odysseus UI** — 16 themes + a **tabbed Settings window (Dwell tab first/default)**,
  custom theme editor, animated backgrounds (canvas + WebGL2), frosted glass, density,
  node-focused sidebar; node **source in the title bar**. **Settings + the quiz windows
  are non-blocking and UNDIMMED** (so the live theme preview reads true colours).

## Since 2026-06-19 — major additions (memory files have full detail)
- **Voices → structured cards + disjoint style CHANNELS.** `VOICES` in `dwell.py` are now `VoiceCard`s (essence/diction/cadence/stance/moves/never/exemplars/purpose/`tts`) rendered as labelled prompt channels so voice/form/level/language BLEND, not override (fixed "only clean works" + "form overrides voice"). 6 curated voices. NEW axes: **form** (article/guided/qa/dialogue, with slot-only SHAPE skeletons that can't content-bleed — see the old "Socratic dialogue" leak), **language** (translate the WHOLE vault to any language; axis default `source`). All four axes (voice/form/level/language) live-re-pitch the focused page IN PLACE on change, **batched behind Apply / close-Settings so N changes = ONE paid render**. See [[project_dwell_voices]].
- **Select-to-clarify renamed Simplify / ✦ Expound** (was Simpler/✦More); these now also obey the anti-slop `_RULES` + the language axis. Added a **✎ Note** action; highlighting NO LONGER pauses narration (only a rework does). Notes = a movable window, saved per-vault, linking back to the EXACT saved page **snapshot** (never regenerated) with the saved passage highlighted.
- **Engine: Mercury 2 is the ONLY render engine.** Anthropic removed as a RENDER engine (didn't work with the framework) → `Renderer` hardcodes mercury, no fallback (→ dry). The Anthropic **API is KEPT** (auth works: claude-sonnet-4-6, 1.7s) for the Learn ingest pipeline. Engine selector gone.
- **tkinter UI DELETED** (`dwell_ui.py` + `Launch Dwell*.bat` gone). The Svelte web app is the ONLY UI; the engine stays UI-agnostic.
- **VAULT_ROOT moved `~/Downloads` → `~/Dwell`** (dedicated folder; `DWELL_VAULT_ROOT` overrides; mkdir'd on start). Existing vaults migrated there. **LESSON: never `shutil.move` a vault** — its rename-fallback does copy-then-`rmtree` and a transient lock half-deleted MPH's source (full copy was safe; use atomic `os.rename`). One test vault (`music-affect-vault`) was found already missing during this.
- **Top-level nav Home / Read / Learn** (`dwell.page`, in the sidebar — Home has NO buttons). Home = a diffusion-animated "Dwell" wordmark (glyphs denoise into place) + cycling scramble phrases. Read = gallery+reader. Learn = the vault builder.
- **Vault management** (gallery + the per-vault detail window, `VaultDetail.svelte`): **Add existing** (register any folder via `<root>/.dwell-vaults.json`), **Remove** (forget an import / delete a managed vault — two-click confirm; root-purge guarded), **set/replace COVER image** (`POST /vault/cover`, cache-busted in UI), **Expand** an existing vault (reuse the intake against it). A vault appears in `/vaults` iff it has `CLAUDE.md` + ≥1 wiki page (so fresh drafts stay hidden until ingested).
- **Learn = the vault builder.** `dwell_learn.py` (`/learn/*`, self-contained router): **create** a draft vault, **upload** files (pdf/md/txt → `raw/uploads/`), **meta** (links + research prompt → `_meta/learn.json`), **sources** (curate; **identical re-uploads flagged "already ingested" via content-hash dedup** vs the vault's existing `raw/` + the `compendium` IngestRegistry), **open** (prepare an existing vault for expansion). `dwell_build.py` (`/learn/build`) = **THE INGEST SWARM**: drives the existing `compendium` pipeline (`cli.py ingest`) as ONE **cancellable subprocess PER SOURCE**, streaming SSE (`build-start`/`source`(queued→ingesting→done/failed/skipped)/`log`/`build-done`) with a **Stop** that `proc.kill()`s; a `dry` mode simulates stages free. **Decision pivot:** Claude Agent SDK is NOT installed and `compendium.IngestOrchestrator` already IS an orchestrator+subagent swarm → we DRIVE it, not rebuild. Frontend: `BuildPanel.svelte` (live per-source dots + log + Stop / Open-in-Read). See [[project_dwell_learn]], `DWELL_LEARN_PLAN.md`, `DWELL_LEARN_PRIOR_ART.md`.

## The task (next) — general large-source splitter for the build  ← BUILDING
The ingest swarm currently feeds each uploaded file to `cli.py ingest` **whole** — a 400-page PDF or a giant `.md` overwhelms Router/PageWriter's context. Research (Hermes Agent's context handling is **lossy compression**, NOT chunked ingestion; our own `cli.py split-book` already chapter-splits PDFs) confirms: large material is handled by **CHUNKING**, not agent count — split into chapter/section pieces, ingest each in a FRESH process, accumulate into the wiki (the durable memory). Build:
- A **prepare/split phase** at build start (NO LLM cost) that EXPANDS oversized sources into chunks before ingest:
  - PDF over a page threshold → `cli.py split-book <pdf> --vault <v>` → chapter chunks in `raw/articles/` (capture the new files via a before/after diff of the dir).
  - Large `.md`/`.txt` → split on top-level `#`/`##` headings into ~target-size chunks (pure string ops, FREE to verify).
  - Each chunk = its OWN source/progress row in the live build; the original stays in `raw/uploads/` (as-uploaded) but is replaced in the worklist by its chunks. Content-hash dedup skips unchanged chunks on a rebuild.
- Our subprocess-per-source design fits this perfectly: each chunk = a fresh bounded context (no compression/drift). Verify the TEXT splitter via a `dry` build with a big `.md` (real split + simulated ingest = free).

### Open from the ingest-swarm build (confirm on a real run)
- A full REAL ingest producing pages was NOT waited out (pipeline is slow — >2 min for a tiny `.md`). **Kick off ONE real build** to confirm a vault graduates into Read. (Subprocess hang already fixed: `stdin=DEVNULL` + `PYTHONUNBUFFERED=1`/`bufsize=1`.)
- `cli.py ingest`'s `rich` progress may not stream cleanly through a pipe → real-build `log` granularity unconfirmed (dry shows the target UX). Consider a `--json-progress` flag on `cli.py`.
- Research-prompt → web-research fan-out (STORM) NOT built — build ingests files + links only.
- `BuildPanel` is type-checked but NOT click-verified (file upload can't be driven headless).

## Deferred reader-feature ideas (pre-Learn)
- **⚡ tension marker SHELVED** (user: not compelling right now — maybe later);
  vault-mode gating is the other half, deferred with it.
- **Tablet deploy (Phase 4) deprioritised** ("we develop with a tablet in mind
  already"). When wanted: FastAPI serves the built `dist/` + a PWA manifest (home-screen
  install, fullscreen) so the tablet experience the deck/zoom were built for is real.
- **Phase 3 `pretext`** is now PARTLY realised — pretext powers the analytic
  fit-to-page, and the deck only mounts ~3 cards (the windowing the plan wanted). The
  rest (virtualised long content / zero-layout-shift streaming) is optional polish.
- **Images in pages (idea — explored 2026-06-20, not started).** The vault format
  ALREADY supports this: `compendium/sources/pdf_image_extractor.py` (PDF figures +
  figure-heavy page renders) and `asset_capture.py` (downloaded article images) save
  crops to **`<vault>/raw/assets/<slug>/`** and explicitly anticipate "a future
  image-aware PageWriter." A reading page is a node → its `sources` (already resolved
  into titles for each `/page` `done`) → `raw/assets/<source-slug>/`, so a node's figures
  are resolvable with the existing plumbing. **Recommended v1:** render the node's figure
  as a captioned illustration BLOCK in the card (top/floated), NOT inline — that keeps
  the page prose a SINGLE text node, so select-to-clarify, karaoke, and the pretext fit
  all keep working unchanged; you only fold the figure's height into the fit math, add an
  `/asset` route to serve the file, and add `images[]` to the page payload (no LLM needed
  — the vault is the source of truth). **Richer follow-up:** image-aware rendering (pass
  figure captions to the renderer so the prose references them / place them inline by the
  relevant paragraph) — more compelling but it breaks the single-text-node model
  (complicates karaoke/clarify/fit), so do it second. **Caveats:** the current test vaults
  (Pythagoras/MPH) have ZERO images (text-only sources) — test on an image-bearing vault or
  drop figures in; and confirm the ingest KEEPS the crops by default (vs transcribe-and-
  discard) so real vaults carry images going forward.
- Smaller: the render **retry-on-empty** exists for `render` (page/repage) — could
  extend to `expand`/`quiz` (they degrade quietly). Quiz: per-question-only evidence
  highlight, an ordering/sequence type. Reading-level: per-vault default, finer levels.

**The engine (`dwell.py`) stays pure — keep wrapping, never forking** (level/quiz/
re-pitch logic lives in the Renderer + server; the UI in the Svelte app).

## Phase 2 — the Svelte frontend (DONE; see the code map for the module list)
**`dwell-web/`** — a Svelte 5 + Vite (TypeScript) SPA, the real reading surface
(replaces tkinter). Run TWO processes: backend `python prototypes/dwell_server.py`
(:8000) and, in `dwell-web/`, `npm run dev` (:5173, proxies to :8000). See
`dwell-web/README.md`. The runes store (`dwell.svelte.ts`) holds all state; the
endless scroll is `pages[]`; flow is voice-driven (Play) or explicit picks.
**Two install/runtime gotchas:** (1) Vite 8 is rolldown-based; npm skipped its
native binding (npm/cli#4828) — pinned `@rolldown/binding-win32-x64-msvc` in
`package.json` fixes it. (2) **Audio + canvas animations only run in a FOREGROUND
tab** — a hidden/background tab suspends the AudioContext and pauses
`requestAnimationFrame`, so headless previews can't audition narration/motion
(verify via DOM/state, not by watching/listening).

## Phase 1 — the web server (DONE)
**`dwell_server.py`** is a thin FastAPI adapter over the engine. **`dwell_web.html`**
is a vanilla-JS test client (served at `/`). **`dwell_smoke.py`** is a stdlib
end-to-end test (dry + live). Launch: `Launch Dwell Server.bat` (or
`python prototypes/dwell_server.py`), then open http://127.0.0.1:8000/.
Deps: `pip install -r prototypes/requirements-server.txt` (all already in the env).

The server drives the SAME `Brain`/`Navigator`/`Renderer`/`TweenCache`/
`ReadingHistory` in the SAME plan→commit→predict→render→propose→prefetch order as
the tkinter UI, with the same two locks (per-session `alock`; `render_lock` for
foreground-vs-prefetch). Sessions are in-memory (`SESSIONS` dict, idle-evicted).

Endpoints (idempotent reads = GET; the two streams = POST):
- `GET  /vaults` — cheap fs scan of `DWELL_VAULT_ROOT` (**default `~/Dwell`**) + registered imports. Plus `/vault/import`, `DELETE /vault`, `/vault/cover`, `/vault-sources`, `/vault-cover`, and the whole `/learn/*` + `/learn/build*` surface (see "Since 2026-06-19").
- `POST /session {vault, voice?, engine?, dry?}` → `{session_id, menu, voices, …}`
  (loads the Brain; heavy, runs in a threadpool).
- `POST /page {session, action:first|auto|plan, plan_id?, start?, seed?, wander?, diffusing?}`
  (SSE) → `start` → `frame`* (each `data:{text}` is the full text-so-far) → `done`
  (`{text,node,title,mode,marker,recap,cost,branches[],steer_bucket,sources[]}`).
  `sources[]` = readable source titles (server resolves source-page ids → titles,
  cached). `seed` starts a thread at a specific node id (used by sidebar node clicks).
- `POST /steer {session, text}` → bends the next page (engine `apply_steering`).
- `GET  /branches?session=` → `propose()` directions (each `{plan_id,label,mode,
  node,title,ready,leap}`); also bundled in every `/page` `done`.
- `POST /expand {session, selected, before, after, mode}` (SSE) → in-place rework.
- `GET  /nodes?session=&top=` → top nodes by centrality (`top=0` = all, for search).
- `POST /tts {session?, text, voice?, speed?}` (SSE) → `clip`* events (`{text, b64}`
  per sentence WAV; **no `done` event — the stream closing is completion**, Kokoro
  server-side). `GET /tts/voices` → `{available, voices, default}`.
- `GET  /voices?session=` · `POST /voice {session,name}`.
- `POST /level {session, name}` → reading level (general/elementary/middle/high/college/
  scholar); `/session` also takes `level` and returns `level`/`levels`.
- `POST /repage {session, index}` (SSE) → re-render an already-composed page (by index)
  at the CURRENT level/voice WITHOUT advancing the nav — the in-place level re-pitch.
  (Server keeps each page's `{plan,tail,recap,hint}` in `DwellSession.page_renders`.)
- `POST /quiz {session, pages[], count, types[]}` → `{questions[], cost}` — a
  retrieval-practice quiz over the given page texts; mixed formats (choice/truefalse/
  cloze/recall/matching), `types[]` filters which (empty = all). Each question carries
  an `evidence` verbatim quote (for the open-book page highlight).
- `GET  /missed?session=&n=` · `GET /state?session=` · `POST /wander {session,value}`.
- **New endpoints MUST be added to the Vite proxy allowlist** (`apiRoutes` regex in
  `dwell-web/vite.config.ts`) + a Vite restart, or they 404 in dev (bit us 4×).

**SSE shape:** `sse_starlette` emits CRLF; every event's `data` is a JSON object
(sidesteps multi-line-prose pitfalls). A client must split events on `\r?\n\r?\n`
and `JSON.parse` the data — see `streamPost` in `dwell_web.html` and `stream()` in
`dwell_smoke.py`. **Deviation from the original plan:** `/page` is POST (not GET/
EventSource) so one fetch-stream path serves both `/page` and `/expand`; reads stay
GET. Verify: `python prototypes/dwell_smoke.py --live`.

## Code map (`prototypes/`)
- **`dwell.py` — the engine (UI-agnostic; reuse behind the API).** Key pieces:
  - `Brain.load(vault, embed_model, progress)` → vault graph + vector space + voices.
  - `Navigator` — `plan_first()`, `plan_auto()`, `propose(k)` (→ `(PagePlan,label)`,
    incl. ✦ leap), `commit(plan)`, `apply_steering(text)`, `hint_for`, `recap`.
  - `Renderer` — `render(plan, tail, recap, next_hint, on_stream=cb, diffusing=bool)`,
    `expand(selected, before, after, mode, on_stream)`, `_complete(...)` (the only
    provider-specific code; streams SSE for Mercury and Anthropic). `cache_key(plan)`.
  - `ReadingHistory`, `TweenCache`, `DenseSpace`/`TfidfSpace`, `PagePlan`,
    `missed_connections(brain, topn)`, `migrate_meta(vault)`, `_read_env_key(name)`.
  - Constants: `MERCURY_*`, `_OPENAI_PROVIDERS`, `VOICES`, `DEFAULT_VOICE`,
    `TWEEN_CACHE_FILE`, `HISTORY_FILE`, `EMBED_CACHE_FILE`, `PAGE_WORDS`, `TAIL_CHARS`.
- **`dwell_ui.py` — DELETED** (tkinter UI removed; the Svelte web app is the only UI).
- **`dwell_learn.py` — Learn intake router** (`/learn/*`): draft create, upload→`raw/uploads/`,
  manifest (links+prompt), curate + **content-hash dedup**, open-for-expand. Self-contained
  (reuses only its own vault helpers; no import cycle). Reused by `dwell_build.py`.
- **`dwell_build.py` — the ingest swarm** (`/learn/build*`): one cancellable `cli.py ingest`
  subprocess per source, SSE progress, Stop (kill), `dry` simulation. `BuildState`/`BUILDS`
  registry; same `_sse_from_thread` bridge shape as the page stream.
- **`dwell_tts.py` — Kokoro audio.** `Narrator` (desktop, plays on the server's
  device) PLUS the web synth used by the server: `web_tts_available()`,
  `synth_wavs(text,voice,speed)` (per-sentence WAV bytes, no audio device),
  `list_web_voices()`, `_pcm_to_wav`.
- **`dwell_server.py` — FastAPI adapter (Phase 1).** `DwellSession` (per-reader
  state + locks), `SESSIONS` dict, `_produce_page` (mirrors tkinter `_beat_worker`),
  `_schedule_prefetch`, `_sse_from_thread` (the async⇄thread bridge). Pure wrapper.
- **`dwell_web.html` — vanilla-JS test client (Phase 1 verify / Phase 2 seed).**
  `streamPost` = SSE-over-fetch; launch menu, streaming page, branch chips, steer,
  select→expand popover, voice/engine/diffuse/wander, missed-connections overlay.
- **`dwell_smoke.py` — stdlib e2e test** of every endpoint (`--live` adds a Mercury pass).
- **`dwell-web/` — the Svelte 5 + Vite frontend (the real app).** `src/lib/`:
  `api.ts` (typed client + `streamPost` SSE-over-fetch + `streamPage`/`streamRepage`/
  `streamExpand`/`streamTts`, `quiz`, `setVoice`/`setLevel`, `ttsVoices`),
  `dwell.svelte.ts` (the runes store — ALL state + orchestration: pages + **`cursor`/
  `zoom`** (the deck), flow, `requestAdvance`/`requestBeginAt` (TTS-gated), `togglePlay`
  recliner, `goPrev`/`goNext`/`goTo`, **quizzes** (`quizEvery`/`quizCount`/`quizTypes`/
  `quizDue`/`openQuiz`/`closeQuiz`), **levels** (`level`/`setLevel`/`relevel`/
  `READING_LEVELS`), `expand` (clarify), themes/bg/narration), `themes.ts`,
  `background.ts` (canvas + WebGL), `audio.ts` (`AudioNarrator` — gapless + word
  timeline + karaoke `onWord`/`onEnd` + a `baseOffset` for substring/resume narration),
  `types.ts` (`QuizQuestion`, …). Components: `Sidebar`, `SettingsWindow` (tabbed —
  **Dwell first/default**; Voice/Level/Quizzes cards), `IconRail`, `TopBar`, **`Reader`
  (the 3-card deck + pretext fit-to-page + zoom + swipe/arrows/pinch + select-to-clarify
  + karaoke + quiz-evidence highlight)**, `Branches`, `Transport`, `Missed`, **`Quiz`
  (the open-book quiz window)**; `App.svelte` composes them. Dev handles: `window.dwell`,
  `window.__dwellFit()` (`import.meta.env.DEV`). See `dwell-web/README.md`.

## Must-know facts & gotchas
- **Mercury key:** `INCEPTION_API_KEY` in `C:\Users\user\Downloads\Compendium\.env`.
  Model `mercury-2`, base `https://api.inceptionlabs.ai/v1`, OpenAI-compatible
  (`openai` SDK). `reasoning_effort`: **medium for pages, instant for expand,
  NEVER high** (high → empty completion). `MERCURY_MAX_TOKENS=8192`.
  `extra_body={"diffusing": true}` makes each stream chunk the full refining text.
  `DWELL_PROVIDER=mercury` in `.env` sets the default engine.
- **Windows:** prefix CLI with `PYTHONIOENCODING=utf-8`. When appending to `.env`,
  ensure a leading newline (a `>>` once welded `DWELL_PROVIDER` onto the key → 401).
- **Embeddings:** `all-MiniLM-L6-v2`, cached at `<vault>/wiki/_meta/.dwell-embeddings.json`
  (offline-first from the HF cache; TF-IDF fallback). Reading memory:
  `dwell-history.json`. Tween cache: `.dwell-tween-cache.json`.
- **Vault layout:** `<vault>/CLAUDE.md` + `<vault>/wiki/{concepts,entities,sources,
  syntheses}/*.md`; read via `compendium.vault` (`read_page` now coerces numeric
  YAML tags — `pages.py:_as_str_list`). Sources are substance, not navigable nodes.
- **Empty/0-node vaults** degrade gracefully (`Brain._build_space` early-returns).
- **Engine/UI separation is the asset** — keep `dwell.py` pure; the API is a thin adapter.
- **Headless/background-tab gotchas (this session):** `requestAnimationFrame` is PAUSED
  and CSS transitions FREEZE in a hidden tab — so fit-to-page runs via `queueMicrotask`
  (not rAF), and you must DISABLE transitions to measure deck-card positions (they never
  "settle" headless). Audio/canvas only run foreground. The preview viewport also
  collapses/fluctuates — set an explicit size before measuring geometry.
- **Reading level + clarify levels are placed LAST in the prompt** (a `level_tail` after
  `_RULES`) so they dominate the persona/voice register — in the system prompt alone the
  level got averaged out (an "elementary" page still read literary). Same recency lesson
  as the quiz prompt (rebuilt to Inception's Mercury 2 prompt guide).
- **`render()` retries once at `effort="low"`** — Mercury sometimes returns an empty
  completion (esp. the dense scholar prompt); the retry self-heals it.
- **Fit-to-page** = font-size scaling solved analytically with `@chenglou/pretext` (no
  DOM reflow); the card is `overflow:hidden` at zoom 1 (never scrolls) and `overflow-y:
  auto` when zoomed. Cache key is now `voice_id[:level]:plan.key()` (default level omitted
  so old caches stay valid; each level keeps its own pages).

## Run / verify
- **Full app (Phase 2):** backend `python prototypes/dwell_server.py` (:8000) +,
  in `prototypes/dwell-web/`, `npm run dev` (:5173 — open this). Tablet on LAN:
  `http://<ip>:5173/`. (`.claude/launch.json` has both as preview configs:
  `dwell-server`, `dwell-web`.)
- **Web server alone** (Phase 1): `Launch Dwell Server.bat` or
  `PYTHONIOENCODING=utf-8 python prototypes/dwell_server.py` → http://127.0.0.1:8000/
  (serves the throwaway `dwell_web.html` test client at `/`).
- **Server e2e test**: `PYTHONIOENCODING=utf-8 python prototypes/dwell_smoke.py [--live]`
  (dry pass is free; `--live` makes a few cheap Mercury calls).
- **Frontend type-check**: in `dwell-web/`, `npm run check` (svelte-check + tsc).
- Dry UI selftest (no API): `COMPENDIUM_DWELL_SELFTEST=1 PYTHONIOENCODING=utf-8 python prototypes/dwell_ui.py`
- CLI walk: `PYTHONIOENCODING=utf-8 python prototypes/dwell.py --vault "C:\Users\user\Downloads\Example Vault" --auto 3 [--dry] [--provider mercury]`
- Missed connections: `… --missed 20`
- Syntax: `python -c "import ast; ast.parse(open('prototypes/dwell_server.py',encoding='utf-8').read())"`

## Pointers
`DWELL_APP_PLAN.md` (the detailed plan), `MEMORY.md` + memory file
`project_the_current.md` (the full evolution and every locked decision).
