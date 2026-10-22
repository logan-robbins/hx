#!/usr/bin/env bash
# adapters/claude/start.sh <id> — the entire launch; nothing else starts a Claude Code
# process.
#
#   start.sh <id>          ensure tmux session <id> with the session env, and run
#                          the launcher in window `main` (cwd harness.json.workdir,
#                          HARNESS_ROOT for partner)
#   start.sh --exec <id>   the launcher itself: refresh the home's CLAUDE.md, then exec (the
#                          persona arrives through the SessionStart `context` hook)
#
# The argv is fixed: no prompt argument, no --resume, no compaction variables.
#
# `--setting-sources user` is not optional. Claude Code loads three settings sources — `user`
# ($CLAUDE_CONFIG_DIR/settings.json), `project` (<cwd>/.claude/settings.json) and `local`
# (<cwd>/.claude/settings.local.json) — and the workdir is a checkout hx does not own. A
# product repo's own `.claude/settings.json` can carry a deny-all PreToolUse hook or
# `defaultMode: plan` that denies every tool call, so the agent never reaches `hx complete`. Only
# the home's settings may apply, so only `user` is loaded. Verified against `claude --help` and
# code.claude.com/docs/en/cli-reference ("Comma-separated list of setting sources to load
# (`user`, `project`, `local`)"). Managed settings are a separate level and still apply.
#
# Auth is the instance credential at $HARNESS_ROOT/seed/token, read by the launcher itself.
# OAuth tokens use CLAUDE_CODE_OAUTH_TOKEN; an API key fallback uses ANTHROPIC_API_KEY. It is never an
# argument to anything — not to `env`, not to `tmux -e` — so it cannot appear in `ps` output,
# and it is never written under run/. Refuses a home without settings, or an instance without
# a 0600 token.
set -euo pipefail

die() { printf 'start.sh: %s\n' "$*" >&2; exit 1; }

mode=session
while :; do
  case "${1:-}" in
    --exec) mode=exec; shift ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || die "usage: start.sh [--exec] <id>"
id=$1
case "$id" in
  partner|[a-z]*-[0-9][0-9][0-9]) ;;
  *) die "refuse: \`$id\` is not an id (\`partner\` or \`<pod>-NNN\`)" ;;
esac

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=${HARNESS_ROOT:?hx exports it for every adapter}
[ -d "$root" ] || die "HARNESS_ROOT $root does not exist"

# The interpreter hx runs on: hx sets HX_PYTHON for every adapter it runs.
python=${HX_PYTHON:-}
[ -n "$python" ] || die "HX_PYTHON is unset; hx runs this adapter with the interpreter it runs on"
command -v "$python" >/dev/null 2>&1 || die "$python not found; hx needs Python 3.14"

# Every worker executable descends from the controller's admitted run supervisor.
"$python" -m hx.adapter_guard "$id" claude "$mode" agent || die "worker launch admission refused"

home=$root/run/$id/home
harness=$root/config/$id/harness.json
# Workers receive the frozen system prompt from their private launch capsule.
# Partner bootstrap receives its current orchestration persona at context boundaries.
persona=$root/run/$id/persona.md

# --- refusals, checked in both modes so a bad instance fails before tmux is touched -------
[ -f "$harness" ] || die "refuse: no $harness"
[ -f "$home/settings.json" ] || die \
  "refuse: no $home/settings.json; run adapters/claude/install.sh $id first"

token_file=$root/seed/token
[ -f "$token_file" ] || die \
  "refuse: no $token_file; use claude setup-token for OAuth or configure --env-file with ANTHROPIC_API_KEY"
token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
[ "$token_mode" = 0 ] || die \
  "refuse: $token_file is readable by group or other; it holds a year-long credential and must
  be mode 0600 (CONTRACTS.md). Run: chmod 600 $token_file"

# The directory this agent runs in: `workdir` from config/<id>/harness.json, whatever the
# Partner chose for it. hx creates no repository and no branch there.
if [ "$id" = partner ]; then
  cwd=$root
