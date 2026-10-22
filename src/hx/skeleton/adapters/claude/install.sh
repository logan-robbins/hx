#!/usr/bin/env bash
# adapters/claude/install.sh <id> — write the per-agent Claude Code home.
#
# Writes $HARNESS_ROOT/run/<id>/home/settings.json with:
#   - the hx hooks, each carrying `--id <id>`, pointed at $HARNESS_ROOT/bin/hx-hook.
#     The Partner alone also gets the `guard` PreToolUse hook over config/partner/guard.json;
#     no worker is ever guarded
#   - instruction-files mode `claude-md`, so no AGENTS.md is ever discovered
#   - claudeMdExcludes for the agent's own workdir
#   - the bypass-permissions acceptance, so no launch is ever interactive
#   - crossSessionInbound: accept, for the Partner only
# Agent homes hold no full credentials. The instance's OAuth token, or API key fallback,
# is at $HARNESS_ROOT/seed/token. start.sh selects the matching environment variable. hx
# never reads the user's ~/.claude, any .credentials.json, or the macOS Keychain.
#
# Refuses when seed/token is missing or readable by anyone but its owner.
set -euo pipefail

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || die "usage: install.sh <id>"
id=$1
case "$id" in
  partner|[a-z]*-[0-9][0-9][0-9]) ;;
  *) die "refuse: \`$id\` is not an id (\`partner\` or \`<pod>-NNN\`)" ;;
esac

root=${HARNESS_ROOT:?hx exports it for every adapter}
[ -d "$root" ] || die "HARNESS_ROOT $root does not exist"

# The interpreter hx runs on: hx sets HX_PYTHON for every adapter it runs.
python=${HX_PYTHON:-}
[ -n "$python" ] || die "HX_PYTHON is unset; hx runs this adapter with the interpreter it runs on"
command -v "$python" >/dev/null 2>&1 || die "$python not found; hx needs Python 3.14"

token_file=$root/seed/token
[ -f "$token_file" ] || die \
  "refuse: no $token_file; use claude setup-token for OAuth or configure --env-file with ANTHROPIC_API_KEY"
token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
[ "$token_mode" = 0 ] || die \
  "refuse: $token_file is readable by group or other; it holds a year-long credential and must
  be mode 0600 (CONTRACTS.md). Run: chmod 600 $token_file"

# Every hook runs bin/hx-hook, which `hx install` links to the package entry point.
hook_bin=$root/bin/hx-hook

# Skills from the package, loaded on demand; nothing is symlinked.
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
# Which skills a home gets is the manifest's decision (src/hx/skills/manifest.json,
# CONTRACTS.md): audience, role, optional skills from config/hx.json `skills`.
manifest_skills() {
  [ -n "$skills_src" ] && [ -d "$skills_src" ] || return 0
  "$python" -m hx.skill_manifest select --skills "$skills_src" --root "$root" --id "$id" ||
    die "skill manifest: cannot select the skills for $id from $skills_src"
}

home=$root/run/$id/home
mkdir -p "$home"

# The directory this agent will run in, which start.sh also uses: the `workdir`
# the Partner chose in config/<id>/harness.json, HARNESS_ROOT for the Partner itself. A
# relative workdir resolves against HARNESS_ROOT (CONTRACTS.md).
if [ "$id" = partner ]; then
  cwd=$root
else
  cwd=$("$python" - "$root/config/$id/harness.json" "$root" <<'CWDEOF'
import json, os, sys
# A relative workdir resolves against HARNESS_ROOT; an absolute one stands.
print(os.path.join(sys.argv[2], json.load(open(sys.argv[1]))["workdir"]))
CWDEOF
)
fi

HX_ID=$id HX_ROOT=$root HX_HOOK_BIN=$hook_bin HX_SETTINGS=$home/settings.json \
HX_CONFIG_JSON=$home/.claude.json HX_CWD=$cwd \
"$python" - <<'PYEOF'
import json
import os
import shlex
from pathlib import Path

item_id = os.environ["HX_ID"]
root = os.environ["HX_ROOT"]
cwd = os.environ["HX_CWD"]
hook_bin = os.environ["HX_HOOK_BIN"]
target = os.environ["HX_SETTINGS"]
is_partner = item_id == "partner"
from hx.hook_contract import installation_args
continuity = installation_args(Path(root), item_id, "claude", os.environ)


# Only the Partner's reads and context travel as hook transport segments.
if is_partner:
    from hx.file_read_delivery import install_profile
    install_profile(Path(root), item_id)

def hook(event):
    args = [hook_bin, "--id", item_id, *(["--root", root, *continuity] if continuity else []), event]
    return {"type": "command", "command": " ".join(shlex.quote(arg) for arg in args)}


def entry(event, matcher=None):
    block = {"hooks": [hook(event)]}
    if matcher is not None:
        block = {"matcher": matcher, **block}
    return block


