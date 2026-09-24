#!/usr/bin/env bash
# adapters/claude/install.sh <id> — write the per-agent Claude Code home (spec 11, 17.3).
#
# Writes $HARNESS_ROOT/run/<id>/home/settings.json with:
#   - the hx hooks of spec 09.1, each carrying `--id <id>`, pointed at the hook binary
#     recorded in config/hx.json (CONTRACTS.md), falling back to $HARNESS_ROOT/bin/hx-hook.
#     The Partner alone also gets the `guard` PreToolUse hook over config/partner/guard.json;
#     no worker and no Companion is ever guarded (spec 09.1)
#   - instruction-files mode `claude-md`, so no AGENTS.md is ever discovered
#   - claudeMdExcludes for the agent's own workdir
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

# Pi workers call this script with HX_COMPANION_ONLY=1. The agent home is Pi's;
# the Companion session is still Claude and still needs this home.
companion_only=${HX_COMPANION_ONLY:-}

# Hook and hx binary paths: config/hx.json when hx install recorded it, else bin/ (spec 17.1).
# Computed for the Companion home too, which is why this sits outside the agent-home branch.
hook_bin=$root/bin/hx-hook
hx_bin=$root/bin/hx
if [ -f "$root/config/hx.json" ]; then
  hook_bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("hook_bin") or sys.argv[2])' \
    "$root/config/hx.json" "$hook_bin")
  hx_bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("hx_bin") or sys.argv[2])' \
    "$root/config/hx.json" "$hx_bin")
fi

# Skills from the package, loaded on demand; nothing is symlinked (spec 17.5). Defined
# out here because the Companion home is written even when the agent home is Pi's.
skills_src=${HX_SKILLS_DIR:-}
copy_skills() {
  local target=$1; shift
  [ -n "$skills_src" ] && [ -d "$skills_src" ] || return 0
  mkdir -p "$target"
  local want
  for want in "$@"; do
    [ -d "$skills_src/$want" ] || continue
    rm -rf "$target/$want"
    cp -R "$skills_src/$want" "$target/$want"
  done
}

home=$root/run/$id/home
if [ "$companion_only" != 1 ]; then
mkdir -p "$home"

# The directory this agent will run in, which start.sh also uses (spec 17.4): the `workdir`
# the Partner chose in config/<id>/harness.json, HARNESS_ROOT for the Partner itself. A
# relative workdir resolves against HARNESS_ROOT (CONTRACTS.md).
if [ "$id" = partner ]; then
  cwd=$root
else
  cwd=$("$python" - "$root/config/$id/harness.json" "$root" <<'CWDEOF'
import json, os, sys
workdir = (json.load(open(sys.argv[1])).get("workdir") or "").strip()
print(workdir if os.path.isabs(workdir) else os.path.join(sys.argv[2], workdir) if workdir
      else sys.argv[2])
CWDEOF
)
fi

HX_ID=$id HX_ROOT=$root HX_HOOK_BIN=$hook_bin HX_SETTINGS=$home/settings.json \
HX_CONFIG_JSON=$home/.claude.json HX_CWD=$cwd \
"$python" - <<'PYEOF'
import json
import os
import shlex

item_id = os.environ["HX_ID"]
root = os.environ["HX_ROOT"]
cwd = os.environ["HX_CWD"]
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
    "PostToolUse": [entry("log", "*")],
    "Stop": [entry("stop")],
    "PreCompact": [entry("precompact", "*")],
    "PostCompact": [entry("postcompact", "*")],
}
# The guard is the one hook that enforces anything, and it is the Partner's alone: it
# directs by status and dispatch, and config/partner/guard.json names what it must not run or
# read (spec 09.1). The matcher is `hook_guard.MATCHER`.
if is_partner:
    hooks["PreToolUse"] = [entry("guard", "Bash|Read|Edit|Write|Grep|Glob")]
else:
    hooks["PostToolUse"].append(entry("subagent-result", "Agent"))
    hooks["SubagentStart"] = [entry("subagent-start", "*")]
    hooks["SubagentStop"] = [entry("subagent-stop", "*")]