else
  cwd=$("$python" - "$harness" "$root" <<'CWDEOF'
import json, os, sys
# A relative workdir resolves against HARNESS_ROOT; an absolute one stands.
print(os.path.join(sys.argv[2], json.load(open(sys.argv[1]))["workdir"]))
CWDEOF
)
  [ -d "$cwd" ] || die "refuse: no workdir $cwd; it is \`workdir\` in $harness and must exist"
fi

bin=${HX_CLAUDE_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/claude.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/claude.json")
fi
[ -n "$bin" ] || bin=$(command -v claude || true)
[ -n "$bin" ] || die "refuse: no claude binary; \`hx install\` records its path as \`bin\` in config/claude.json"
[ -x "$bin" ] || die "refuse: claude binary $bin is not executable"

model=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["model"])' "$harness")
effort=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["effort"])' "$harness")
[ -n "$model" ] || die "refuse: $harness has no \`model\`"
[ -n "$effort" ] || die "refuse: $harness has no \`effort\`"

# `autocompact_window` from this model's row in config/models.json, exported below as
# CLAUDE_CODE_AUTO_COMPACT_WINDOW. Optional: a row without it leaves the variable unset and
# the session keeps Claude Code's native autocompact window. With it,
# native compaction is moved down to a number the operator picks, and `config_models.py` has
# already refused any row where hx's seam threshold is not below it. Empty on anything
# unexpected — a missing file, an unlisted model, a malformed row: a launch never fails
# over this.
autocompact=$("$python" - "$root/config/models.json" "$model" <<'ACEOF'
import json, sys
try:
    rows = json.load(open(sys.argv[1]))
    value = (rows.get(sys.argv[2]) or {}).get("autocompact_window")
except Exception:
    value = None
print(value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else "")
ACEOF
)

if [ "$mode" = exec ]; then
  . "$root/adapters/load-env.sh"
  mkdir -p "$root/run/$id"
  system_args=()
  if [ -n "${HX_CONTINUITY_CAPSULE:-}" ]; then
    # A planned launch's persona is the compiled planned system prompt, prepared in its
    # private root and verified by the controller (`native_launch`).
    [ -f "$persona" ] || die "refuse: no $persona; a planned launch prepares it (native_launch)"
    system_args=(--append-system-prompt-file "$persona")
  fi
  # Otherwise no system-prompt flag: the persona, the identity line and the learnings arrive
  # through the SessionStart `context` hook at every boundary (`hx persona $id` prints it).
  # The invariants' one channel on Claude is the home's native user CLAUDE.md
  # (`config_harness.INVARIANTS_CHANNEL`), refreshed here so an edit to config/CLAUDE.md
  # lands at the next launch or restart, like a persona edit, not only at a reinstall.
  if [ "$id" = partner ]; then
    # The verified complete Partner context segments own invariants and
    # persona delivery; clear this HX-private copy to avoid duplicates.
    : > "$home/CLAUDE.md"
  elif [ -f "$root/config/CLAUDE.md" ]; then
    cp "$root/config/CLAUDE.md" "$home/CLAUDE.md"
  fi

  cd "$cwd"
  # The session env. Exported rather than passed to `env`, so the token
  # never becomes an argv element of anything.
  export HARNESS_ID="$id"
  export HARNESS_ROOT="${HX_CONTINUITY_AUTHORITY:-$root}"
  export CLAUDE_CONFIG_DIR="$home"
  # Runtime stability: the binary under a running session never changes mid-turn.
  export DISABLE_AUTOUPDATER=1
  # `hx` on the agent's PATH, so no agent digs the absolute path out of config/hx.json.
  export PATH="$root/bin:$PATH"
  if [ -n "${HX_MAX_OUTPUT_TOKENS:-}" ]; then
    export CLAUDE_CODE_MAX_OUTPUT_TOKENS="$HX_MAX_OUTPUT_TOKENS"
  fi
  # Claude's Read is a worker's complete-file delivery. Its own token cap (25000 by default)
  # would truncate a file hx admits, and admission already bounds every read by the threshold.
  if [ -n "${HX_CONTEXT_THRESHOLD:-}" ]; then
    export CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS="$HX_CONTEXT_THRESHOLD"
  fi
  if [ -n "${HX_AUTOCOMPACT_WINDOW:-}" ]; then
    autocompact=$HX_AUTOCOMPACT_WINDOW
  fi
  # The operator's autocompact window, when `config/models.json` names one for this model.
  # hx's own seam threshold is below it by validation, so the seam still lands first and
  # native compaction stays the thing that never runs.
  if [ -n "$autocompact" ]; then
    export CLAUDE_CODE_AUTO_COMPACT_WINDOW="$autocompact"
  fi
  # Every agent runs sandboxed and with permissions bypassed, always. The
  # `.claude.json` pre-seed stays too: both mechanisms, so no dialog can ever appear.
  export IS_SANDBOX=1
  credential=$(cat "$token_file")
  if [[ "$credential" == sk-ant-api* ]]; then
    export ANTHROPIC_API_KEY="$credential"
    unset CLAUDE_CODE_OAUTH_TOKEN
  else
    export CLAUDE_CODE_OAUTH_TOKEN="$credential"
    unset ANTHROPIC_API_KEY
  fi

  # No prompt argument, ever.
  tool_args=()
  if [ -n "${HX_CONTINUITY_RUN:-}" ]; then
    selected_tools=$("$python" -c 'import json,sys;print(",".join(json.load(open(sys.argv[1]))["selected"]))' "$root/run/$id/toolset.json")
    tool_args=(--tools "$selected_tools")
  fi
  exec "$bin" \
    --dangerously-skip-permissions \
    --setting-sources user \
    --effort "$effort" \
    --model "$model" \
    ${tool_args[@]+"${tool_args[@]}"} \
    ${system_args[@]+"${system_args[@]}"}
