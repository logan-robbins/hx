#!/usr/bin/env bash
# adapters/codex/install.sh <id> — write the per-agent Codex home.
#
# The home is CODEX_HOME for this agent. It holds config.toml (unattended
# posture, the hx hooks, the web search route of docs/codex-search-guard.md)
# and skills. Auth is the invoker's ChatGPT login,
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

root=${HARNESS_ROOT:?hx exports it for every adapter (spec 11)}
[ -d "$root" ] || die "HARNESS_ROOT $root does not exist"

# The interpreter hx runs on: hx sets HX_PYTHON for every adapter it runs.
python=${HX_PYTHON:-}
[ -n "$python" ] || die "HX_PYTHON is unset; hx runs this adapter with the interpreter it runs on"
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

harness=$root/config/$id/harness.json
[ -f "$harness" ] || die "refuse: no $harness"

# The agent's working directory, pre-trusted below so the first launch never stops at the
# folder-trust prompt with nobody to answer it: HARNESS_ROOT for the Partner, the resolved
# `workdir` for a worker.
if [ "$id" = partner ]; then
  cwd=$root
else
  cwd=$("$python" -c 'import json,os,sys;print(os.path.join(sys.argv[2], json.load(open(sys.argv[1]))["workdir"]))' \
    "$harness" "$root")
fi

# Every hook runs bin/hx-hook, which `hx install` links to the package entry point.
hook_bin=$root/bin/hx-hook

