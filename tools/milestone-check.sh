#!/usr/bin/env bash
# Run at the end of every goal in every lane, before writing goals/<lane>-<n>.done.md.
# 1. guard tests: user ~/.claude untouched since baseline; HARNESS_ROOT refusal.
# 2. the whole test suite.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pytest tests/guard -q
python3 -m pytest -q