fi

# --- session mode: put the launcher in window `main` of tmux session <id> ------------------
read -r -a TMUX_CMD <<< "${HX_TMUX:-tmux}"
command -v "${TMUX_CMD[0]}" >/dev/null 2>&1 || die "refuse: ${TMUX_CMD[0]} not found; every agent runs in tmux"

# The session env. HX_* variables (a test's tmux socket, a scratch binary) are
# forwarded so a scratch instance behaves exactly like a real one.
env_args=(
  -e HARNESS_ID="$id"
  -e HARNESS_ROOT="$root"
  -e CLAUDE_CONFIG_DIR="$home"
  -e DISABLE_AUTOUPDATER=1  # runtime stability: no binary swap under a running session
  -e IS_SANDBOX=1
  -e PATH="$root/bin:$PATH"
)
if [ -n "$autocompact" ]; then
  env_args+=(-e CLAUDE_CODE_AUTO_COMPACT_WINDOW="$autocompact")
fi
while IFS='=' read -r name value; do
  case "$name" in HX_*) env_args+=(-e "$name=$value") ;; esac
done < <(env)

launcher=("$here/start.sh" --exec "$id")
pane_log=$root/logs/$id/$id-pane.log
mkdir -p "$root/logs/$id"

if ! "${TMUX_CMD[@]}" has-session -t "=$id" 2>/dev/null; then
  "${TMUX_CMD[@]}" new-session -d -s "$id" -n main -c "$cwd" "${env_args[@]}" "${launcher[@]}"
else
  for arg in "${env_args[@]}"; do
    case "$arg" in
      -e) continue ;;
      *) "${TMUX_CMD[@]}" set-environment -t "=$id" "${arg%%=*}" "${arg#*=}" ;;
    esac
  done
  if "${TMUX_CMD[@]}" list-windows -t "=$id" -F '#{window_name}' | grep -qx main; then
    "${TMUX_CMD[@]}" respawn-window -k -t "=$id:main" -c "$cwd" "${launcher[@]}"
  else
    "${TMUX_CMD[@]}" new-window -d -t "=$id:" -n main -c "$cwd" "${launcher[@]}"
  fi
fi

# The pane capture, the UI's fallback for a dead session. Not a Companion
# stream: `hx.streams` ignores it and the runtime retains it only as bounded execution diagnostics.
"${TMUX_CMD[@]}" pipe-pane -t "=$id:main" 2>/dev/null || true
"${TMUX_CMD[@]}" pipe-pane -o -t "=$id:main" "cat >> '$pane_log'"

printf 'start.sh: %s running in tmux session %s window main (cwd %s)\n' "$bin" "$id" "$cwd"
printf 'start.sh: pane log %s\n' "$pane_log"