bin=${HX_CODEX_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/codex.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/codex.json")
fi
[ -n "$bin" ] || bin=$(command -v codex || true)
[ -n "$bin" ] || die "refuse: no codex binary; record its path as \`bin\` in config/codex.json"
[ -x "$bin" ] || die "refuse: codex binary $bin is not executable"

# Inspected web search (docs/codex-search-guard.md): config/codex.json `search_guard`
# names the Foundry toolbox and the Content Safety endpoint. Both, or no object.
if [ -f "$root/config/codex.json" ]; then
  guard_problem=$("$python" - "$root/config/codex.json" <<'GUARDEOF'
import json, sys
guard = json.load(open(sys.argv[1])).get("search_guard")
if guard is not None and not isinstance(guard, dict):
    print("is not an object")
elif guard is not None:
    print(" and ".join("lacks " + key for key in ("toolbox_url", "content_safety_endpoint")
                       if not isinstance(guard.get(key), str) or not guard[key].strip()))
GUARDEOF
  ) || die "refuse: config/codex.json is not a JSON object"
  [ -z "$guard_problem" ] || die "refuse: config/codex.json search_guard $guard_problem;" \
    "it needs both toolbox_url and content_safety_endpoint (docs/codex-search-guard.md)"
fi

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
from pathlib import Path
from hx.hook_contract import installation_args
continuity = installation_args(Path(os.environ["HX_ROOT"]), item_id, "codex", os.environ)
root = os.environ["HX_ROOT"]
cwd = os.environ["HX_CWD"]

if continuity or item_id == "partner":
    from hx.file_read_delivery import install_profile
    install_profile(Path(root), item_id)

# The Claude hook set (spec 09.1), in Codex's TOML shape. Matchers follow the
# official reference: SessionStart filters the start source, PostToolUse and
# the compaction events match every occurrence when matcher is omitted, and
# the Agent matcher catches subagent tool calls for the subagent-result hook.
# PreToolUse matchers filter tool names (`Bash`, `apply_patch` with its `Edit`
# and `Write` aliases); the guard below is the Partner's alone, like Claude's.
events = [
    ("SessionStart", "startup|resume|clear|compact", "context"),
    ("PostToolUse", None, "log"),
    ("Stop", None, "stop"),
    ("PreCompact", None, "precompact"),
    ("PostCompact", None, "postcompact"),
    ("SubagentStart", None, "subagent-start"),
    ("SubagentStop", None, "subagent-stop"),
]
if continuity:
    events.append(("UserPromptSubmit", None, "request"))
    events.append(("PreToolUse", None, "tool-start"))
if item_id == "partner":
    events.append(("PreToolUse", None, "guard"))
    events.append(("PostToolUse", "Agent|spawn_agent", "subagent-result"))
blocks = []
for codex_event, matcher, hx_event in events:
    # Quote each part; the shell splits them back at fire time.
    cmd = " ".join(shlex.quote(part) for part in (
        python, adapter, "--id", item_id, "--hook-bin", hook_bin,
        "--root", root, *continuity, hx_event,
    ))
    if item_id == 'partner' and hx_event in {'context', 'subagent-start', 'subagent-result'}:
        cmd += ' --context-segment 0'
    block = f"[[hooks.{codex_event}]]\n"
    if matcher is not None:
        block += f"matcher = \"{matcher}\"\n"
    block += (
        f"  [[hooks.{codex_event}.hooks]]\n"
        f"  type = \"command\"\n"
        f"  command = '{cmd}'\n"
        + ("  additionalContextLimit = 0\n" if (continuity or item_id == 'partner') and hx_event in {'log', 'context'} else "")
    )
    blocks.append(block)
if item_id == 'partner':
    from hx.file_read_delivery import segment_slots
    for index in range(1, segment_slots(Path(root), item_id)):
        cmd = shlex.join([python, adapter, '--id', item_id, '--hook-bin', hook_bin,
            '--root', root, 'log', '--read-segment', str(index)])
        blocks.append('[[hooks.PostToolUse]]\nmatcher = "Read|read|read_file|Bash|bash|exec_command|shell|shell_command|run_terminal_cmd|run_terminal_command|run_shell_command"\n'
            '  [[hooks.PostToolUse.hooks]]\n  type = "command"\n'
            + f"  command = '{cmd}'\n  additionalContextLimit = 0\n")
    for native_event, hx_event, matcher in [('SessionStart', 'context', 'startup|resume|clear|compact'),
            ('SubagentStart', 'subagent-start', None), ('PostToolUse', 'subagent-result', 'Agent|spawn_agent')]:
        for index in range(1, segment_slots(Path(root), item_id)):
            cmd = shlex.join([python, adapter, '--id', item_id, '--hook-bin', hook_bin,
                '--root', root, hx_event, '--context-segment', str(index)])
            block = f'[[hooks.{native_event}]]\n'
            if matcher:
                block += 'matcher = "' + matcher + '"\n'
            blocks.append(block + f'  [[hooks.{native_event}.hooks]]\n  type = "command"\n'
                + f"  command = '{cmd}'\n  additionalContextLimit = 0\n")

# Web search (docs/codex-search-guard.md). The hosted tool fires no hook, and the
# full-access sandbox would default it to live: without `search_guard` it stays
# cached. With it the hosted tool is off and search is the Foundry toolbox's MCP
# tool, pre-approved, with both directions through search_guard.py. This is not
# the Partner's guard: every Codex agent gets it, matched to that one tool.
import json
codex_json = os.path.join(root, "config", "codex.json")
guard = json.load(open(codex_json)).get("search_guard") if os.path.isfile(codex_json) else None
web_search = "cached" if guard is None else "disabled"
if guard is not None:
    adapters = os.path.join(root, "adapters", "codex")
    helper = shlex.join([python, os.path.join(adapters, "toolbox_headers.py")])
    blocks.append(
        "[mcp_servers.foundry]\n"
        f"url = {json.dumps(guard['toolbox_url'], ensure_ascii=False)}\n"
        f"http_headers_helper = {json.dumps(helper, ensure_ascii=False)}\n"
        'enabled_tools = ["web_search"]\n'
        'default_tools_approval_mode = "approve"\n'
        "tool_timeout_sec = 60\n\n"
        "[mcp_servers.foundry.tools.web_search]\n"
        "output_token_limit = 4000\n")
    for event, mode, timeout, status in (
            ("PreToolUse", "pre", 20, "Checking the search query"),
            ("PostToolUse", "post", 60, "Scanning search results with Prompt Shields")):
        # A TOML basic string: shlex quotes a part with `'`, which a literal cannot hold.
        cmd = shlex.join([python, os.path.join(adapters, "search_guard.py"), mode,
                          "--endpoint", guard["content_safety_endpoint"],
                          "--log", os.path.join(root, "run", item_id, "search-guard.jsonl")])
        blocks.append(
            f"[[hooks.{event}]]\n"
            'matcher = "mcp__foundry__web_search"\n'
            f"  [[hooks.{event}.hooks]]\n"
            '  type = "command"\n'
            f"  command = {json.dumps(cmd, ensure_ascii=False)}\n"
            f"  timeout = {timeout}\n"
            f'  statusMessage = "{status}"\n')

config = f"""\
approval_policy = "never"
sandbox_mode = "danger-full-access"
web_search = "{web_search}"

""" + "\n".join(blocks)
if continuity:
    import json
    from hx.compaction_policy import PROMPT
    config = "compact_prompt = " + json.dumps(PROMPT) + "\n" + config
    if os.environ.get("HX_AUTOCOMPACT_WINDOW"):
        config = f"model_auto_compact_token_limit = {int(os.environ['HX_AUTOCOMPACT_WINDOW'])}\n" + config
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
  # Claude adapter (spec 11, 12): the manifest decides (src/hx/skills/manifest.json).
  wants=$("$python" -m hx.skill_manifest select --skills "$skills_src" --root "$root" --id "$id") ||
    die "skill manifest: cannot select the skills for $id from $skills_src"
  for want in $wants; do
    [ -d "$skills_src/$want" ] || continue
    rm -rf "$home/skills/$want"
    cp -R "$skills_src/$want" "$home/skills/$want"
  done
fi

printf 'install.sh: wrote %s (Codex home, unattended, hx hooks)\n' "$home/config.toml"
if [ "$use_login" = 1 ]; then
  printf 'install.sh: auth is the copied ChatGPT login at %s\n' "$home/auth.json"
else
  printf 'install.sh: auth provisioned from %s via codex login\n' "$token_file"
fi
