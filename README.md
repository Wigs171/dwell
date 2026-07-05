# Dwell

**A streaming, steerable reader for knowledge that builds itself.**

Dwell turns a folder of cross-linked Markdown — a *vault* — into an endless,
narrated, "rabbit-hole" reading experience. Instead of clicking through static
pages, you drift through the knowledge graph: each page is re-composed on the
fly in the voice, reading level, and form you choose, and you can *steer* where
the thread goes next or let it wander. A companion **Learn** mode builds new
vaults for you from your own files, links, or a topic, using an agent pipeline.

> Dwell ships with three small, original demo vaults — **Biology 101**, a
> Japanese-language **Cosmos** vault (perfect for showing off live translation), and
> **Cael Morren**, a fiction world built for guided paths, the creativity dial, and
> ghost doors — so you can try everything the moment the server starts.

---

## What's in the box

**Reading features:** twelve output *forms* (article, guided tour, Q&A, dialogue, story,
tutorial, brief, case study, interview, debate, letters, chronicle) that re-pitch the same
vault in place — arc-aware on a guided path (a tutorial's first beat orients, its last
consolidates); **Guided Paths** (a firm spine of beats with fluid tween corridors between
them, generated or authored) — every path plans a **through-line** up front (a premise
plus one causally-chained turn per gate, mined from the vault's own tensions), so a story
path actually goes somewhere and an expository path builds to something; **reading
levels** as real comprehension contracts (Elementary picks the one idea a child could
retell tomorrow — shorter pages, simpler world — while Scholar spends the space on
precision, from the same vault); a **creativity dial** from faithful conveyance to dreamed
scene; **ghost doors** — unwritten links render as threshold pages, and what you find is
staged as a *proposal* the vault can grow by (accepted only through a Learn build);
**OKF interop** — read any [Open Knowledge Format](docs/DWELL_OKF.md) bundle as a vault
and export any vault as one (lossless round trip, one click from the vault card).

**Narration:** local Kokoro voices (instant, free, karaoke-highlighted) — plus an
optional **cloud voice studio**: clone a narrator from a ~10-second recording or any
clip, design one from a text description, or pick presets across four hosted models,
each labeled with measured speed and honest per-page cost (bring your own
[fal.ai](https://fal.ai) key; cloning is consent-gated and the voice lives on your disk).


| Path | What it is |
|------|------------|
| `server/` | FastAPI backend (`dwell_server.py`) + the reader engine (`dwell.py`), Learn intake/build (`dwell_learn.py`, `dwell_build.py`), provider keys (`dwell_endpoints.py`), and optional TTS (`dwell_tts.py`). |
| `web/` | The frontend — Svelte 5 + Vite (TypeScript). The reading UI, themes, settings, narration, and the Learn screens. |
| `compendium/` | The vault engine + ingest agents that **Learn** drives to build vaults. |
| `cli.py` | Command-line vault builder: `init`, `ingest`, `research`, `loop`, `enrich`, `explore`, `split-book`. |
| `vaults/` | Your knowledge bases. Three bundled demos: **`Biology 101 (Demo)`**, **`Cosmos (Japanese Demo)`** (live translation), and **`Cael Morren`** — an original fiction world built for guided paths, the dream dial, and ghost doors. |
| `docs/` | Design docs and the [vault format spec](docs/VAULT_FORMAT.md). |
| `tests/` | A smoke test for the server (`dwell_smoke.py`). |

---

## ⚠️ The reader needs a Mercury (Inception) key

Dwell's reader is built on **Mercury**, a text-*diffusion* model from
[Inception](https://inceptionlabs.ai) (OpenAI-compatible API). This is a
deliberate design choice — the streaming, refine-in-place reading effect depends
on a diffusion LLM, so an ordinary autoregressive model can't be swapped in. Set
`INCEPTION_API_KEY` (in `.env` or the in-app Settings) to use the live reader.

> **Other diffusion engines:** Mercury is the only *wired-in* engine today, but the
> constraint is the category, not the vendor. Google's
> [DiffusionGemma](https://deepmind.google/models/gemma/diffusiongemma/)
> (open weights, Apache 2.0, `vllm serve google/diffusiongemma-26B-A4B-it` gives an
> OpenAI-compatible endpoint) is the first open alternative — ~24 GB+ of VRAM or a
> rented GPU, and note it commits 256-token blocks, so the whole-page live-morph
> visual may differ. Not integrated yet; if you wire it up, we'd love the PR.

Without it, the reader falls back to a free **"dry" mode** that stitches the raw
vault text together with no LLM — fine for verifying the app runs, but it is not
the real experience.

The **Learn** builder is separate and runs on **Anthropic Claude** by default
(set `ANTHROPIC_API_KEY`), or on any OpenAI-compatible provider you add in
Settings.

---

## Quickstart

Requires **Python 3.11+** and **Node 18+**.

```bash
git clone https://github.com/Wigs171/dwell.git dwell
cd dwell

# 1. Python deps
python -m venv .venv
# Windows:  .venv\Scripts\activate     |  macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

# 2. Keys
cp .env.example .env          # then edit: INCEPTION_API_KEY (reader), ANTHROPIC_API_KEY (Learn)

# 3. Build the frontend
cd web && npm install && npm run build && cd ..

# 4. Run
python server/dwell_server.py          # or:  ./run.sh   (macOS/Linux)
#                                       or:  server/Launch Dwell Server.bat  (Windows)
```

Open **http://127.0.0.1:8000** — the bundled demo vaults (Biology 101, the Japanese Cosmos vault, and the Cael Morren fiction world) are already there.

> **macOS / Linux note:** the frontend pins a Windows build of Vite's native
> bundler binding (`@rolldown/binding-win32-x64-msvc`) to work around
> [npm/cli#4828](https://github.com/npm/cli/issues/4828). If `npm ci` /
> `npm run build` fails on that binding on macOS/Linux, remove that line from
> `web/package.json` and `web/package-lock.json`, then run `npm install` so npm
> resolves your platform's binding. (Tracked as a known cross-platform issue.)

### Dev mode (hot reload)

Run the backend and the Vite dev server separately; Vite proxies the API to the
backend, so you get instant frontend reloads:

```bash
# terminal 1 — backend
python server/dwell_server.py
# terminal 2 — frontend
cd web && npm run dev          # http://localhost:5173
```

---

## First-run model downloads

Two optional capabilities fetch models on first use; both have automatic
fallbacks, so nothing is required up front:

- **Semantic embeddings** (`sentence-transformers`, ~a few hundred MB incl.
  PyTorch) power the best "next page" choices and the *missed-connections*
  feature. Install with `pip install -e ".[embeddings]"`. Without it, Dwell uses
  a built-in TF-IDF fallback.
- **Audio narration** (`kokoro-onnx`, ~hundreds of MB) gives high-quality local
  text-to-speech. Install with `pip install -e ".[tts]"`. Without it, the
  browser's Web Speech API is used.
- **More natural narration** (optional, recommended): Kokoro's default espeak
  phonemizer sounds flat; the [misaki](https://github.com/hexgrad/misaki) G2P (its
  purpose-built pronunciation dictionaries) makes the same voices markedly less
  robotic at no extra runtime cost. Enable with:

  ```bash
  pip install -e ".[tts-natural]"
  python -m spacy download en_core_web_sm     # small CNN model for POS tagging
  ```

  It's used automatically when present (espeak fallback for out-of-dictionary words);
  set `DWELL_TTS_G2P=espeak` to force the old path. *Install base misaki + spaCy only —
  do **not** `pip install misaki[en]`, which pulls a torch-heavy transformer dep.*
- **Cloud narration voices** (optional): `pip install fal-client` and add a
  [fal.ai](https://fal.ai) key (Settings → Read → Narration, or `FALAI_API_KEY` in
  `.env`) to unlock the voice studio — clone a narrator from a short recording or an
  uploaded clip (one-time enrollment; the voice embedding is stored under your vault
  root), design a voice from a text description, or use hosted preset voices. The
  picker shows each model's measured speed and rough per-page cost; Kokoro remains
  the local, free default.

---

## Building your own vaults

Two ways:

- **Learn tab (in the app):** create a knowledge base, add files (PDF / Markdown
  / text) and links, and run the ingest swarm with live progress, cost caps, and
  stop/resume. New vaults appear in your library when they finish.
- **CLI:** `python cli.py ingest <file-or-url> --vault vaults/my-topic` (run
  `python cli.py init vaults/my-topic --topic "..."` first). `cli.py research
  "<topic>"` will gather sources from the web and ingest them (needs a search
  provider — see `.env.example`).

See [docs/VAULT_FORMAT.md](docs/VAULT_FORMAT.md) for the on-disk format if you'd
rather hand-author one (that's how the demo vaults were made).

---

## Configuration

All settings can be provided via environment variables (or a `.env` file); API
keys can also be entered in the app's Settings UI, where they're stored under
your vault root and never committed.

| Variable | Purpose | Default |
|----------|---------|---------|
| `INCEPTION_API_KEY` | Mercury reader engine | — (dry mode if unset) |
| `ANTHROPIC_API_KEY` | Learn / ingest pipeline | — |
| `COMPENDIUM_SEARCH_PROVIDER` / `COMPENDIUM_SEARCH_API_KEY` | Web research (`tavily`/`brave`/`jina`) | none |
| `JINA_API_KEY` | Research + page-fetch via Jina | — |
| `FALAI_API_KEY` | Cloud narration voices (clone/design/presets via fal.ai) | — (Kokoro/local only) |
| `DWELL_VAULT_ROOT` | Where vaults live | `./vaults` |
| `DWELL_HOST` / `DWELL_PORT` | Server bind address | `127.0.0.1` / `8000` |

---

## Architecture in one paragraph

The vault is a content-neutral substrate: cross-linked Markdown pages with YAML
frontmatter. The **reader** (`server/dwell.py`) loads the vault, builds an
embedding space + the wikilink graph, and walks it — for each step it asks
Mercury to re-compose the underlying page's material into one narrated "page" in
the chosen voice/level/form/language, caching results so revisiting is free.
**Learn** (`server/dwell_learn.py` + `dwell_build.py`) drives the `compendium`
ingest pipeline (`cli.py ingest`/`research` as cancellable subprocesses) to turn
raw material into a vault. The **frontend** (`web/`) is a Svelte SPA the backend
serves in production and proxies to in dev.

---

## License

- **Code:** [Apache-2.0](LICENSE).
- **Demo vault content** (the `vaults/` demos): original, AI-generated material
  released under CC BY 4.0 (see each vault's `CREDITS.md`). Vaults *you* build carry
  whatever license their sources do — that's on you.

---

## Status & known gaps

This is an early public release. A few things to know:

- **Research prompts** in Learn need a web-search key — set Tavily / Brave / Jina in
  Settings → Learn → Web search (or via the variables in `.env.example`).
- The reader is **diffusion-only** by design; Mercury is the only wired-in engine
  today (see above for the first open-weights alternative, if you have the GPU).
- See [docs/](docs/) for design notes and the roadmap.
