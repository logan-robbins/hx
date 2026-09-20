#!/usr/bin/env bash
# The M10 deploy proof for one machine (spec 17.2–17.4, 11).
#
#   packaging/e2e-deploy.sh <scratch-dir>
#
# `e2e-install.sh` proves the wheel builds, installs, and can create an instance skeleton. This
# goes the rest of the way: the *full* `hx install`, a real repo mirror, a sparse worktree with
# the product's own `.claude/` excluded, an agent launched into tmux, and the boot units
# rendered into the machine's own launchd/systemd directories — all inside a HOME that did not
# exist a moment ago, and with the real `~/.claude` proved untouched at the end.
#
# Nothing here reads or writes the user's Claude home. `--from-user-config` is pointed at a
# fake one this script builds, which is the whole reason that flag takes a path.
#
# Gate: `hx install` without `--skeleton-only` is build-4. While it still exits 2, this script
# prints the gate and exits 0 with `SKIPPED (waiting on build-4)`.
#
# Exits non-zero on the first failure, naming the step.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

STEP=""
step() { STEP="$1"; printf '\n== %s\n' "$1"; }
ok()   { printf '   ok  %s\n' "$1"; }
die()  { printf '\nFAILED at step: %s\n     reason: %s\n' "$STEP" "$1" >&2; exit 1; }

