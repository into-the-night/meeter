#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
if [[ ! -f "$SCRIPT_DIR/local.env" || ! -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  print -u2 "Meeter is not fully set up. Run ./setup-local.command first."
  exit 1
fi

source "$SCRIPT_DIR/local.env"
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export TRANSFORMERS_OFFLINE=1

if curl --silent --fail --max-time 2 http://127.0.0.1:4317/api/health >/dev/null 2>&1; then
  print "Meeter is already running at http://127.0.0.1:4317"
  exit 0
fi

exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/server.py"
