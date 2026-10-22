#!/usr/bin/env bash
# adapters/meta/install.sh <id> — write the per-agent Muse Code home.
#
# The home is an XDG pair: XDG_CONFIG_HOME points at it (settings live in
# home/muse/settings.json) and XDG_DATA_HOME points at home/data (sessions live
# in home/data/muse/sessions). settings.json carries the Unrestricted posture,
# goal-friendly defaults, and the hx hooks with --id/--hook-bin/--root baked
# in, because hook commands run with a cleared environment. The CLI has no
# verifiable system-prompt flag, so the invariants, the persona, the identity line
# and the learnings arrive through the SessionStart `context` hook at every boundary. Auth is META_API_KEY from seed/meta-token, exported at
# launch; the home holds no credentials. Nothing is read from ~/.config/muse.
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

root=${HARNESS_ROOT:?hx exports it for every adapter}
[ -d "$root" ] || die "HARNESS_ROOT $root does not exist"

# The interpreter hx runs on: hx sets HX_PYTHON for every adapter it runs.
python=${HX_PYTHON:-}
[ -n "$python" ] || die "HX_PYTHON is unset; hx runs this adapter with the interpreter it runs on"
command -v "$python" >/dev/null 2>&1 || die "$python not found; hx needs Python 3.14"

token_file=$root/seed/meta-token
[ -f "$token_file" ] || die \
  "refuse: no $token_file; paste the Meta API key there, mode 0600"
token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
[ "$token_mode" = 0 ] || die \
  "refuse: $token_file is readable by group or other; it must be mode 0600. Run: chmod 600 $token_file"

harness=$root/config/$id/harness.json
[ -f "$harness" ] || die "refuse: no $harness"

# Every hook runs bin/hx-hook, which `hx install` links to the package entry point.
hook_bin=$root/bin/hx-hook

home=$root/run/$id/home
mkdir -p "$home/muse/skills" "$home/data"

HX_ID=$id HX_ROOT=$root HX_HOME=$home HX_PYTHON=$python HX_HOOK_BIN=$hook_bin \
"$python" - <<'PYEOF'
import json
import os
import shlex

home = os.environ["HX_HOME"]
item_id = os.environ["HX_ID"]
python = os.environ["HX_PYTHON"]
hook_bin = os.environ["HX_HOOK_BIN"]
adapter = os.path.join(os.environ["HX_ROOT"], "adapters", "meta", "hook.py")
from pathlib import Path
from hx.hook_contract import installation_args
continuity = installation_args(Path(os.environ["HX_ROOT"]), item_id, "meta", os.environ)
root = os.environ["HX_ROOT"]

if continuity:
    from hx.file_read_delivery import install_profile
    install_profile(Path(root), item_id)

events = (
    "SessionStart:context",
    "PostToolUse:log",
    "Stop:stop",
    "PreCompact:precompact",
    "PostCompact:postcompact",
    "SubagentStart:subagent-start",
    "SubagentStop:subagent-stop",
)
if continuity:
    events += ("UserPromptSubmit:request", "PreToolUse:tool-start", "PostToolUseFailure:log-failure",
               "StopFailure:stop-failure", "SessionEnd:session-end", "PostLLMCall:model-response")
hooks = {}
for spec in events:
    muse_event, hx_event = spec.split(":")
    # Quote each part; the shell splits them back at fire time.
    cmd = " ".join(shlex.quote(part) for part in (
        python, adapter, "--id", item_id, "--hook-bin", hook_bin,
        "--root", root, *continuity, hx_event,
    ))
    hooks.setdefault(muse_event, []).append({"hooks": [{"type": "command", "command": cmd}]})
if continuity:
    from hx.file_read_delivery import segment_slots
    for index in range(1, segment_slots(Path(root), item_id)):
        cmd = shlex.join([python, adapter, '--id', item_id, '--hook-bin', hook_bin,
            '--root', root, *continuity, 'log', '--read-segment', str(index)])
        hooks['PostToolUse'].append({'matcher': 'Read|read|read_file|Bash|bash|exec_command|shell|shell_command|run_terminal_cmd|run_terminal_command|run_shell_command',
            'hooks': [{'type': 'command', 'command': cmd}]})
        hooks['PostToolUseFailure'].append({'matcher': 'Read|read|read_file|Bash|bash|exec_command|shell|shell_command|run_terminal_cmd|run_terminal_command|run_shell_command',
            'hooks': [{'type': 'command', 'command': cmd.replace(' log --read-segment ', ' log-failure --read-segment ')}]})

settings = {
    "schema_version": 1,
    "provider": "meta",
    "permissions": {"schema_version": 1, "default_profile": ":unrestricted"},
    "hooks": hooks,
}
with open(os.path.join(home, "muse", "settings.json"), "w") as handle:
    json.dump(settings, handle, indent=2)
    handle.write("\n")
PYEOF

skills_src=${HX_SKILLS_DIR:-}
if [ -n "$skills_src" ] && [ -d "$skills_src" ]; then
  # The manifest decides (src/hx/skills/manifest.json, CONTRACTS.md).
  wants=$("$python" -m hx.skill_manifest select --skills "$skills_src" --root "$root" --id "$id") ||
    die "skill manifest: cannot select the skills for $id from $skills_src"
  for want in $wants; do
    [ -d "$skills_src/$want" ] || continue
    rm -rf "$home/muse/skills/$want"
    cp -R "$skills_src/$want" "$home/muse/skills/$want"
  done
fi

printf 'install.sh: wrote %s (Muse home, Unrestricted, hx hooks)\n' "$home/muse/settings.json"
printf 'install.sh: auth is %s, exported as META_API_KEY at launch\n' "$token_file"
