#!/usr/bin/env bash
# adapters/codex/install.sh <id> — write the per-agent Codex home.
#
# The home is CODEX_HOME for this agent. It holds config.toml (unattended
# posture, the hx hooks) and skills. Auth is the invoker's ChatGPT login,
# copied from $HOME/.codex/auth.json into the agent home — the CLI refreshes
# those tokens itself under CODEX_HOME, so no device flow ever runs
# unattended. Only when the invoker has no login does install fall back to an
# OpenAI API key provisioned with codex's own machinery: `codex login
# --with-api-key` reads the key from seed/codex-token and writes the home's
# auth file, so install.sh never learns the auth schema.
#
# Hook trust is bypassed at launch (--dangerously-bypass-hook-trust), which the
# CLI documents for automation that already vets hook sources: every hook
# command here is baked at install time with --id/--hook-bin/--root, and hook
# commands run with the session cwd as their working directory.
#
# Repo-layer hooks and skills need project trust, which is never granted
# unattended, so a checkout's own .codex/ stays inert.
#
# The Companion is still a Claude session. After the Codex home is written this runs
# adapters/claude/install.sh with HX_COMPANION_ONLY=1, which needs seed/token.
set -euo pipefail

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || die "usage: install.sh <id>"
id=$1
case "$id" in
  partner|[a-z]*-[0-9][0-9][0-9]) ;;
  *) die "refuse: \`$id\` is not an id (\`partner\` or \`<pod>-NNN\`)" ;;
esac
# The Partner may run on Codex (spec 12): `hx wake` reaches it by pasting into
# its pane, and the guard rides `PreToolUse` exactly as on Claude.

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=${HARNESS_ROOT:-$(cd "$here/../.." && pwd)}
[ -d "$root" ] || die "HARNESS_ROOT $root does not exist"

python=${HX_PYTHON:-}
if [ -z "$python" ] && [ -f "$root/config/hx.json" ]; then
  python=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("python_bin") or "")' \
    "$root/config/hx.json" 2>/dev/null || true)
fi
[ -n "$python" ] || python=python3
command -v "$python" >/dev/null 2>&1 || die "$python not found; hx needs Python 3.14"

token_file=$root/seed/codex-token
user_auth=""
[ -n "${HOME:-}" ] && user_auth="$HOME/.codex/auth.json"
use_login=0
[ -n "$user_auth" ] && [ -f "$user_auth" ] && use_login=1
if [ "$use_login" = 0 ]; then
  [ -f "$token_file" ] || die \
    "refuse: no $user_auth (ChatGPT login) and no $token_file; log in with codex once, or paste the OpenAI API key there, mode 0600"
  token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
  [ "$token_mode" = 0 ] || die \
    "refuse: $token_file is readable by group or other; it must be mode 0600. Run: chmod 600 $token_file"
fi

claude_token=$root/seed/token
[ -f "$claude_token" ] || die \
  "refuse: no $claude_token; the Companion is a Claude session and needs it, mode 0600"

harness=$root/config/$id/harness.json
[ -f "$harness" ] || die "refuse: no $harness"

# The agent's working directory (HARNESS_ROOT for the Partner, which has none):
# it is pre-trusted below so the first launch never stops at the folder-trust
# prompt with nobody to answer it.
cwd=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("workdir") or "")' "$harness")
[ -n "$cwd" ] || cwd=$root

hook_bin=$root/bin/hx-hook
if [ -f "$root/config/hx.json" ]; then
  hook_bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("hook_bin") or sys.argv[2])' \
    "$root/config/hx.json" "$hook_bin" 2>/dev/null || true)
fi

