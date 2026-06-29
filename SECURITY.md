# Security model

Dwell is designed as a **local, single-user application**. By default the server
binds to `127.0.0.1` (localhost only), has **no authentication**, and uses a
permissive CORS policy (`*`) to support the separate Vite dev server. That is
appropriate for running it on your own machine and **not** safe to expose to an
untrusted network as-is.

## If you reach beyond localhost

Anyone who can reach the server's port can read your vaults, create/delete
vaults, and spend your configured API keys. Before binding to a non-loopback
address or putting Dwell behind a public URL, you should:

- Put it behind an authenticating reverse proxy (or add auth to the app).
- Restrict CORS (`web`/`dwell_server.py` `CORSMiddleware`) to known origins.
- Keep `DWELL_HOST=127.0.0.1` unless you've done the above.

## What is already hardened

- **No shell injection.** Subprocesses (the Learn build → `cli.py ingest`,
  `split-book`) are spawned with argument *lists* (`shell=False`), never a shell
  string.
- **Path-traversal guarded.** Vault paths are resolved and checked against the
  vault root (`_safe_vault`), and the `/asset` endpoint only serves files that
  resolve *inside* the active session's vault.
- **Keys are not echoed.** API keys live in `.env` or under your vault root
  (e.g. `~/Dwell/.dwell-*.json`); the API returns only `has_key`-style flags,
  never the secret. `.env` and those files are git-ignored.

## Things to be aware of

- **Outbound fetches.** `cli.py research` and link ingestion fetch arbitrary
  URLs that *you* supply (an SSRF surface). Only research/ingest sources you
  trust, especially if the server can reach internal network hosts.
- **Local code execution by design.** The ingest pipeline runs an LLM-driven
  REPL in a sandbox to process sources; treat ingested material as you would any
  input to an automated tool.

## Reporting a vulnerability

Please report security issues privately to the maintainer (open a GitHub
security advisory, or email the address in the repository's profile) rather than
in a public issue. Thanks!
