#!/usr/bin/env bash
# End-to-end packaging check (spec 17, M10 part 1).
#
#   packaging/e2e-install.sh <scratch-dir>
#
# Builds the wheel, installs it as a uv tool into a HOME that did not exist a moment ago, runs
# the installed `hx` against a fresh instance, and proves three things:
#
#   1. the wheel actually carries the package data `hx install` needs (skeleton, skills, the
#      unit templates, the tested-versions list) — a package-data glob that silently stops
#      matching ships an hx whose install has nothing to copy, and no unit test notices;
#   2. every skeleton file the gtm lane authored lands in the instance byte-identical;
#   3. nothing in the whole sequence touches a Claude home — not the fresh one (Claude Code is
#      never launched, so `$HOME/.claude` must not exist afterwards) and not the real one
#      (`tools/claude-home-hash.sh` before and after must match).
#
# Everything happens under the scratch directory: a fresh HOME, and UV_TOOL_DIR,
# UV_TOOL_BIN_DIR and UV_CACHE_DIR beneath it, so the user's own uv tools and cache are
# untouched and the run is repeatable.
#
# Exits non-zero on the first failure, naming the step.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------------------------------------------------------------- reporting

STEP=""
step() { STEP="$1"; printf '\n== %s\n' "$1"; }
ok()   { printf '   ok  %s\n' "$1"; }
die()  { printf '\nFAILED at step: %s\n     reason: %s\n' "$STEP" "$1" >&2; exit 1; }

# ---------------------------------------------------------------- arguments