bin=${HX_CODEX_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/codex.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/codex.json")
fi
[ -n "$bin" ] || bin=$(command -v codex || true)
[ -n "$bin" ] || die "refuse: no codex binary; record {bin, version} in config/codex.json"
[ -x "$bin" ] || die "refuse: pinned codex binary $bin is not executable"

home=$root/run/$id/home
mkdir -p "$home/skills"

HX_ID=$id HX_ROOT=$root HX_HOME=$home HX_PYTHON=$python HX_HOOK_BIN=$hook_bin HX_CWD=$cwd \
"$python" - <<'PYEOF'
import os
import shlex

home = os.environ["HX_HOME"]
item_id = os.environ["HX_ID"]
python = os.environ["HX_PYTHON"]
hook_bin = os.environ["HX_HOOK_BIN"]
adapter = os.path.join(os.environ["HX_ROOT"], "adapters", "codex", "hook.py")
root = os.environ["HX_ROOT"]
cwd = os.environ["HX_CWD"]

# The Claude hook set (spec 09.1), in Codex's TOML shape. Matchers follow the
# official reference: SessionStart filters the start source, PostToolUse and
# the compaction events match every occurrence when matcher is omitted, and
# the Agent matcher catches subagent tool calls for the subagent-result hook.
# PreToolUse matchers filter tool names (`Bash`, `apply_patch` with its `Edit`
# and `Write` aliases); the guard below is the Partner's alone, like Claude's.
events = [
    ("SessionStart", "startup|resume|clear|compact", "context"),
    ("PostToolUse", None, "log"),
    ("PostToolUse", "Agent|spawn_agent", "subagent-result"),
    ("Stop", None, "stop"),
    ("PreCompact", None, "precompact"),
    ("PostCompact", None, "postcompact"),
    ("SubagentStart", None, "subagent-start"),
    ("SubagentStop", None, "subagent-stop"),
]
if item_id == "partner":
    events.append(("PreToolUse", "Bash|apply_patch|Edit|Write", "guard"))
blocks = []
for codex_event, matcher, hx_event in events:
    # hook_bin can be a command with arguments (`python -m hx.hooks` in
    # tests); quote each part, the shell splits them back at fire time.
    cmd = " ".join(shlex.quote(part) for part in (
        python, adapter, "--id", item_id, "--hook-bin", hook_bin,
        "--root", root, hx_event,
    ))
    block = f"[[hooks.{codex_event}]]\n"
    if matcher is not None:
        block += f"matcher = \"{matcher}\"\n"
    block += (
        f"  [[hooks.{codex_event}.hooks]]\n"
        f"  type = \"command\"\n"
        f"  command = '{cmd}'\n"
    )
    blocks.append(block)

config = """\
approval_policy = "never"
sandbox_mode = "danger-full-access"

""" + "\n".join(blocks)
# The agent runs unattended in exactly one directory: pre-trust it, or the
# first launch stops at the folder-trust prompt with nobody to answer it.
# This is the human's own trust decision, recorded by hx at install time.
config += f"""
[projects."{cwd}"]
trust_level = "trusted"
"""
with open(os.path.join(home, "config.toml"), "w") as handle:
    handle.write(config)
PYEOF

# Provision auth: the invoker's login wins; the API key is the fallback.
if [ "$use_login" = 1 ]; then
  cp "$user_auth" "$home/auth.json"
  chmod 600 "$home/auth.json"
  printf 'install.sh: copied ChatGPT login from %s (API key not used)\n' "$user_auth"
else
  # Provision auth with codex's own login so install.sh never learns the schema.
  # --with-api-key reads the key from stdin; CODEX_HOME scopes everything it
  # writes to this agent's home.
  CODEX_HOME="$home" "$bin" login --with-api-key < "$token_file" \
    || die "refuse: codex login --with-api-key failed; is $token_file an OpenAI API key?"
fi
[ -f "$home/auth.json" ] || die \
  "refuse: no $home/auth.json; not authenticated"

skills_src=${HX_SKILLS_DIR:-}
if [ -n "$skills_src" ] && [ -d "$skills_src" ]; then
  # The Partner gets hx-partner and hx-fleet, a worker gets hx-worker, like the
  # Claude adapter (spec 11, 12).
  wants="hx-worker hx-memory"
  [ "$id" = partner ] && wants="hx-partner hx-fleet hx-memory"
  for want in $wants; do
    [ -d "$skills_src/$want" ] || continue
    rm -rf "$home/skills/$want"
    cp -R "$skills_src/$want" "$home/skills/$want"
  done
fi

HX_COMPANION_ONLY=1 bash "$root/adapters/claude/install.sh" "$id"

printf 'install.sh: wrote %s (Codex home, unattended, hx hooks)\n' "$home/config.toml"
if [ "$use_login" = 1 ]; then
  printf 'install.sh: auth is the copied ChatGPT login at %s\n' "$home/auth.json"
else
  printf 'install.sh: auth provisioned from %s via codex login\n' "$token_file"
fi
