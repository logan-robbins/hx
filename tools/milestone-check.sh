#!/usr/bin/env bash
# Run at the end of every goal in every lane, before writing goals/<lane>-<n>.done.md.
# 1. guard tests: user ~/.claude untouched since baseline; HARNESS_ROOT refusal.
# 2. the whole test suite.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
"$PY" -m pytest tests/guard -q
"$PY" -m pytest -q