# Both audiences observe native child starts/stops. Only the Partner composes
# bounded research digests; worker results use the ordinary ledger tool log.
hooks = {
    "SessionStart": [entry("context", "startup|resume|clear|compact")],
    "PostToolUse": [entry("log", "*")],
    "Stop": [entry("stop")],
    "PreCompact": [entry("precompact", "*")],
    "PostCompact": [entry("postcompact", "*")],
    "SubagentStart": [entry("subagent-start", "*")],
    "SubagentStop": [entry("subagent-stop", "*")],
}
if continuity:
    hooks["UserPromptSubmit"] = [entry("request")]
    hooks["PreToolUse"] = [entry("tool-start", "*")]
    hooks["PostToolUseFailure"] = [entry("log-failure", "*")]
    hooks["StopFailure"] = [entry("stop-failure")]
    hooks["SessionEnd"] = [entry("session-end")]
    # One entry per event: Claude's own Read result is the complete-file delivery, so the
    # worker's log hook adds only a completeness marker (hx.file_read_delivery.native_marker).

# The guard is the one hook that enforces anything, and it is the Partner's alone: it
# directs by status and dispatch, and config/partner/guard.json names what it must not run or
# read. The matcher is `hook_guard.MATCHER`.
if is_partner:
    hooks["PreToolUse"] = [entry("guard", "*")]
    hooks.setdefault('PostToolUseFailure', [entry('log-failure', '*')])
    hooks["PostToolUse"].append(entry("subagent-result", "Agent"))
    from hx.file_read_delivery import segment_slots
    from pathlib import Path
    for index in range(1, segment_slots(Path(root), item_id)):
        for native_event, hx_event in [('PostToolUse', 'log'), ('PostToolUseFailure', 'log-failure')]:
            part = entry(hx_event, 'Read|read|read_file|Bash|bash|exec_command|shell|shell_command|run_terminal_cmd|run_terminal_command|run_shell_command')
            part['hooks'][0]['command'] += ' --read-segment ' + str(index)
            hooks[native_event].append(part)
    for native_event, hx_event, matcher in [('SessionStart', 'context', 'startup|resume|clear|compact'),
            ('SubagentStart', 'subagent-start', '*'), ('PostToolUse', 'subagent-result', 'Agent')]:
        for part in hooks[native_event]:
            if part['hooks'][0]['command'].endswith(' ' + hx_event):
                part['hooks'][0]['command'] += ' --context-segment 0'
        for index in range(1, segment_slots(Path(root), item_id)):
            part = entry(hx_event, matcher)
            part['hooks'][0]['command'] += ' --context-segment ' + str(index)
            hooks[native_event].append(part)

settings = {
    "hooks": hooks,
    # Only config/CLAUDE.md loads; the product repo's instruction files never do.
    "pluginConfigs": {"agents-md@builtin": {"options": {"instructionFiles": "claude-md"}}},
    "claudeMdExcludes": [
        f"{cwd}/**/CLAUDE.md",
        f"{cwd}/**/CLAUDE.local.md",
        f"{cwd}/**/.claude/CLAUDE.md",
        f"{cwd}/**/AGENTS.md",
        f"{cwd}/**/.claude/AGENTS.md",
    ],
    # Bypass permissions, always. The Partner's guard is a hook, not a permission rule.
    "skipDangerousModePermissionPrompt": True,
}
if continuity:
    settings["autoMemoryEnabled"] = False
if is_partner:
    # The human and hx wake the Partner through the messaging socket.
    settings["crossSessionInbound"] = "accept"
settings["theme"] = "dark"

with open(target, "w") as handle:
    json.dump(settings, handle, indent=2)
    handle.write("\n")

# A brand-new CLAUDE_CONFIG_DIR runs the first-run onboarding wizard (theme picker, tips),
# which would stop a launch dead: nothing about launch is interactive.
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
credential = (Path(root) / "seed" / "token").read_text().strip()
if credential.startswith("sk-ant-api"):
    responses = existing.setdefault("customApiKeyResponses", {"approved": [], "rejected": []})
    if credential[-20:] not in responses["approved"]:
        responses["approved"].append(credential[-20:])

# Claude Code also asks, once per working directory, whether the folder is trusted. An
# agent has no one to ask, and the directory is one hx created, so the answer is recorded
# here rather than waited for. Key names confirmed against a real, human-accepted
# `.claude.json` (read-only) and by a live run: with these two set, the
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

# The one CLAUDE.md that loads, installed as this home's user-level CLAUDE.md.
if [ -f "$root/config/CLAUDE.md" ]; then
  cp "$root/config/CLAUDE.md" "$home/CLAUDE.md"
fi

# Audience, role and enabled optional capabilities determine installed skills.
# Native packet launches load task-specific capabilities through the bounded catalog.
wants=$(manifest_skills) || exit 1
copy_skills "$home/skills" $wants

if [ -n "${HX_CONTINUITY_AUTHORITY:-}" ]; then
  printf 'install.sh: prepared executor home %s\n' "$home"
  exit 0
fi
printf 'install.sh: wrote %s\n' "$home/settings.json"
printf 'install.sh: auth is %s, exported by start.sh for the credential type\n' "$token_file"
