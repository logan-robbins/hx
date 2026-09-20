#!/usr/bin/env bash
# adapters/claude/install.sh <id> — write the per-agent Claude Code home (spec 11, 17.3).
#
# Writes $HARNESS_ROOT/run/<id>/home/settings.json with:
#   - the nine hx hooks of spec 09.1, each carrying `--id <id>`, pointed at the hook binary
#     recorded in config/hx.json (CONTRACTS.md), falling back to $HARNESS_ROOT/bin/hx-hook
#   - instruction-files mode `claude-md`, so no AGENTS.md is ever discovered
#   - claudeMdExcludes for the product repo's CLAUDE.md / AGENTS.md under wt/ and repos/
#   - the bypass-permissions acceptance, so no launch is ever interactive
#   - crossSessionInbound: accept, for the Partner only (spec 05, 11, 17.3)
# Agent homes hold no credentials at all. Auth is one long-lived OAuth token per instance,
# at $HARNESS_ROOT/seed/token (spec 11 Auth, CONTRACTS.md): the human runs `claude setup-token`
# once and pastes the result there, and start.sh exports it as CLAUDE_CODE_OAUTH_TOKEN. hx
# never reads the user's ~/.claude, any .credentials.json, or the macOS Keychain.
#
# Refuses when seed/token is missing or readable by anyone but its owner.
set -euo pipefail

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || die "usage: install.sh <id>"
id=$1
case "$id" in
  partner|[a-z]*-[0-9][0-9][0-9]) ;;
  *) die "refuse: \`$id\` is not an id (\`partner\` or \`<pod>-NNN\`, spec 06)" ;;
esac

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=${HARNESS_ROOT:-$(cd "$here/../.." && pwd)}
[ -d "$root" ] || die "HARNESS_ROOT $root does not exist"

# The interpreter hx itself runs on, recorded as `python_bin` in config/hx.json
# (CONTRACTS.md), so the adapters read JSON with the same Python the package was installed on.
python=${HX_PYTHON:-}
if [ -z "$python" ] && [ -f "$root/config/hx.json" ]; then
  python=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("python_bin") or "")' \
    "$root/config/hx.json" 2>/dev/null || true)
fi
[ -n "$python" ] || python=python3
command -v "$python" >/dev/null 2>&1 || die "$python not found; hx needs Python 3.14 (spec 17.2)"

token_file=$root/seed/token
[ -f "$token_file" ] || die \
  "refuse: no $token_file; the human runs \`claude setup-token\` once and pastes the token
  there, mode 0600 (spec 11 Auth, 17.2 step 3). Without it a launch would stop at a login prompt"
token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
[ "$token_mode" = 0 ] || die \
  "refuse: $token_file is readable by group or other; it holds a year-long credential and must
  be mode 0600 (CONTRACTS.md). Run: chmod 600 $token_file"

home=$root/run/$id/home
mkdir -p "$home"

# The directory this agent will run in, which start.sh also uses (spec 17.4).
if [ "$id" = partner ]; then cwd=$root; else cwd=$root/wt/$id; fi

# Hook and hx binary paths: config/hx.json when hx install recorded it, else bin/ (spec 17.1).
hook_bin=$root/bin/hx-hook
hx_bin=$root/bin/hx
if [ -f "$root/config/hx.json" ]; then
  hook_bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("hook_bin") or sys.argv[2])' \
    "$root/config/hx.json" "$hook_bin")
  hx_bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("hx_bin") or sys.argv[2])' \
    "$root/config/hx.json" "$hx_bin")
fi

HX_ID=$id HX_ROOT=$root HX_HOOK_BIN=$hook_bin HX_SETTINGS=$home/settings.json \
HX_CONFIG_JSON=$home/.claude.json HX_CWD=$cwd \
"$python" - <<'PYEOF'
import json
import os
import shlex

item_id = os.environ["HX_ID"]
root = os.environ["HX_ROOT"]
hook_bin = os.environ["HX_HOOK_BIN"]
target = os.environ["HX_SETTINGS"]
is_partner = item_id == "partner"


def hook(event):
    return {"type": "command", "command": f"{shlex.quote(hook_bin)} --id {shlex.quote(item_id)} {event}"}


def entry(event, matcher=None):
    block = {"hooks": [hook(event)]}
    if matcher is not None:
        block = {"matcher": matcher, **block}
    return block


