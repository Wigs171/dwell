#!/usr/bin/env bash
# Launch the Dwell server on macOS/Linux.
# Windows: use "server/Launch Dwell Server.bat" or run `python server/dwell_server.py`.
#
# The bundled Biology 101 demo vault (./vaults) is found automatically.
# Override the vault library location with DWELL_VAULT_ROOT.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
exec python server/dwell_server.py
