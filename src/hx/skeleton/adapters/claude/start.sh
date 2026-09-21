#!/usr/bin/env bash
# adapters/claude/start.sh <id> — the entire launch; nothing else starts a Claude Code
# process (spec 17.4).
#
#   start.sh <id>          ensure tmux session <id> with the session env of spec 11, and run
#                          the launcher in window `main` (cwd harness.json.workdir,
#                          HARNESS_ROOT for partner)
#   start.sh --exec <id>   the launcher itself: derive run/<id>/persona.md from
#                          config/<id>/AGENTS.md above `## UPDATES BELOW ONLY`, then exec
#
# The argv is exactly spec 17.4: no prompt argument, no --resume, no compaction variables.
#
# Auth is the instance token at $HARNESS_ROOT/seed/token (spec 11 Auth, CONTRACTS.md), read
# from the file by the launcher itself and exported as CLAUDE_CODE_OAUTH_TOKEN. It is never an
# argument to anything — not to `env`, not to `tmux -e` — so it cannot appear in `ps` output,
# and it is never written under run/. Refuses a home without settings, or an instance without
# a 0600 token.
set -euo pipefail

die() { printf 'start.sh: %s\n' "$*" >&2; exit 1; }

HEADER='## UPDATES BELOW ONLY'

# `--companion` launches the Companion's own Claude Code session in window `companion`
# (spec 10). There is no headless path: every model call in hx is a tmux session.
mode=session
role=agent
while :; do
  case "${1:-}" in
    --exec) mode=exec; shift ;;
    --companion) role=companion; shift ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || die "usage: start.sh [--exec] <id>"
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

if [ "$role" = companion ]; then
  home=$root/run/$id/companion-home
  window=companion
else
  home=$root/run/$id/home
  window=main
fi
agents=$root/config/$id/AGENTS.md
harness=$root/config/$id/harness.json
persona=$root/run/$id/persona.md
companion_system=$root/run/$id/companion-system.md

# --- refusals, checked in both modes so a bad instance fails before tmux is touched -------
[ -f "$harness" ] || die "refuse: no $harness (spec 05)"
if [ "$role" = companion ]; then
  [ -f "$companion_system" ] || die \
    "refuse: no $companion_system; \`hx companion $id\` composes it from companion/BASE.md and
    the role file before launching (spec 10)"
fi
[ "$role" = companion ] || [ -f "$agents" ] || die "refuse: no $agents; the persona is derived from it at every launch (spec 11)"
[ "$role" = companion ] || grep -qxF "$HEADER" "$agents" || die \
  "refuse: $agents has no \`$HEADER\` line; the persona is the part above it and the agent's
  own memory is the part below it (spec 03, 04). Without the header hx cannot tell them apart"
[ -f "$home/settings.json" ] || die \
  "refuse: no $home/settings.json; run adapters/claude/install.sh $id first (spec 11)"

token_file=$root/seed/token
[ -f "$token_file" ] || die \
  "refuse: no $token_file; the human runs \`claude setup-token\` once and pastes the token
  there, mode 0600 (spec 11 Auth). Without it a launch would stop at a login prompt"
token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
[ "$token_mode" = 0 ] || die \
  "refuse: $token_file is readable by group or other; it holds a year-long credential and must
  be mode 0600 (CONTRACTS.md). Run: chmod 600 $token_file"

# The directory this agent runs in: `workdir` from config/<id>/harness.json, whatever the
# Partner chose for it (spec 17.2). hx creates no repository and no branch there.
if [ "$id" = partner ]; then
  cwd=$root
else
  cwd=$("$python" - "$harness" "$root" <<'CWDEOF'
import json, os, sys
workdir = (json.load(open(sys.argv[1])).get("workdir") or "").strip()
print(workdir if os.path.isabs(workdir) else os.path.join(sys.argv[2], workdir) if workdir
      else sys.argv[2])
CWDEOF
)
  [ -d "$cwd" ] || die "refuse: no workdir $cwd; it is \`workdir\` in $harness and must exist (spec 17.2)"
fi

bin=${HX_CLAUDE_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/claude.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/claude.json")
fi
[ -n "$bin" ] || bin=$(command -v claude || true)
[ -n "$bin" ] || die "refuse: no claude binary; \`hx install\` records {bin, version} in config/claude.json (spec 17.1)"
[ -x "$bin" ] || die "refuse: pinned claude binary $bin is not executable (spec 17.1)"

if [ "$role" = companion ]; then
  model=$("$python" -c 'import json,sys;d=json.load(open(sys.argv[1]));print((d.get("companion") or {}).get("model") or d["model"])' "$harness")
  effort=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("effort","low"))' "$harness")