# Spec 09.1. The subagent events are non-Partner: the Partner spawns no harness subagents
# that hx tracks as streams.
hooks = {
    "SessionStart": [entry("context", "startup|resume|clear|compact")],
    "PreToolUse": [entry("guard", "*")],
    "PostToolUse": [entry("log", "*")],
    "Stop": [entry("stop")],
    "PreCompact": [entry("precompact", "*")],
    "PostCompact": [entry("postcompact", "*")],
}
if not is_partner:
    hooks["PostToolUse"].append(entry("subagent-result", "Agent"))
    hooks["SubagentStart"] = [entry("subagent-start", "*")]
    hooks["SubagentStop"] = [entry("subagent-stop", "*")]

settings = {
    "hooks": hooks,
    # Only config/CLAUDE.md loads; the product repo's instruction files never do (spec 03).
    "pluginConfigs": {"agents-md@builtin": {"options": {"instructionFiles": "claude-md"}}},
    "claudeMdExcludes": [
        f"{root}/wt/**/CLAUDE.md",
        f"{root}/wt/**/CLAUDE.local.md",
        f"{root}/wt/**/.claude/CLAUDE.md",
        f"{root}/wt/**/AGENTS.md",
        f"{root}/wt/**/.claude/AGENTS.md",
        f"{root}/repos/**/CLAUDE.md",
        f"{root}/repos/**/AGENTS.md",
    ],
    # Bypass permissions, always: the guard hook is the only enforcement (spec 05, 09.2).
    "skipDangerousModePermissionPrompt": True,
}
if is_partner:
    # The human and hx wake the Partner through the messaging socket (spec 11, 12).
    settings["crossSessionInbound"] = "accept"

settings["theme"] = "dark"

with open(target, "w") as handle:
    json.dump(settings, handle, indent=2)
    handle.write("\n")

# A brand-new CLAUDE_CONFIG_DIR runs the first-run onboarding wizard (theme picker, tips),
# which would stop a launch dead: "Nothing about launch is interactive" (spec 05, 11).
# The flag lives in the config dir's own .claude.json, next to settings.json. Existing keys
# are kept, so a home that has been running is not reset.
config_json = os.environ["HX_CONFIG_JSON"]
existing = {}
if os.path.exists(config_json):
    try:
        with open(config_json) as handle:
            loaded = json.load(handle)
        existing = loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, OSError):
        existing = {}
existing["hasCompletedOnboarding"] = True
existing.setdefault("theme", "dark")

# Claude Code also asks, once per working directory, whether the folder is trusted. An
# agent has no one to ask, and the directory is one hx created, so the answer is recorded
# here rather than waited for. Key names confirmed against a real, human-accepted
# `.claude.json` (read-only) and by the build-3 live run: with these two set, the pinned
# binary reaches its prompt with no dialog.
projects = existing.get("projects")
if not isinstance(projects, dict):
    projects = {}
workdir = os.environ["HX_CWD"]
project = projects.get(workdir)
if not isinstance(project, dict):
    project = {}
project["hasTrustDialogAccepted"] = True
projects[workdir] = project
existing["projects"] = projects
with open(config_json, "w") as handle:
    json.dump(existing, handle, indent=2)
    handle.write("\n")
PYEOF

# The one CLAUDE.md that loads, installed as this home's user-level CLAUDE.md (spec 11).
if [ -f "$root/config/CLAUDE.md" ]; then
  cp "$root/config/CLAUDE.md" "$home/CLAUDE.md"
fi

# Skills from the package, loaded on demand; nothing is symlinked (spec 17.5).
skills_src=${HX_SKILLS_DIR:-}
if [ -n "$skills_src" ] && [ -d "$skills_src" ]; then
  mkdir -p "$home/skills"
  if [ "$id" = partner ]; then want=hx-partner; else want=hx-worker; fi
  if [ -d "$skills_src/$want" ]; then
    rm -rf "$home/skills/$want"
    cp -R "$skills_src/$want" "$home/skills/$want"
  fi
fi

printf 'install.sh: wrote %s\n' "$home/settings.json"
printf 'install.sh: auth is %s, exported by start.sh as CLAUDE_CODE_OAUTH_TOKEN\n' "$token_file"
