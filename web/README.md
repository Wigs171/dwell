# Dwell — web frontend

A Svelte 5 + Vite single-page app: the real reading surface for Dwell, built on
the FastAPI engine (`../dwell_server.py`). It replaced the tkinter UI. The reader is
a **PDF-style deck of page cards** (prev · current · next) with **fit-to-page zoom**,
**retrieval-practice quizzes**, and a **reading-level** axis that re-pitches the same
vault from elementary to scholar — see "What works" below.

## Run it (two processes)

1. **Backend** (the engine) — from the repo root:
   ```
   PYTHONIOENCODING=utf-8 python prototypes/dwell_server.py     # → http://127.0.0.1:8000
   ```
2. **Frontend** (this app) — from `prototypes/dwell-web/`:
   ```
   npm install        # first time (see the rolldown note below)
   npm run dev        # → http://127.0.0.1:5173   (open this)
   ```
   Vite proxies all API calls (`/vaults`, `/session`, `/page`, …) to the backend
   on :8000 (see `vite.config.ts`), so the browser sees one same-origin host.
   `npm run dev -- --host` is already on (`server.host`), so a tablet on the LAN
   can open `http://<your-ip>:5173/`.

Build for production: `npm run build` → `dist/` (Phase 4 will have FastAPI serve it).