else
  model=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["model"])' "$harness")
  effort=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["effort"])' "$harness")
fi
[ -n "$model" ] || die "refuse: $harness has no \`model\` (spec 05)"
[ -n "$effort" ] || die "refuse: $harness has no \`effort\` (spec 05)"

if [ "$mode" = exec ]; then
  # Regenerated immediately before exec, so a persona edit takes effect at the next launch
  # and never leaks the agent's own memory below the header into the system prompt.
  mkdir -p "$root/run/$id"
  if [ "$role" = companion ]; then
    system_prompt=$companion_system
  else
    awk -v header="$HEADER" '$0 == header {exit} {print}' "$agents" > "$persona"
    system_prompt=$persona
  fi

  cd "$cwd"
  # The session env of spec 11 and 17.4. Exported rather than passed to `env`, so the token
  # never becomes an argv element of anything.
  export HARNESS_ID="$id"
  export HX_ROLE="$role"
  export HARNESS_ROOT="$root"
  export CLAUDE_CONFIG_DIR="$home"
  export DISABLE_AUTOUPDATER=1
  # Every agent runs sandboxed and with permissions bypassed, always (spec 11). The
  # `.claude.json` pre-seed stays too: both mechanisms, so no dialog can ever appear.
  export IS_SANDBOX=1
  CLAUDE_CODE_OAUTH_TOKEN=$(cat "$token_file")
  export CLAUDE_CODE_OAUTH_TOKEN

  # Spec 17.4, exactly. No prompt argument, ever.
  exec "$bin" \
    --dangerously-skip-permissions \
    --effort "$effort" \
    --model "$model" \
    --append-system-prompt-file "$system_prompt"
fi

# --- session mode: put the launcher in window `main` of tmux session <id> ------------------
read -r -a TMUX_CMD <<< "${HX_TMUX:-tmux}"
command -v "${TMUX_CMD[0]}" >/dev/null 2>&1 || die "refuse: ${TMUX_CMD[0]} not found; every agent runs in tmux (spec 17.4)"

# The session env of spec 11. HX_* variables (a test's tmux socket, a scratch binary) are
# forwarded so a scratch instance behaves exactly like a real one.
env_args=(
  -e HARNESS_ID="$id"
  -e HARNESS_ROOT="$root"
  -e CLAUDE_CONFIG_DIR="$home"
  -e DISABLE_AUTOUPDATER=1
  -e IS_SANDBOX=1
)
while IFS='=' read -r name value; do
  case "$name" in HX_*) env_args+=(-e "$name=$value") ;; esac
done < <(env)

if [ "$role" = companion ]; then
  launcher=("$here/start.sh" --exec --companion "$id")
else
  launcher=("$here/start.sh" --exec "$id")
fi
pane_log=$root/logs/$id/$id-pane.log
mkdir -p "$root/logs/$id"

if ! "${TMUX_CMD[@]}" has-session -t "=$id" 2>/dev/null; then
  "${TMUX_CMD[@]}" new-session -d -s "$id" -n "$window" -c "$cwd" "${env_args[@]}" "${launcher[@]}"
else
  for arg in "${env_args[@]}"; do
    case "$arg" in
      -e) continue ;;
      *) "${TMUX_CMD[@]}" set-environment -t "=$id" "${arg%%=*}" "${arg#*=}" ;;
    esac
  done
  if "${TMUX_CMD[@]}" list-windows -t "=$id" -F '#{window_name}' | grep -qx "$window"; then
    "${TMUX_CMD[@]}" respawn-window -k -t "=$id:$window" -c "$cwd" "${launcher[@]}"
  else
    "${TMUX_CMD[@]}" new-window -d -t "=$id:" -n "$window" -c "$cwd" "${launcher[@]}"
  fi
fi

# The pane capture, the UI's fallback for a dead session (spec 03, 11). Not a Companion
# stream: `hx.streams` ignores it and `hx dispatch` archives it with the rest of logs/<id>/.
if [ "$role" != companion ]; then
  "${TMUX_CMD[@]}" pipe-pane -t "=$id:main" 2>/dev/null || true
  "${TMUX_CMD[@]}" pipe-pane -o -t "=$id:main" "cat >> '$pane_log'"
fi

printf 'start.sh: %s running in tmux session %s window %s (cwd %s)\n' "$bin" "$id" "$window" "$cwd"
[ "$role" = companion ] || printf 'start.sh: pane log %s\n' "$pane_log"
