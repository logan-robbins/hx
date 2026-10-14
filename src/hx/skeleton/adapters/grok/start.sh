#!/usr/bin/env bash
# adapters/grok/start.sh <id> — launch Grok Build in tmux window <id>:main.
#
# Bare: no prompt argument. The persona file is passed to --rules (appended to
# the system prompt in a <human_rules> block). --minimal is the default run
# mode: scrollback-native rendering, never the fullscreen TUI. The Companion is
# not started here; lifecycle calls adapters/claude/start.sh --companion for that.
set -euo pipefail

die() { printf 'start.sh: %s\n' "$*" >&2; exit 1; }

mode=session
while :; do
  case "${1:-}" in
    --exec) mode=exec; shift ;;
    --companion) die "refuse: the Companion is a Claude session; start.sh --companion is adapters/claude" ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || die "usage: start.sh [--exec] <id>"
id=$1
case "$id" in
  partner|[a-z]*-[0-9][0-9][0-9]) ;;
  *) die "refuse: \`$id\` is not an id (\`partner\` or \`<pod>-NNN\`)" ;;
esac
[ "$id" != partner ] || die "refuse: the Partner stays on claude"

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

home=$root/run/$id/home
harness=$root/config/$id/harness.json
# A planned launch's compiled system prompt (`native_launch`); nothing else writes it.
persona=$root/run/$id/persona.md

[ -f "$harness" ] || die "refuse: no $harness"
[ -f "$home/config.toml" ] || die "refuse: no $home/config.toml; run adapters/grok/install.sh $id first"

token_file=$root/seed/grok-token
[ -f "$token_file" ] || die "refuse: no $token_file"
token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
[ "$token_mode" = 0 ] || die "refuse: $token_file must be mode 0600"

cwd=$("$python" - "$harness" "$root" <<'CWDEOF'
import json, os, sys
workdir = (json.load(open(sys.argv[1])).get("workdir") or "").strip()
print(workdir if os.path.isabs(workdir) else os.path.join(sys.argv[2], workdir) if workdir
      else sys.argv[2])
CWDEOF
)
[ -d "$cwd" ] || die "refuse: no workdir $cwd; it is \`workdir\` in $harness and must exist"

bin=${HX_GROK_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/grok.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/grok.json")
fi
[ -n "$bin" ] || bin=$(command -v grok || true)
[ -n "$bin" ] || die "refuse: no grok binary; record {bin, version} in config/grok.json"
[ -x "$bin" ] || die "refuse: pinned grok binary $bin is not executable"

model=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["model"])' "$harness")
effort=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["effort"])' "$harness")
[ -n "$model" ] || die "refuse: $harness has no \`model\`"
[ -n "$effort" ] || die "refuse: $harness has no \`effort\`"
# Grok effort levels are low/medium/high/xhigh; hx max saturates at xhigh.
role=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["role"])' "$harness")
# The role persona, passed as it is (`config_harness.PERSONA_CHANNEL`): nothing is rendered
# or copied per id. A role without one launches without it.
role_persona=$root/personas/$role.md
[ "$effort" = max ] && effort=xhigh

if [ "$mode" = exec ]; then
  . "$root/adapters/load-env.sh"
  mkdir -p "$root/run/$id" "$home/sessions"
  rules_args=()
  if [ -n "${HX_CONTINUITY_CAPSULE:-}" ]; then
    # A planned launch's persona is the compiled planned system prompt, prepared in its
    # private root and verified by the controller (`native_launch`).
    [ -f "$persona" ] || die "refuse: no $persona; a planned launch prepares it (native_launch)"
    rules_args=(--rules "$(cat "$persona")")
  elif [ -f "$role_persona" ]; then
    rules_args=(--rules "$(cat "$role_persona")")
  fi

  cd "$cwd"
  export HARNESS_ID="$id"
  export HARNESS_ROOT="${HX_CONTINUITY_AUTHORITY:-$root}"
  export GROK_HOME="$home"
  export XAI_API_KEY="$(cat "$token_file")"
  export PATH="$root/bin:$PATH"

  # No prompt argument. --rules appends the persona to the system prompt; the invariants,
  # the identity line and the learnings arrive through the `context` hook at every boundary.
  # --minimal keeps the session in scrollback-native rendering.
  exec "$bin" --minimal --permission-mode bypassPermissions \
    -m "$model" --reasoning-effort "$effort" ${rules_args[@]+"${rules_args[@]}"}
fi

read -r -a TMUX_CMD <<< "${HX_TMUX:-tmux}"
command -v "${TMUX_CMD[0]}" >/dev/null 2>&1 || die "refuse: ${TMUX_CMD[0]} not found; every agent runs in tmux"

key=$(cat "$token_file")
env_args=(
  -e HARNESS_ID="$id"
  -e HARNESS_ROOT="$root"
  -e GROK_HOME="$home"
  -e XAI_API_KEY="$key"
  -e PATH="$root/bin:$PATH"
)
while IFS='=' read -r name value; do
  case "$name" in HX_*) env_args+=(-e "$name=$value") ;; esac
done < <(env)

launcher=("$here/start.sh" --exec "$id")
pane_log=$root/logs/$id/$id-pane.log
mkdir -p "$root/logs/$id"
window=main

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

"${TMUX_CMD[@]}" pipe-pane -t "=$id:main" 2>/dev/null || true
"${TMUX_CMD[@]}" pipe-pane -o -t "=$id:main" "cat >> '$pane_log'"

printf 'start.sh: %s running in tmux session %s window %s (cwd %s)\n' "$bin" "$id" "$window" "$cwd"
printf 'start.sh: pane log %s\n' "$pane_log"
