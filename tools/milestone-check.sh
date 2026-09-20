#!/usr/bin/env bash
# Run at the end of every goal, before writing goals/<lane>-<n>.done.md:
#   tools/milestone-check.sh <lane> [--all]      lane ∈ build | ui | gtm
#   tools/milestone-check.sh                      no lane: the whole suite is required (CI form)
# 1. tests/guard (required): user ~/.claude untouched since baseline; HARNESS_ROOT refusal.
# 2. the lane's own test paths (required).
# 3. with --all: every other lane's tests as well (advisory): reported, never fatal here. A red
#    advisory suite is another lane mid-commit; report it in a handoff, do not fix it, do not
#    wait for it. Without --all the check stops after step 2 (fast).
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
lane="${1:-}"
case "$lane" in
  build) own="tests/core tests/fakeclaude" ;;
  ui)    own="tests/ui" ;;
  gtm)   own="tests/packaging tests/scenario" ;;
  "")    own="ALL" ;;
  *) echo "milestone-check: unknown lane '$lane' (build|ui|gtm)"; exit 2 ;;
esac
echo "== required: tests/guard"
"$PY" -m pytest tests/guard -q || { echo "MILESTONE-CHECK FAILED: guard"; exit 1; }
if [ "$own" = "ALL" ]; then
  echo "== required: the whole suite (no lane given: CI form, everything required)"
  "$PY" -m pytest -q || { echo "MILESTONE-CHECK FAILED: full suite"; exit 1; }
  echo "MILESTONE-CHECK PASSED for all"; exit 0
fi
if [ -n "$own" ]; then
  own_existing=""; for d in $own; do [ -d "$d" ] && own_existing="$own_existing $d"; done
  echo "== required: $own_existing"
  "$PY" -m pytest $own_existing -q || { echo "MILESTONE-CHECK FAILED: $lane"; exit 1; }
fi
if [ "${2:-}" != "--all" ]; then echo "MILESTONE-CHECK PASSED for ${lane:-all} (own paths; add --all for the advisory run)"; exit 0; fi
echo "== advisory: the rest of the suite (other lanes; never fatal here)"
others=""; for d in tests/*/; do d=${d%/}; case " tests/guard $own " in *" $d "*) ;; *) others="$others $d";; esac; done
if "$PY" -m pytest $others -q; then echo "advisory: green"; else echo "advisory: RED in another lane's paths; report in a handoff, do not fix, do not wait"; fi
echo "MILESTONE-CHECK PASSED for ${lane:-all}"
