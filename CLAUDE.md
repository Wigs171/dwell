# CLAUDE.md — Dwell

Guidance for Claude Code (and other AI agents or contributors) working in this
repository. Claude Code loads this file automatically at the start of a session.

Dwell is a streaming, steerable reader for LLM-built knowledge **vaults**, plus an
in-app **Learn** builder that turns sources/prompts into a vault. See
[README.md](README.md) for the overview and [docs/](docs/) for the design docs
(CREED, ROADMAP, MERCURY_PROMPT_GUIDE, VAULT_FORMAT).

> This root `CLAUDE.md` is guidance, **not** a vault. Vaults live under
> `vaults/<name>/` and each carries its own `CLAUDE.md` (the vault schema +
> "is-a-vault" marker). Vault discovery only scans `vaults/`, so this file is
> never mistaken for one.

## Repo map
- `cli.py` — builder CLI (`init` / `ingest` / `research` / `loop` / `split-book` / `explore` / `enrich`)
- `compendium/` — vault engine + ingest agents (Router / PageWriter / Explorer / Reviewer)
- `server/` — FastAPI app (`dwell_server.py`), reader engine (`dwell.py`), Learn builder (`dwell_build.py`, `dwell_learn.py`), endpoints/keys (`dwell_endpoints.py`), TTS (`dwell_tts.py`)
- `web/` — Svelte 5 + Vite frontend; the `npm run build` output (`web/dist`) is served by the server
- `vaults/` — bundled demo vaults
- `docs/`, `tests/`

## Run / build / test
```bash
pip install -r requirements.txt          # Python deps (3.11+)
cd web && npm install && npm run build    # frontend (Node 18+)
python server/dwell_server.py             # serves web/dist at http://127.0.0.1:8000/
pytest -q                                 # tests
```
The **reader** is Mercury-only and needs `INCEPTION_API_KEY`; **Learn** needs an
LLM provider (`ANTHROPIC_API_KEY` or an endpoint configured in the app). See
`.env.example`. Common overrides: `DWELL_PORT`, `DWELL_VAULT_ROOT`.

## Conventions
- **Match the surrounding code** — naming, structure, and comment density.
- **Never commit secrets or build artifacts.** `.gitignore` already excludes
  `.env`, `.dwell-*.json`, `node_modules/`, `web/dist/`, caches, model weights,
  and non-demo vaults. Before committing, scan the staged diff for API keys or
  credentials and abort if any appear.
- **Keep commits focused** and write clear messages; add a `Co-Authored-By:`
  trailer when a commit was made with an AI assistant.
- **Run the checks** — `pytest -q`, plus `npm run check` in `web/` — before
  opening a pull request.
- To contribute, push to your fork and open a PR. See [CONTRIBUTING.md](CONTRIBUTING.md).
