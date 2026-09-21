#!/usr/bin/env bash
# adapters/grok/install.sh <id> — write the per-agent Grok home.
#
# The home is GROK_HOME for this agent. It holds config.toml (minimal screen,
# always-approve posture, goal mode on, the hx hooks) and skills. Auth is
# XAI_API_KEY from seed/grok-token, exported at launch; the home holds no
# credentials. Nothing is read from ~/.grok. Goal delivery is grok's native
# /goal loop, enabled in config; no custom command shadows it.
#
# The Companion is still a Claude session. After the Grok home is written this runs
# adapters/claude/install.sh with HX_COMPANION_ONLY=1, which needs seed/token.
set -euo pipefail

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || die "usage: install.sh <id>"
id=$1
case "$id" in
  partner|[a-z]*-[0-9][0-9][0-9]) ;;
  *) die "refuse: \`$id\` is not an id (\`partner\` or \`<pod>-NNN\`)" ;;
esac
[ "$id" != partner ] || die \
  "refuse: the Partner stays on claude; hx wake uses its messaging socket"

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

token_file=$root/seed/grok-token
[ -f "$token_file" ] || die \
  "refuse: no $token_file; paste the xAI API key there, mode 0600"
token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
[ "$token_mode" = 0 ] || die \
  "refuse: $token_file is readable by group or other; it must be mode 0600. Run: chmod 600 $token_file"

claude_token=$root/seed/token
[ -f "$claude_token" ] || die \
  "refuse: no $claude_token; the Companion is a Claude session and needs it, mode 0600"

harness=$root/config/$id/harness.json
[ -f "$harness" ] || die "refuse: no $harness"

hook_bin=$root/bin/hx-hook
if [ -f "$root/config/hx.json" ]; then
  hook_bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("hook_bin") or sys.argv[2])' \
    "$root/config/hx.json" "$hook_bin" 2>/dev/null || true)
fi

home=$root/run/$id/home
mkdir -p "$home/skills" "$home/sessions"

HX_ID=$id HX_ROOT=$root HX_HOME=$home HX_PYTHON=$python HX_HOOK_BIN=$hook_bin \
"$python" - <<'PYEOF'
import os
import shlex

home = os.environ["HX_HOME"]
item_id = os.environ["HX_ID"]
python = os.environ["HX_PYTHON"]
hook_bin = os.environ["HX_HOOK_BIN"]
adapter = os.path.join(os.environ["HX_ROOT"], "adapters", "grok", "hook.py")

events = (
    "SessionStart:context",
    "PostToolUse:log",
    "Stop:stop",
    "PreCompact:precompact",
    "PostCompact:postcompact",
    "SubagentStart:subagent-start",
    "SubagentStop:subagent-stop",
)
blocks = []
for spec in events:
    grok_event, hx_event = spec.split(":")
    # hook_bin can be a command with arguments (`python -m hx.hooks` in tests),
    # so it rides inside a shell-quoted command string grok runs.
    cmd = " ".join(shlex.quote(part) for part in (
        python, adapter, "--id", item_id, "--hook-bin", hook_bin, hx_event,
    ))
    blocks.append(
        f"[[hooks.{grok_event}]]\n"
        f"  [[hooks.{grok_event}.hooks]]\n"
        f"  type = \"command\"\n"
        f"  command = '{cmd}'\n"
    )

config = """\
[cli]
auto_update = false

[features]
telemetry = false
feedback = false

[ui]
screen_mode = "minimal"
permission_mode = "always-approve"

[goal]
enabled = true

""" + "\n".join(blocks)
with open(os.path.join(home, "config.toml"), "w") as handle:
    handle.write(config)
PYEOF

skills_src=${HX_SKILLS_DIR:-}
if [ -n "$skills_src" ] && [ -d "$skills_src" ]; then
  for want in hx-worker hx-memory; do
    [ -d "$skills_src/$want" ] || continue
    rm -rf "$home/skills/$want"
    cp -R "$skills_src/$want" "$home/skills/$want"
  done
fi

HX_COMPANION_ONLY=1 bash "$root/adapters/claude/install.sh" "$id"

printf 'install.sh: wrote %s (Grok home, minimal screen, hx hooks)\n' "$home/config.toml"
printf 'install.sh: auth is %s, exported as XAI_API_KEY at launch\n' "$token_file"
