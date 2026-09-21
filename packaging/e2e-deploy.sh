#!/usr/bin/env bash
# The deploy proof for one machine (spec 17.2).
#
#   packaging/e2e-deploy.sh <scratch-dir>
#
# `e2e-install.sh` proves the wheel builds, installs, and can create an instance skeleton.
# This goes the rest of the way: the *full* `hx install` — its four steps, including the exit-4
# stop for the seed token — and a worker launched into tmux, inside a HOME that did not exist a
# moment ago, ending with the real `~/.claude` proved untouched.
#
# That is the whole of deployment. hx ships no launchd plist and no systemd unit, creates no
# repository, cuts no worktree, and pushes nothing (spec 14 D25): `hx up` and `hx heartbeat`
# are ordinary commands a human puts in their own cron if they want them.
#
# Auth is one long-lived token per instance at `seed/token` (spec 11 Auth, CONTRACTS.md). hx
# reads nothing from any user Claude home, on any platform, so this script plants a fake
# `$HOME/.claude` full of strings that must never appear anywhere afterwards — that is how you
# tell "did not read it" from "did not look".
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
if ! command -v claude >/dev/null 2>&1; then
  printf '\n== SKIPPED (no claude binary)\n'
  printf '   `hx install` step 1 pins {bin, version} and checks the version against the\n'
  printf '   package tested list, so a real binary has to be on PATH. Nothing is launched\n'
  printf '   with it: HX_CLAUDE_BIN points every launch at the fake claude.\n'
  exit 0
fi
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

# ------------------------------------------------------ a planted user home, and a product

step "4. the fake claude, a private tmux server, and a planted ~/.claude"
FAKE_BIN_DIR="$SCRATCH/bin"
mkdir -p "$FAKE_BIN_DIR"
cp "$REPO/tests/fakeclaude/claude" "$FAKE_BIN_DIR/claude"
chmod +x "$FAKE_BIN_DIR/claude"
FAKE_BIN_EARLY="$FAKE_BIN_DIR/claude"
# `hx install` step 1 records {bin, version} and refuses a version outside the package's
# tested list, which the fake cannot satisfy and should not — that check is the point of the
# step. So the *real* binary is what gets pinned, and HX_CLAUDE_BIN points `start.sh` at the
# fake, so nothing in this proof ever starts a real agent.
export HX_CLAUDE_BIN="$FAKE_BIN_EARLY"
export HX_TMUX="tmux -L hx-deploy-$$"
export HX_SKILLS_DIR="$REPO/src/hx/skills"
trap '$HX_TMUX kill-server >/dev/null 2>&1 || true' EXIT
ok "fake claude at $FAKE_BIN_EARLY, tmux server hx-deploy-$$"

# hx must read nothing from any user Claude home, so plant one full of strings that would be
# unmistakable if they ever turned up in the instance.
FAKE_USER="$HOME/.claude"
mkdir -p "$FAKE_USER/skills/user-only-skill"
cat > "$FAKE_USER/.credentials.json" <<'JSON'
{"claudeAiOauth": {"accessToken": "USER-CREDENTIALS-LEAKED", "scopes": ["user:inference"]}}
JSON
chmod 600 "$FAKE_USER/.credentials.json"
cat > "$FAKE_USER/settings.json" <<'JSON'
{
  "theme": "dark",
  "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo USER-HOOK-LEAKED >&2; exit 2"}]}]}
}
JSON
echo "USER-CLAUDE-MD-LEAKED" > "$FAKE_USER/CLAUDE.md"
echo "USER-SKILL-LEAKED" > "$FAKE_USER/skills/user-only-skill/SKILL.md"
FAKE_BEFORE="$SCRATCH/fake-user.before"
"$REPO/tools/claude-home-hash.sh" "$FAKE_USER" > "$FAKE_BEFORE"
ok "$FAKE_USER with credentials, a deny-everything hook, a banner CLAUDE.md and a skill"

step "5. hx install stops for the seed token, and says how to make one"
ROOT="$SCRATCH/hx"
set +e
"$HX" install --root "$ROOT" > "$SCRATCH/install-1.log" 2>&1
FIRST_STATUS=$?
set -e
[ "$FIRST_STATUS" -eq 4 ] || die "expected exit 4 until seed/token exists, got $FIRST_STATUS"
grep -q "claude setup-token" "$SCRATCH/install-1.log" \
  || die "the stop does not tell the human the command to run"