[ $# -eq 1 ] || { echo "usage: $(basename "$0") <scratch-dir>" >&2; exit 2; }
SCRATCH="$1"
case "$SCRATCH" in /*) ;; *) SCRATCH="$PWD/$SCRATCH" ;; esac

REAL_CLAUDE_HOME="${HX_USER_CLAUDE_HOME:-$HOME/.claude}"
case "$SCRATCH/" in
  "$REAL_CLAUDE_HOME"/*) echo "refusing a scratch dir inside $REAL_CLAUDE_HOME" >&2; exit 2 ;;
esac

step "0. scratch directory"
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
SCRATCH="$(cd "$SCRATCH" && pwd)"
ok "$SCRATCH"
command -v uv >/dev/null 2>&1 || die "uv is not on PATH"
command -v git >/dev/null 2>&1 || die "git is not on PATH"
command -v tmux >/dev/null 2>&1 || die "tmux is not on PATH"
ok "uv $(uv --version), $(git --version), $(tmux -V)"

step "1. record the real ~/.claude manifest"
BEFORE="$SCRATCH/claude-home.before"
if [ -d "$REAL_CLAUDE_HOME" ]; then
  "$REPO/tools/claude-home-hash.sh" "$REAL_CLAUDE_HOME" > "$BEFORE" || die "could not hash $REAL_CLAUDE_HOME"
  ok "$(wc -l < "$BEFORE" | tr -d ' ') entries from $REAL_CLAUDE_HOME"
else
  : > "$BEFORE"; ok "$REAL_CLAUDE_HOME does not exist; manifest is empty"
fi

step "2. fresh HOME and uv directories under the scratch dir"
export HOME="$SCRATCH/home"
export UV_TOOL_DIR="$SCRATCH/uv/tools"
export UV_TOOL_BIN_DIR="$SCRATCH/uv/bin"
export UV_CACHE_DIR="$SCRATCH/uv/cache"
mkdir -p "$HOME" "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR" "$UV_CACHE_DIR"
export PATH="$UV_TOOL_BIN_DIR:$PATH"
unset HARNESS_ROOT HARNESS_ID CLAUDE_CONFIG_DIR
ok "HOME=$HOME"

step "3. uv build and uv tool install"
DIST="$SCRATCH/dist"
uv build --out-dir "$DIST" "$REPO" > "$SCRATCH/build.log" 2>&1 \
  || { tail -20 "$SCRATCH/build.log" >&2; die "uv build failed"; }
WHEEL="$(ls "$DIST"/*.whl | head -1)"
uv tool install --python 3.14 "$WHEEL" > "$SCRATCH/install.log" 2>&1 \
  || { tail -20 "$SCRATCH/install.log" >&2; die "uv tool install failed"; }
HX="$UV_TOOL_BIN_DIR/hx"
[ -x "$HX" ] || die "no hx entry point at $HX"
ok "$(basename "$WHEEL") -> $HX"

# --------------------------------------------------------------------------- the gate

step "4. is the full hx install built yet?"
ROOT="$SCRATCH/hx"
set +e
GATE_OUT="$("$HX" install --root "$ROOT" 2>&1)"
GATE_STATUS=$?
set -e
case "$GATE_STATUS:$GATE_OUT" in
  2:*"not implemented"*)
    printf '   gate  %s\n' "$GATE_OUT"
    printf '\n== SKIPPED (waiting on build-4)\n'
    printf '   `hx install` without --skeleton-only is not built yet, so the deploy proof\n'
    printf '   cannot run. Everything before this point passed: the wheel builds, installs as\n'
    printf '   a uv tool, and its `hx` runs. Re-run this script when build-4 lands.\n'
    exit 0
    ;;
esac
ok "the full hx install is available (exit $GATE_STATUS)"
rm -rf "$ROOT"

# ------------------------------------------------- a fake user Claude home, and a product repo

step "5. a fake user ~/.claude to seed from"
FAKE_USER="$SCRATCH/fake-user-claude"
mkdir -p "$FAKE_USER"
cat > "$FAKE_USER/.credentials.json" <<'JSON'
{"claudeAiOauth": {"accessToken": "fake-token-for-the-deploy-proof", "scopes": ["user:inference"]}}
JSON
chmod 600 "$FAKE_USER/.credentials.json"
cat > "$FAKE_USER/settings.json" <<'JSON'
{
  "skipDangerousModePermissionPrompt": true,
  "theme": "dark",
  "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo USER-HOOK-LEAKED >&2; exit 2"}]}]}
}
JSON
cat > "$FAKE_USER/CLAUDE.md" <<'MD'
USER-CLAUDE-MD-LEAKED — this is the user's own CLAUDE.md and must not reach any agent home.
MD
mkdir -p "$FAKE_USER/skills/user-only-skill"
echo "USER-SKILL-LEAKED" > "$FAKE_USER/skills/user-only-skill/SKILL.md"
FAKE_BEFORE="$SCRATCH/fake-user.before"
"$REPO/tools/claude-home-hash.sh" "$FAKE_USER" > "$FAKE_BEFORE"
ok "$FAKE_USER with credentials, settings, a CLAUDE.md, and a skill"
ok "the settings carry a deny-everything hook and the CLAUDE.md a banner: both must NOT be copied"

step "6. a bare product repo with its own .claude/"
SRC="$SCRATCH/product-src"
cp -R "$REPO/tests/scenario/m8/repo" "$SRC"
mkdir -p "$SRC/.claude"
cat > "$SRC/.claude/settings.json" <<'JSON'
{"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo REPO-CLAUDE-DIR-LEAKED >&2; exit 2"}]}]}}
JSON
echo "REPO-CLAUDE-DIR-LEAKED" > "$SRC/.claude/notes.md"
git -C "$SRC" init -q -b main
git -C "$SRC" -c user.email=deploy@example.invalid -c user.name=deploy add -A
git -C "$SRC" -c user.email=deploy@example.invalid -c user.name=deploy commit -qm "product"
PRODUCT="$SCRATCH/product.git"
git clone -q --bare "$SRC" "$PRODUCT"
ok "$PRODUCT (bare), with .claude/settings.json and .claude/notes.md committed"

# --------------------------------------------------------------------------- hx install

step "7. hx install --from-user-config"
"$HX" install --root "$ROOT" --from-user-config "$FAKE_USER" > "$SCRATCH/deploy-install.log" 2>&1 \
  || { tail -30 "$SCRATCH/deploy-install.log" >&2; die "hx install --from-user-config failed"; }
sed 's/^/   | /' "$SCRATCH/deploy-install.log" | tail -20
ok "instance at $ROOT"

step "8. the seed credentials were copied, and nothing else was"
SEED="$ROOT/seed/home"
[ -f "$SEED/.credentials.json" ] || die "no $SEED/.credentials.json"
cmp -s "$FAKE_USER/.credentials.json" "$SEED/.credentials.json" \
  || die "$SEED/.credentials.json differs from the source credentials"
ok "seed/home/.credentials.json copied byte-identical"
perms=$(ls -l "$SEED/.credentials.json" | cut -c1-10)
case "$perms" in -rw-------) ok "mode 0600" ;; *) die "seed credentials are $perms, expected -rw-------" ;; esac
if [ -f "$SEED/settings.json" ]; then
  if grep -q "USER-HOOK-LEAKED" "$SEED/settings.json"; then
    die "the user's hooks were copied into seed/home/settings.json"
  fi
  ok "seed/home/settings.json carries no hook of the user's"
fi
if [ -f "$SEED/CLAUDE.md" ]; then
  die "the user's CLAUDE.md was copied into seed/home; only credentials and the bypass
  acceptance are copied (handoff/gtm-to-build.md, gtm-2 entry 4)"
fi
[ ! -d "$SEED/skills/user-only-skill" ] || die "the user's skills were copied into seed/home"
ok "no CLAUDE.md, no skills, no settings of the user's beyond the bypass acceptance"

step "9. the fake user home was only read"
"$REPO/tools/claude-home-hash.sh" "$FAKE_USER" > "$SCRATCH/fake-user.after"
diff -u "$FAKE_BEFORE" "$SCRATCH/fake-user.after" > "$SCRATCH/fake-user.diff" \
  || { sed 's/^/   | /' "$SCRATCH/fake-user.diff" >&2; die "hx install wrote to the user's Claude home"; }
ok "$FAKE_USER is byte-identical before and after"

step "10. config/claude.json records a bare version from the tested list"
CLAUDE_JSON="$ROOT/config/claude.json"
[ -f "$CLAUDE_JSON" ] || die "no $CLAUDE_JSON (spec 17.2 step 1)"
python3 - "$CLAUDE_JSON" "$REPO/src/hx/packaging/tested-claude-versions.json" <<'PY' || die "config/claude.json is wrong"
import json, re, sys
claude = json.load(open(sys.argv[1]))
tested = json.load(open(sys.argv[2]))["versions"]
assert set(claude) >= {"bin", "version"}, sorted(claude)
version = claude["version"]
assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{version!r} is not a bare version"
assert version in tested, f"{version} is not in the tested list {tested}"
print(f"   ok  bin={claude['bin']}")
print(f"   ok  version={version}, bare and in the tested list")
PY

# --------------------------------------------------------------------------- the mirror

step "11. hx repo add"
"$HX" repo add "$PRODUCT" > "$SCRATCH/repo-add.log" 2>&1 \
  || { tail -20 "$SCRATCH/repo-add.log" >&2; die "hx repo add failed"; }
REPO_JSON="$ROOT/config/repo.json"
[ -f "$REPO_JSON" ] || die "no $REPO_JSON (spec 17.2 step 4)"
NAME=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["name"])' "$REPO_JSON")
MIRROR="$ROOT/repos/$NAME.git"
[ -d "$MIRROR" ] || die "no bare mirror at $MIRROR"
[ "$(git -C "$MIRROR" rev-parse --is-bare-repository)" = true ] || die "$MIRROR is not bare"
git -C "$MIRROR" remote get-url upstream >/dev/null 2>&1 || die "$MIRROR has no upstream remote"
ok "config/repo.json name=$NAME"
ok "$MIRROR is a bare mirror of $(git -C "$MIRROR" remote get-url upstream)"

# ------------------------------------------------------------- launch, and the sparse worktree

step "12. hx launch eng-001 with the fake claude and a private tmux server"
mkdir -p "$ROOT/config/eng-001"
for f in AGENTS.md SUBAGENTS.md harness.json; do
  sed -e 's/{{id}}/eng-001/g' -e 's/{{pod}}/engineers/g' \
    "$ROOT/templates/worker/$f" > "$ROOT/config/eng-001/$f"
done
FAKE_BIN="$SCRATCH/bin"
mkdir -p "$FAKE_BIN"
cp "$REPO/tests/fakeclaude/claude" "$FAKE_BIN/claude"
chmod +x "$FAKE_BIN/claude"
export HX_CLAUDE_BIN="$FAKE_BIN/claude"
export HX_TMUX="tmux -L hx-deploy-$$"
export HX_SKILLS_DIR="$REPO/src/hx/skills"
trap '$HX_TMUX kill-server >/dev/null 2>&1 || true' EXIT

"$HX" launch eng-001 > "$SCRATCH/launch.log" 2>&1 \
  || { tail -30 "$SCRATCH/launch.log" >&2; die "hx launch eng-001 failed"; }
sed 's/^/   | /' "$SCRATCH/launch.log" | tail -8
$HX_TMUX has-session -t "=eng-001" 2>/dev/null || die "no tmux session eng-001 on the private server"
ok "tmux session eng-001 is live on the private server"

step "13. wt/eng-001 is a worktree without the product's .claude/"
WT="$ROOT/wt/eng-001"
[ -d "$WT" ] || die "no worktree at $WT"
[ -f "$WT/greet.py" ] || die "$WT does not hold the product"
[ ! -e "$WT/.claude" ] || die "$WT/.claude exists; the sparse checkout must exclude it (spec 17.3)"
if grep -rq "REPO-CLAUDE-DIR-LEAKED" "$WT" 2>/dev/null; then
  die "the product's .claude/ content is present in $WT"
fi
ok "$WT holds the product and no .claude/"
BRANCH=$(git -C "$WT" rev-parse --abbrev-ref HEAD)
[ "$BRANCH" = "agent/eng-001" ] || die "worktree is on branch $BRANCH, expected agent/eng-001"
ok "on branch agent/eng-001, cut from the mirror"

step "14. the agent home was written and seeded, not inherited"
AGENT_HOME="$ROOT/run/eng-001/home"
[ -f "$AGENT_HOME/settings.json" ] || die "no $AGENT_HOME/settings.json"
[ -f "$AGENT_HOME/.credentials.json" ] || die "no $AGENT_HOME/.credentials.json"
if grep -q "USER-HOOK-LEAKED" "$AGENT_HOME/settings.json"; then
  die "the user's hook reached the agent home"
fi
[ ! -e "$AGENT_HOME/skills/user-only-skill" ] || die "the user's skill reached the agent home"
if [ -f "$AGENT_HOME/CLAUDE.md" ] && grep -q "USER-CLAUDE-MD-LEAKED" "$AGENT_HOME/CLAUDE.md"; then
  die "the user's CLAUDE.md reached the agent home"
fi
python3 - "$AGENT_HOME/settings.json" <<'PY' || die "the agent settings are not what spec 11 requires"
import json, sys
s = json.load(open(sys.argv[1]))
assert s.get("skipDangerousModePermissionPrompt") is True, "no bypass acceptance"
mode = s["pluginConfigs"]["agents-md@builtin"]["options"]["instructionFiles"]
assert mode == "claude-md", mode
assert s["claudeMdExcludes"], "no claudeMdExcludes"
assert "crossSessionInbound" not in s, "crossSessionInbound is the Partner's alone (spec 11)"
events = sorted(s["hooks"])
print("   ok  hook events: " + ", ".join(events))
print("   ok  instructionFiles=claude-md, %d claudeMdExcludes globs" % len(s["claudeMdExcludes"]))
PY
[ -d "$AGENT_HOME/skills/hx-worker" ] || die "no hx-worker skill in the agent home (spec 17.5)"
[ ! -d "$AGENT_HOME/skills/hx-partner" ] || die "hx-partner is the Partner's skill alone"
ok "skills/hx-worker only"

# --------------------------------------------------------------------------- the units

step "15. the boot and heartbeat units were rendered into this HOME"
case "$(uname -s)" in
  Darwin) UNIT_DIR="$HOME/Library/LaunchAgents"; UNITS="com.hx.up.plist com.hx.heartbeat.plist" ;;
  *)      UNIT_DIR="$HOME/.config/systemd/user"; UNITS="hx-up.service hx-heartbeat.service hx-heartbeat.timer" ;;
esac
[ -d "$UNIT_DIR" ] || die "no $UNIT_DIR; hx install writes the units there (spec 17.2 step 5)"
for unit in $UNITS; do
  path="$UNIT_DIR/$unit"
  [ -f "$path" ] || die "no $path"
  if grep -q '{HARNESS_ROOT}' "$path"; then die "$path still holds an unsubstituted {HARNESS_ROOT}"; fi
  if grep -q '{HX_BIN}' "$path"; then die "$path still holds an unsubstituted {HX_BIN}"; fi
  grep -qF "$ROOT" "$path" || die "$path does not name this instance root"
  grep -qF "$HX" "$path" || die "$path does not name the installed hx binary"
  ok "$unit rendered with HARNESS_ROOT and HX_BIN substituted"
done

step "16. the real ~/.claude is unchanged"
AFTER="$SCRATCH/claude-home.after"
if [ -d "$REAL_CLAUDE_HOME" ]; then
  "$REPO/tools/claude-home-hash.sh" "$REAL_CLAUDE_HOME" > "$AFTER" || die "could not re-hash"
else
  : > "$AFTER"
fi
diff -u "$BEFORE" "$AFTER" > "$SCRATCH/claude-home.diff" \
  || { sed 's/^/   | /' "$SCRATCH/claude-home.diff" >&2; die "$REAL_CLAUDE_HOME changed during the run"; }
ok "$REAL_CLAUDE_HOME manifest identical before and after"

printf '\n== PASS  full install, mirror, sparse worktree, launch, units — no Claude home touched\n'
printf '   instance %s\n' "$ROOT"
printf '   worktree %s (no .claude/)\n' "$WT"
printf '   units    %s\n' "$UNIT_DIR"