## ⚠️ rolldown native binding (npm bug)
Vite 8 is rolldown-based and needs a platform native binding that npm sometimes
skips (npm/cli#4828 — symptom: *"Cannot find module @rolldown/binding-win32-x64-
msvc"*). It's pinned in `package.json` (`@rolldown/binding-win32-x64-msvc`) so a
normal `npm install` pulls it. On a different OS/arch, install the matching
`@rolldown/binding-<platform>` (same version as `rolldown`), or downgrade to Vite 7.

## Layout
- `src/lib/types.ts` — payload types mirroring the server.
- `src/lib/api.ts` — typed endpoint client + `streamPost` (SSE-over-fetch; splits
  on `/\r?\n\r?\n/` because sse_starlette emits CRLF; each event `data` is JSON).
- `src/lib/themes.ts` — the **Odysseus theme palettes** (5-color model:
  bg/fg/panel/border/accent + `light` flag) + per-theme `THEME_BG` (signature
  background) + `BG_PATTERNS`. `writeTheme(theme)` writes the 5 base CSS vars onto
  `:root`; `app.css` derives everything else via `color-mix`. A pre-paint snippet
  in `index.html` applies the saved theme + density before mount (no flash) — keep
  its inlined palette in sync with this file.
- `src/lib/dwell.svelte.ts` — the app store (Svelte 5 runes). All state +
  orchestration: load session, `begin`/`beginAt(seed)`, `advance` (first/flow/branch),
  `requestAdvance`/`requestBeginAt` (TTS-gated navigation), `togglePlay` (recliner); the
  **deck** (`pages[]` + `cursor` + `zoom`, `goPrev`/`goNext`/`goTo`); **quizzes**
  (`quizEvery`/`quizCount`/`quizTypes`/`quizDue`/`openQuiz`/`closeQuiz`); **reading
  levels** (`level`/`setLevel`/`relevel`/`READING_LEVELS`); `steer`, `expand` (clarify),
  narration (`narrate`/`spoken`/`onNarrationEnd`), themes/bg, voices, missed. Dev
  handles: `window.dwell`, `window.__dwellFit()` (`import.meta.env.DEV`).
- `src/lib/background.ts` — the canvas + WebGL animation engine (faithful port of
  the Odysseus effects) + `--bg-effect-*` setters + frosted toggle.
- `src/lib/audio.ts` — `AudioNarrator`: streams `/tts` clips, plays them gaplessly
  via Web Audio, builds a **word timeline** and fires `onWord` (karaoke) + `onEnd`
  (drives auto-advance); Web Speech fallback. (Backend: `dwell_tts.py` `synth_wavs`
  + `/tts` SSE.)
- `src/lib/*.svelte` — `Sidebar` (node-focused: search · reading trail · popular
  nodes · missed; bottom user-bar with the ⚙ gear), `SettingsWindow` (draggable,
  **non-blocking, undimmed** tabbed window — **Dwell (default) / Themes / Customize**;
  the Dwell tab has Engine/Voice/**Level**/Wander + Narration + a **Quizzes** card),
  `IconRail` (48px collapsed sidebar), `TopBar` (title + node source), `LaunchMenu`,
  **`Reader` (the 3-card deck: prev·current·next + a "compose next" ghost; fit-to-page
  zoom via `@chenglou/pretext`; swipe/arrows/pinch; select-to-clarify; karaoke +
  auto-scroll; quiz-evidence highlight)**, `Branches`, `Transport` (Play / Steer / New
  thread), **`Quiz` (the open-book quiz window: 5 formats, draggable, non-blocking,
  answer highlights)**, `Missed`. `src/App.svelte` is the sidebar/icon-rail + main shell
  + the Quiz/Settings windows.

## What works (verified in-browser, live Mercury)
**The reading experience (this session, 2026-06-19/20):**
- **Reading deck + zoom** — the page is a **card** (prev · current · next + a "compose
  next" ghost); flip with hover-arrows / swipe / ←→; **pinch / Ctrl-wheel / +−0 zoom**.
  **Fit-to-page**: zoom 1 shows the whole page (no scroll), zoom>1 enlarges + scrolls
  (narration autoscroll follows the spoken word then). Fit is analytic via
  **`@chenglou/pretext`** (no DOM reflow). Text is **justified + hyphenated**. Deck
  spacing is modular (a `--peek` sliver; the card is sized to the stage, not the viewport).
- **Select-to-clarify** — select a passage → **Simpler / ✦ More** in a draggable,
  non-blocking popover; re-pitches it in place and **re-narrates** the changed text.
- **Quizzes (retrieval practice)** — every N pages, a quiz over the previous N (from the
  cached page text), in a varied mix of **5 formats** (multiple-choice / true-false /
  cloze / free-recall / matching), each graded inline. A draggable, **open-book** window
  (flip back through the pages for answers; each answer's evidence is highlighted in the
  pages). Settings → Dwell → Quizzes: on/off · every-N (2–20) · count (3–25) · per-type
  toggles. The quiz inherits the reading level.
- **Reading levels** — re-pitch the SAME vault from **elementary → scholar** (Settings →
  Dwell → Reading → Level). Changing it re-renders the current page **in place**; if
  narration was playing it resumes at ~the sentence you were on. Voice (how it sounds)
  and level (how complex) are orthogonal axes; each level caches its own pages.

**The Odysseus UI port:**
- **Settings live in a tabbed window** (Odysseus style) — a ⚙ gear in the
  sidebar's bottom user-bar (and the icon-rail) opens a draggable window with
  three tabs: **Themes** (a swatch grid of presets + your custom themes — click to
  apply), **Customize** (the theme editor: circular color pickers w/ reset for the
  5 colors + save-as-custom · background pattern/intensity/size/effect-color ·
  frosted · density), and **Dwell** (engine/voice/wander/diffuse/free + a
  Narration card: read-aloud toggle, voice, speed). The sidebar body itself is
  **node-focused** (search, reading trail, popular nodes). Other windows (Missed
  connections) share the same chrome.
- **Animated backgrounds (ported 1:1 from Odysseus)** — 15 canvas effects
  (`synapse rain constellations perlin-flow petals sparkles embers aurora
  glyph-rain retro-grid fireflies bubbles ripples snow` + static `dots`) and 3
  **WebGL2 fragment-shader** effects (`caustics silk topo`, auto-fallback to a 2D
  cousin if WebGL2 is unavailable). Each theme ships a signature one; intensity /
  size / effect-color sliders + **frosted glass** (real recipe: 32% tint +
  `blur(24px) saturate(170%)` + gradient sheen). Plays behind the reading cards.
  NB: `requestAnimationFrame` is paused in a hidden/background tab, so the canvas
  only animates in a **foreground** window.
- **16 themes + a custom theme editor** (author/save/delete palettes, live
  whole-app preview, persisted & restored). Whole UI incl. the reading page
  re-themes; reading text stays serif.
- **Sidebar**: node search → jump to any node; reading trail; popular nodes by
  centrality (click → thread seeded there); collapsible; collapses to a 48px
  icon-rail. **Density** compact/comfortable/spacious.
- **Audio narration with karaoke** — server-side **Kokoro TTS** (54 voices)
  streamed per sentence from `POST /tts` (SSE base64 WAV), played **gaplessly** via
  Web Audio. The **word being spoken is highlighted** in the theme accent (CSS
  Custom Highlight API + a client word-timeline) and the page **auto-scrolls to
  follow** it. A single **▶ Play / ⏸ Pause** button is the recliner: it reads the
  page, then on finish follows a **queued direction** (a branch/node you clicked
  while it read) else **autoplays the default flow path**. Voice + speed + a
  read-aloud toggle live in the Dwell settings tab. Falls back to the browser's
  SpeechSynthesis (real word boundaries) if `/tts` is down. NB: the `/tts` stream
  has no `done` event — its closing IS completion (the narrator marks done on
  stream-resolve, which is what drives the auto-advance).
- Plus the reader: launch menu · live diffusing stream · endless scroll (prefetch
  → free replay, shown `· Dwell`) · branches ("↻ Dwell here" + node titles; the
  near-but-unlinked node is just another path) · steer · select-to-expand · the
  **node's source shown in the title bar**.
- **Everything audio/animation runs only in a FOREGROUND tab** (a hidden tab
  suspends the AudioContext + `requestAnimationFrame`).

### Themes & background — how it works
`themes.ts` holds the palettes + per-theme `THEME_BG` (pattern/intensity/color/
frosted). `writeTheme(t)` writes the 5 base vars on `:root`; `app.css` derives the
rest via `color-mix`. `background.ts` runs the canvas effects (a faithful port of
the Odysseus effects) behind a transparent app shell; `--bg-effect-intensity`
(canvas opacity), `--bg-effect-size`, `--bg-effect-color` tune them. Persistence:
`localStorage` keys `dwell-theme`, `dwell-custom-themes`, `dwell-density`,
`dwell-bg-pattern`/`-intensity`/`-size`/`-color`, `dwell-frosted`. The pre-paint
`<script>` in `index.html` restores theme colors + density before mount (no flash)
— **keep its inlined palette in sync with `themes.ts`.** NB: browsers pause
`requestAnimationFrame` in a hidden/backgrounded tab, so the canvas only animates
in a foreground window (this is why headless screenshots show a single frame).

## Next
Done this session (2026-06-19/20): the card deck + **pretext** fit-to-page + zoom,
justified text, the select-to-clarify redesign, **quizzes** (5 formats, open-book,
settings), **reading levels** (in-place re-pitch + narration handover), and non-blocking
Settings. **Open:** the **⚡ tension marker + vault-mode gating** are **shelved** (user's
call). **Phase 4** — PWA install + FastAPI-serves-`dist/` so the tablet experience the
deck/zoom were built for is real — is deprioritised but the obvious next big step. Minor:
extend the render retry to quiz/expand; per-question-only quiz highlight; an
ordering/sequence question type.
