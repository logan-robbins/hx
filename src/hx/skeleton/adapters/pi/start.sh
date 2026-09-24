#!/usr/bin/env bash
# adapters/pi/start.sh <id> — launch interactive Pi in tmux window <id>:main.
#
# Bare: no prompt argument. The persona file is passed to --append-system-prompt,
# which Pi 0.84 reads as a file when the path exists. --no-approve and
# --no-extensions keep a checkout's .pi/ from loading. The Companion is not
# started here; lifecycle calls adapters/claude/start.sh --companion for that.
set -euo pipefail

die() { printf 'start.sh: %s\n' "$*" >&2; exit 1; }

HEADER='## UPDATES BELOW ONLY'

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
agents=$root/config/$id/AGENTS.md
harness=$root/config/$id/harness.json
persona=$root/run/$id/persona.md
extension=$home/extensions/hx/index.ts

[ -f "$harness" ] || die "refuse: no $harness"
[ -f "$agents" ] || die "refuse: no $agents"
grep -qxF "$HEADER" "$agents" || die \
  "refuse: $agents has no \`$HEADER\` line; the persona is the part above it"
[ -f "$home/settings.json" ] || die "refuse: no $home/settings.json; run adapters/pi/install.sh $id first"
[ -f "$extension" ] || die "refuse: no $extension; run adapters/pi/install.sh $id first"
[ -f "$home/auth.json" ] || die "refuse: no $home/auth.json; run adapters/pi/install.sh $id first"

token_file=$root/seed/pi-token
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

bin=${HX_PI_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/pi.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/pi.json")
fi
[ -n "$bin" ] || bin=$(command -v pi || true)
[ -n "$bin" ] || die "refuse: no pi binary; record {bin, version} in config/pi.json"
[ -x "$bin" ] || die "refuse: pinned pi binary $bin is not executable"

model=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["model"])' "$harness")
effort=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["effort"])' "$harness")
[ -n "$model" ] || die "refuse: $harness has no \`model\`"
[ -n "$effort" ] || die "refuse: $harness has no \`effort\`"

pi_args=(
  --no-extensions
  -e "$extension"
  --no-skills
)
if [ -d "$home/skills" ]; then
  for skill in "$home/skills"/*; do
    [ -d "$skill" ] || continue
    pi_args+=(--skill "$skill")
  done
fi
pi_args+=(
  --no-prompt-templates
  --no-themes
  --no-context-files
  --no-approve
  --offline
  --thinking "$effort"
  --model "$model"
  --session-dir "$home/sessions"
)

if [ "$mode" = exec ]; then
  . "$root/adapters/load-env.sh"
  mkdir -p "$root/run/$id" "$home/sessions"
  awk -v header="$HEADER" '$0 == header {exit} {print}' "$agents" > "$persona"
  cp "$persona" "$home/APPEND_SYSTEM.md"

  cd "$cwd"
  export HARNESS_ID="$id"
  export HARNESS_ROOT="$root"
  export PI_CODING_AGENT_DIR="$home"
  export PI_CODING_AGENT_SESSION_DIR="$home/sessions"
  export PI_OFFLINE=1
  export PI_SKIP_VERSION_CHECK=1
  export PATH="$root/bin:$PATH"

  # No prompt argument. --append-system-prompt reads the persona file when the path exists
  # (Pi 0.84 resolvePromptInput). APPEND_SYSTEM.md is the same text for inspection;
  # the flag suppresses Pi's own discovery of that file, so it is not appended twice.
  exec "$bin" "${pi_args[@]}" --append-system-prompt "$persona"
fi

read -r -a TMUX_CMD <<< "${HX_TMUX:-tmux}"
command -v "${TMUX_CMD[0]}" >/dev/null 2>&1 || die "refuse: ${TMUX_CMD[0]} not found; every agent runs in tmux"

env_args=(
  -e HARNESS_ID="$id"
  -e HARNESS_ROOT="$root"
  -e PI_CODING_AGENT_DIR="$home"
  -e PI_CODING_AGENT_SESSION_DIR="$home/sessions"
  -e PI_OFFLINE=1
  -e PI_SKIP_VERSION_CHECK=1
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
