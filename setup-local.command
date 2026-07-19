#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
cd "$SCRIPT_DIR"
export UV_CACHE_DIR="$SCRIPT_DIR/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$SCRIPT_DIR/.python"
export HF_HOME="$SCRIPT_DIR/models/.hf-cache"
export HF_HUB_DISABLE_TELEMETRY=1

if [[ ! -x .venv/bin/python ]]; then
  uv venv --python 3.12 .venv
else
  print "✓ Existing isolated Python environment"
fi
uv pip install --python .venv/bin/python -r requirements-local-models.txt
.venv/bin/python setup_models.py

print ""
print "Meeter is ready. Start it with: ./run-meeter.command"
