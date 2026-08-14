#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
BPS_PYTHON="${BPS_PYTHON:-$REPO_DIR/venv/bin/python}"

if [[ ! -x "$BPS_PYTHON" ]]; then
  echo "BPS Python is not executable: $BPS_PYTHON" >&2
  echo "Set BPS_PYTHON to the intended interpreter." >&2
  exit 1
fi

export BPS_PYTHON
export PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$BPS_PYTHON" "$SCRIPT_DIR/run_linux.py" "$@"