grep -q "reads nothing from your own" "$SCRATCH/install-1.log" \
  || die "the stop does not say that hx reads nothing of the user's"
ok "exit 4, naming the setup-token command and the path to paste into"
[ ! -s "$ROOT/seed/token" ] || die "a token appeared without the human pasting one"

step "6. paste a token, and install again"
mkdir -p "$ROOT/seed"
printf 'deploy-proof-token-not-a-real-credential\n' > "$ROOT/seed/token"
chmod 600 "$ROOT/seed/token"
"$HX" install --root "$ROOT" \
  > "$SCRATCH/install-2.log" 2>&1 \
  || { tail -30 "$SCRATCH/install-2.log" >&2; die "hx install failed with a token present"; }
sed 's/^/   | /' "$SCRATCH/install-2.log" | tail -12
ok "instance at $ROOT"

step "7. the token is 0600 and no credentials file was created anywhere"
token_mode=$(python3 -c 'import os,sys;print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$ROOT/seed/token")
[ "$token_mode" = "0o600" ] || die "seed/token is $token_mode, expected 0o600"
ok "seed/token mode $token_mode"
creds=$(find "$ROOT" -name '.credentials.json' 2>/dev/null | head -5)
[ -z "$creds" ] || die "a credentials file exists in the instance: $creds"
ok "no .credentials.json anywhere under the instance"

step "8. nothing of the user's Claude home reached the instance"
for planted in USER-CREDENTIALS-LEAKED USER-HOOK-LEAKED USER-CLAUDE-MD-LEAKED USER-SKILL-LEAKED; do
  if grep -rq "$planted" "$ROOT" 2>/dev/null; then
    grep -rl "$planted" "$ROOT" 2>/dev/null | sed 's/^/     /' >&2
    die "$planted reached the instance"
  fi
done
ok "none of the four planted strings appears anywhere under $ROOT"
"$REPO/tools/claude-home-hash.sh" "$FAKE_USER" > "$SCRATCH/fake-user.after"
diff -u "$FAKE_BEFORE" "$SCRATCH/fake-user.after" > "$SCRATCH/fake-user.diff" \
  || { sed 's/^/   | /' "$SCRATCH/fake-user.diff" >&2; die "hx wrote to the user's Claude home"; }
ok "$FAKE_USER is byte-identical before and after"

step "9. config/claude.json records a bare version from the tested list"
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

step "10. hx creates no repository, no worktree, and no unit files"
# Spec 14 D25: working directories are not hx's business and hx ships no unit files. An
# instance that grew any of these would mean the cut did not take.
for stray in repos wt; do
  [ ! -e "$ROOT/$stray" ] || die "$ROOT/$stray exists; hx manages no git (spec 17.2)"
done
[ ! -e "$ROOT/config/repo.json" ] || die "config/repo.json exists; hx mirrors nothing"
for unit_dir in "$HOME/Library/LaunchAgents" "$HOME/.config/systemd/user"; do
  if [ -d "$unit_dir" ] && [ -n "$(ls -A "$unit_dir" 2>/dev/null)" ]; then
    ls -A "$unit_dir" >&2
    die "$unit_dir is not empty; hx ships no launchd plist and no systemd unit"
  fi
done
ok "no repos/, no wt/, no config/repo.json, no unit files written anywhere"

step "11. hx launch eng-001 into the workdir its harness.json names"
# From here on, act the way the Partner does: HARNESS_ROOT selects the instance, and the
# worker's workdir is a directory somebody chose — not one hx created (spec 17.2).
export HARNESS_ROOT="$ROOT"
WORKDIR="$SCRATCH/work/eng-001"
mkdir -p "$WORKDIR"
mkdir -p "$ROOT/config/eng-001"
for f in AGENTS.md SUBAGENTS.md harness.json; do
  sed -e 's/{{id}}/eng-001/g' -e 's/{{pod}}/engineers/g' \
    "$ROOT/templates/worker/$f" > "$ROOT/config/eng-001/$f"
done
python3 - "$ROOT/config/eng-001/harness.json" "$WORKDIR" <<'PY'
import json, sys
path, workdir = sys.argv[1], sys.argv[2]
cfg = json.load(open(path))
cfg["workdir"] = workdir
cfg.pop("branch", None)
with open(path, "w") as handle:
    json.dump(cfg, handle, indent=2)
    handle.write("\n")
PY
"$HX" launch eng-001 > "$SCRATCH/launch.log" 2>&1 \
  || { tail -30 "$SCRATCH/launch.log" >&2; die "hx launch eng-001 failed"; }
sed 's/^/   | /' "$SCRATCH/launch.log" | tail -8
$HX_TMUX has-session -t "=eng-001" 2>/dev/null || die "no tmux session eng-001 on the private server"
ok "tmux session eng-001 is live on the private server"
[ -f "$ROOT/pods/engineers/eng-001-idle.md" ] || die "no idle work item at pods/engineers/eng-001-idle.md"
ok "work item pods/engineers/eng-001-idle.md, workdir $WORKDIR"

step "12. the agent home was written from the package, and holds no credential"
AGENT_HOME="$ROOT/run/eng-001/home"
[ -f "$AGENT_HOME/settings.json" ] || die "no $AGENT_HOME/settings.json"
# Auth is the instance token exported at launch, so an agent home holds no credential at all.
[ ! -e "$AGENT_HOME/.credentials.json" ] || die \
  "$AGENT_HOME/.credentials.json exists; auth is seed/token exported as CLAUDE_CODE_OAUTH_TOKEN
  and agent homes hold no credentials file (spec 11 Auth)"
# The token must reach the agent as CLAUDE_CODE_OAUTH_TOKEN in its environment and nowhere
# else. `fake-argv*.json` is the fake claude recording its own argv and env for tests to
# assert on — a real binary writes no such file — so they are excluded from the "nowhere else"
# scan and the main-session one is used for the positive half instead. The Companion writes
# its own name; both records carry the token env, so both are excluded.
ARGV_JSON="$ROOT/run/partner/fake-argv.json"
if [ -f "$ARGV_JSON" ]; then
  python3 - "$ARGV_JSON" <<'PY' || die "the launch environment is not what spec 11 Auth requires"
import json, sys
rec = json.load(open(sys.argv[1]))
env = rec.get("env", rec)
assert env.get("CLAUDE_CODE_OAUTH_TOKEN"), "the token did not reach the session env"
argv = rec.get("argv", [])
joined = " ".join(argv)
assert "CLAUDE_CODE_OAUTH_TOKEN" not in joined, f"the token is in argv: {joined}"
assert env["CLAUDE_CODE_OAUTH_TOKEN"] not in joined, "the token value is in argv"
print("   ok  the token reached the session as CLAUDE_CODE_OAUTH_TOKEN, and is not in argv")
PY
fi
leaked=$(grep -rl "deploy-proof-token-not-a-real-credential" "$ROOT/run" 2>/dev/null \
  | grep -v 'fake-argv' || true)
if [ -n "$leaked" ]; then
  printf '%s\n' "$leaked" | sed 's/^/     /' >&2
  die "the instance token was written under run/; it belongs only in seed/token"
fi
ok "the token appears nowhere under run/ that hx wrote"
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

step "13. the real ~/.claude is unchanged"
AFTER="$SCRATCH/claude-home.after"
if [ -d "$REAL_CLAUDE_HOME" ]; then
  "$REPO/tools/claude-home-hash.sh" "$REAL_CLAUDE_HOME" > "$AFTER" || die "could not re-hash"
else
  : > "$AFTER"
fi
diff -u "$BEFORE" "$AFTER" > "$SCRATCH/claude-home.diff" \
  || { sed 's/^/   | /' "$SCRATCH/claude-home.diff" >&2; die "$REAL_CLAUDE_HOME changed during the run"; }
ok "$REAL_CLAUDE_HOME manifest identical before and after"

printf '\n== PASS  install with a seeded token, a launched worker — no Claude home touched\n'
printf '   instance %s\n' "$ROOT"
printf '   workdir  %s (chosen, not created by hx)\n' "$WORKDIR"
