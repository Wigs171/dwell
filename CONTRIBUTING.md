# Contributing to Dwell

Thanks for your interest! Dwell is early and there's plenty to do.

## Dev setup

```bash
# Python (3.11+)
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[server,dev]"        # or: pip install -r requirements.txt

# Frontend (Node 18+)
cd web && npm install
```

Run the backend and the Vite dev server in two terminals (see the README's
"Dev mode"). The bundled `vaults/biology-101` vault gives you something to read
immediately.

## Before you open a PR

- **Python:** `ruff check .` (and `ruff format .`). Keep functions small and in
  the style of the surrounding code.
- **Frontend:** `cd web && npm run check` (svelte-check + `tsc`) must pass.
- **Smoke test:** start the server, then `python tests/dwell_smoke.py`
  (set `DWELL_SMOKE_VAULT` or pass `--vault` to choose a vault). Add `--live` to
  exercise a real Mercury pass.
- Keep PRs focused. Describe what changed and how you verified it.

## Project layout

See the table in the [README](README.md#whats-in-the-box). In short:
`server/` = backend + reader, `web/` = Svelte frontend, `compendium/` = the
vault/ingest engine that Learn drives, `cli.py` = the builder CLI,
`vaults/` = knowledge bases, `docs/` = design notes.

## Good first contributions

- A non-Mercury reader path, if/when a comparable open diffusion model exists.
- More demo vaults (original or clearly-licensed, AI-permissive sources).
- A clean cross-platform fix for the Vite/rolldown native binding (see the
  README "macOS / Linux note") so `npm ci` works everywhere without the win32 pin.

## Licensing of contributions

By contributing you agree your contributions are licensed under the repository's
[Apache-2.0](LICENSE) license. Don't add content under licenses that forbid
redistribution or AI use.