settings = {
    "hooks": hooks,
    # Only config/CLAUDE.md loads; the product repo's instruction files never do (spec 03).
    "pluginConfigs": {"agents-md@builtin": {"options": {"instructionFiles": "claude-md"}}},
    "claudeMdExcludes": [
        f"{cwd}/**/CLAUDE.md",
        f"{cwd}/**/CLAUDE.local.md",
        f"{cwd}/**/.claude/CLAUDE.md",
        f"{cwd}/**/AGENTS.md",
        f"{cwd}/**/.claude/AGENTS.md",
    ],
    # Bypass permissions, always. The Partner's guard is a hook, not a permission rule (spec 05, 09.1).
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
# A `config/CLAUDE.md` that uses `@path` imports would otherwise prompt for approval of
# includes outside the project, which is a third interactive gate (CONTRACTS.md).
project["hasClaudeMdExternalIncludesApproved"] = True
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

# The Partner gets hx-partner and hx-fleet, a worker gets hx-worker, a Companion gets
# hx-companion. Both kinds of HarnessAgent also get hx-memory. The Companion does not.
if [ "$id" = partner ]; then
  copy_skills "$home/skills" hx-partner hx-fleet hx-memory
else
  copy_skills "$home/skills" hx-worker hx-memory
fi
fi

# The Companion's own config dir (spec 10). It gets one hook, the `hx-companion` skill, and no
# CLAUDE.md: the Companion interprets a stream and returns JSON, and anything else that could
# make it act or load project context is a liability, not a feature. Onboarding and trust are
# pre-seeded here too, for the same reason as the agent's home.
companion_home=$root/run/$id/companion-home
mkdir -p "$companion_home"
copy_skills "$companion_home/skills" hx-companion
HX_COMPANION_HOME=$companion_home HX_CWD=$root HX_HOOK_BIN=$hook_bin HX_ID=$id \
"$python" - <<'COMPANIONEOF'
import json
import os
import shlex

home = os.environ["HX_COMPANION_HOME"]
hook = os.environ["HX_HOOK_BIN"]
item_id = os.environ["HX_ID"]


def companion_hook(event):
    return {"type": "command",
            "command": f"{shlex.quote(hook)} --id {shlex.quote(item_id)} {event}"}


# The Companion gets one hook and no others: its own `stop`, which validates what it wrote
# and installs it. It is never guarded: the guard is the Partner's (spec 09.1). No product skills and
# no CLAUDE.md: it reads the files each pass names and nothing else (spec 10).
settings = {
    "hooks": {
        "Stop": [{"hooks": [companion_hook("companion-stop")]}],
    },
    "pluginConfigs": {"agents-md@builtin": {"options": {"instructionFiles": "claude-md"}}},
    "skipDangerousModePermissionPrompt": True,
    "theme": "dark",
}
with open(os.path.join(home, "settings.json"), "w") as handle:
    json.dump(settings, handle, indent=2)
    handle.write("\n")

config_json = os.path.join(home, ".claude.json")
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
projects = existing.get("projects")
if not isinstance(projects, dict):
    projects = {}
workdir = os.environ["HX_CWD"]
project = projects.get(workdir)
if not isinstance(project, dict):
    project = {}
project["hasTrustDialogAccepted"] = True
project["hasClaudeMdExternalIncludesApproved"] = True
projects[workdir] = project
existing["projects"] = projects
with open(config_json, "w") as handle:
    json.dump(existing, handle, indent=2)
    handle.write("\n")
COMPANIONEOF

if [ "$companion_only" != 1 ]; then
  printf 'install.sh: wrote %s\n' "$home/settings.json"
fi
printf 'install.sh: wrote %s (stop hook + hx-companion, no CLAUDE.md)\n' "$companion_home/settings.json"
printf 'install.sh: auth is %s, exported by start.sh as CLAUDE_CODE_OAUTH_TOKEN\n' "$token_file"
