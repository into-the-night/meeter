#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]] || ! "$PYTHON" -c "import mcp" >/dev/null 2>&1; then
  PYTHON=$(command -v python3)
fi

exec "$PYTHON" "$SCRIPT_DIR/mcp_server.py" "$@"