[ $# -eq 1 ] || { echo "usage: $(basename "$0") <scratch-dir>" >&2; exit 2; }
SCRATCH="$1"

case "$SCRATCH" in
  /*) ;;
  *) SCRATCH="$PWD/$SCRATCH" ;;
esac

# Never let a scratch path land in the user's Claude home, whatever was passed.
REAL_CLAUDE_HOME="${HX_USER_CLAUDE_HOME:-$HOME/.claude}"
case "$SCRATCH/" in
  "$REAL_CLAUDE_HOME"/*) echo "refusing a scratch dir inside $REAL_CLAUDE_HOME" >&2; exit 2 ;;
esac

step "0. scratch directory"
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH"
SCRATCH="$(cd "$SCRATCH" && pwd)"
ok "$SCRATCH"

command -v uv >/dev/null 2>&1 || die "uv is not on PATH"
ok "uv $(uv --version)"

# ------------------------------------------------- the user's Claude home, before

step "1. record the real ~/.claude manifest"
BEFORE="$SCRATCH/claude-home.before"
if [ -d "$REAL_CLAUDE_HOME" ]; then
  "$REPO/tools/claude-home-hash.sh" "$REAL_CLAUDE_HOME" > "$BEFORE" || die "could not hash $REAL_CLAUDE_HOME"
  ok "$(wc -l < "$BEFORE" | tr -d ' ') entries from $REAL_CLAUDE_HOME"
else
  : > "$BEFORE"
  ok "$REAL_CLAUDE_HOME does not exist; manifest is empty"
fi

# ------------------------------------------------------- a HOME of its own

step "2. fresh HOME and uv directories under the scratch dir"
export HOME="$SCRATCH/home"
export UV_TOOL_DIR="$SCRATCH/uv/tools"
export UV_TOOL_BIN_DIR="$SCRATCH/uv/bin"
export UV_CACHE_DIR="$SCRATCH/uv/cache"
mkdir -p "$HOME" "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR" "$UV_CACHE_DIR"
export PATH="$UV_TOOL_BIN_DIR:$PATH"
# hx must never derive anything from an inherited instance.
unset HARNESS_ROOT HARNESS_ID CLAUDE_CONFIG_DIR
ok "HOME=$HOME"
ok "UV_TOOL_DIR=$UV_TOOL_DIR"
ok "UV_TOOL_BIN_DIR=$UV_TOOL_BIN_DIR"
ok "UV_CACHE_DIR=$UV_CACHE_DIR"

# --------------------------------------------------------------- build

step "3. uv build"
DIST="$SCRATCH/dist"
uv build --out-dir "$DIST" "$REPO" > "$SCRATCH/build.log" 2>&1 || {
  tail -20 "$SCRATCH/build.log" >&2; die "uv build failed; see $SCRATCH/build.log"
}
WHEEL="$(ls "$DIST"/*.whl 2>/dev/null | head -1)"
[ -n "$WHEEL" ] || die "uv build produced no wheel in $DIST"
ok "$(basename "$WHEEL")"

# ------------------------------------------ the wheel carries its package data

step "4. the wheel carries the package data hx install needs"
REQUIRED_IN_WHEEL="
hx/skeleton/PARTNER.md
hx/skeleton/config/CLAUDE.md
hx/skeleton/companion/BASE.md
hx/skeleton/companion/roles/partner.md
hx/skeleton/companion/roles/backend-engineer.md
hx/skeleton/companion/roles/frontend-engineer.md
hx/skeleton/companion/roles/release-engineer.md
hx/skeleton/personas/partner/AGENTS.md
hx/skeleton/personas/backend-engineer/AGENTS.md
hx/skeleton/personas/frontend-engineer/AGENTS.md
hx/skeleton/personas/release-engineer/AGENTS.md
hx/skeleton/templates/work-item.md
hx/skeleton/templates/goal.md
hx/skeleton/templates/addendum.md
hx/skeleton/templates/worker/AGENTS.md
hx/skeleton/templates/worker/SUBAGENTS.md
hx/skeleton/templates/worker/harness.json
hx/skeleton/adapters/claude/install.sh
hx/skeleton/adapters/claude/start.sh
hx/skeleton/adapters/claude/seam-command
hx/skeleton/adapters/pi/install.sh
hx/skeleton/adapters/pi/start.sh
hx/skeleton/adapters/pi/seam-command
hx/skeleton/adapters/pi/extension/index.ts
hx/skeleton/config/partner/AGENTS.md
hx/skeleton/config/partner/SUBAGENTS.md
hx/skeleton/config/partner/harness.json
hx/skills/hx-partner/SKILL.md
hx/skills/hx-fleet/SKILL.md
hx/skills/hx-worker/SKILL.md
hx/skills/hx-memory/SKILL.md
hx/skills/hx-companion/SKILL.md
hx/packaging/tested-claude-versions.json
hx/ui/static/index.html
hx/ui/static/app.js
hx/ui/static/style.css
"
NAMES="$SCRATCH/wheel-names.txt"
python3 -c 'import sys, zipfile; print("\n".join(zipfile.ZipFile(sys.argv[1]).namelist()))' \
  "$WHEEL" > "$NAMES" || die "could not read $WHEEL"
missing=""
for entry in $REQUIRED_IN_WHEEL; do
  grep -qxF "$entry" "$NAMES" || missing="$missing $entry"
done
if [ -n "$missing" ]; then
  printf '   wheel is missing:\n' >&2
  for m in $missing; do printf '     %s\n' "$m" >&2; done
  case "$missing" in
    *hx/packaging/*|*hx/skeleton/personas/*) printf '\n   package data missing from the wheel. hx/packaging/** needs "packaging/**/*" and\n   hx/skeleton/** needs "skeleton/**/*" in [tool.setuptools.package-data] of\n   pyproject.toml (build lane; see handoff/gtm-to-build.md).\n' >&2 ;;
  esac
  case "$missing" in
    *hx/ui/static/*) printf '\n   hx/ui/static/** is what `hx ui` serves; it needs "ui/static/**/*" in\n   [tool.setuptools.package-data]. If a file was renamed, update this list\n   (gtm lane; see handoff/gtm-to-ui.md).\n' >&2 ;;
  esac
  die "wheel is missing package data"
fi
ok "$(wc -l < "$NAMES" | tr -d ' ') entries, all $(echo "$REQUIRED_IN_WHEEL" | grep -c .) required files present"

# Spec 16.1: the UI is vanilla JS and CSS from the package, "no build step, no CDN". The
# repo-side test asserts it too, but the wheel is where it would matter — a page that pulls a
# script from a CDN works on the machine that built it and fails on an air-gapped one.
# (Asked for by the ui lane, handoff/ui-to-gtm.md.)
CDN_SCAN="$(python3 -c '
import re, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
pages = ("hx/ui/static/index.html", "hx/ui/static/app.js")
print("\n".join(
    n + ": " + u
    for n in pages
    for u in re.findall(r"https?://[^\s<>)]+", z.read(n).decode())
))' "$WHEEL")" || die "could not scan the packaged UI for external URLs"
if [ -n "$CDN_SCAN" ]; then
  printf '   external URLs in the packaged UI:\n' >&2
  printf '     %s\n' "$CDN_SCAN" >&2
  die "the packaged UI references an external URL (spec 16.1: no CDN)"
fi
ok "the packaged UI references no external URL (spec 16.1: no CDN)"

# --------------------------------------------------------------- install

step "5. uv tool install"
uv tool install --python 3.14 "$WHEEL" > "$SCRATCH/install.log" 2>&1 || {
  tail -20 "$SCRATCH/install.log" >&2; die "uv tool install failed; see $SCRATCH/install.log"
}
HX="$UV_TOOL_BIN_DIR/hx"
[ -x "$HX" ] || die "no hx entry point at $HX"
[ -x "$UV_TOOL_BIN_DIR/hx-hook" ] || die "no hx-hook entry point in $UV_TOOL_BIN_DIR"
ok "hx        -> $HX"
ok "hx-hook   -> $UV_TOOL_BIN_DIR/hx-hook"

# ---------------------------------------------------------------- doctor

step "6. hx doctor from the installed tool"
set +e
"$HX" doctor > "$SCRATCH/doctor.log" 2>&1
DOCTOR_STATUS=$?
set -e
sed 's/^/   | /' "$SCRATCH/doctor.log"
# doctor exits 1 only on what it owns (python, tmux, git, a broken pinned binary).
[ "$DOCTOR_STATUS" -eq 0 ] || die "hx doctor exited $DOCTOR_STATUS (see $SCRATCH/doctor.log)"
ok "exit 0"

# --------------------------------------------------------------- install --skeleton-only

step "7. hx install --skeleton-only"
ROOT="$SCRATCH/hx"
"$HX" install --root "$ROOT" --skeleton-only > "$SCRATCH/skeleton.log" 2>&1 || {
  tail -20 "$SCRATCH/skeleton.log" >&2; die "hx install --skeleton-only failed"
}
ok "$(grep -c '^created' "$SCRATCH/skeleton.log") files created under $ROOT"

# ------------------------------------------- the skeleton landed byte-identical

step "8. every skeleton file landed byte-identical"
count=0
while IFS= read -r src; do
  rel="${src#"$REPO"/src/hx/skeleton/}"
  dst="$ROOT/$rel"
  [ -f "$dst" ] || die "skeleton file not installed: $rel"
  cmp -s "$src" "$dst" || die "skeleton file differs after install: $rel"
  count=$((count + 1))
done < <(find "$REPO/src/hx/skeleton" -type f ! -name '.*' | sort)
[ "$count" -gt 0 ] || die "no skeleton files found in the repo to compare"
ok "$count files identical to src/hx/skeleton/"

step "9. a fresh instance installs the Partner and no worker"
ids="$(find "$ROOT/config" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort | tr '\n' ' ')"
[ "$ids" = "partner " ] || die "config/ holds ids other than partner: $ids"
ok "config/ holds: $ids"

# ------------------------------------------------ nothing touched a Claude home

step "10. the fresh HOME has no .claude"
[ ! -e "$HOME/.claude" ] || die "$HOME/.claude exists; nothing in this sequence should create it"
ok "$HOME/.claude does not exist"

step "11. the real ~/.claude is unchanged"
AFTER="$SCRATCH/claude-home.after"
if [ -d "$REAL_CLAUDE_HOME" ]; then
  "$REPO/tools/claude-home-hash.sh" "$REAL_CLAUDE_HOME" > "$AFTER" || die "could not re-hash $REAL_CLAUDE_HOME"
else
  : > "$AFTER"
fi
if ! diff -u "$BEFORE" "$AFTER" > "$SCRATCH/claude-home.diff"; then
  sed 's/^/   | /' "$SCRATCH/claude-home.diff" >&2
  die "$REAL_CLAUDE_HOME changed during the run"
fi
ok "$REAL_CLAUDE_HOME manifest identical before and after"

printf '\n== PASS  wheel built, installed, instance created, no Claude home touched\n'
printf '   wheel    %s\n' "$WHEEL"
printf '   instance %s\n' "$ROOT"
