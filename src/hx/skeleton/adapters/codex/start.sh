#!/usr/bin/env bash
# adapters/codex/start.sh <id> — launch Codex in tmux window <id>:main.
#
# Bare: no prompt argument. --dangerously-bypass-approvals-and-sandbox is the
# unattended posture (no approval, no sandbox), matching approval_policy never
# and sandbox_mode danger-full-access baked into the home config.
# --dangerously-bypass-hook-trust runs the install-time hooks without a trust
# review, which the CLI documents for automation that already vets hook
# sources. The Companion is not started here; lifecycle calls
# adapters/claude/start.sh --companion for that.
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
# The Partner may run on Codex (spec 12). Its harness has no workdir, so the
# workdir lookup below falls back to HARNESS_ROOT, where the Partner runs.

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

[ -f "$harness" ] || die "refuse: no $harness"
[ -f "$agents" ] || die "refuse: no $agents"
grep -qxF "$HEADER" "$agents" || die \
  "refuse: $agents has no \`$HEADER\` line; the persona is the part above it"
[ -f "$home/config.toml" ] || die "refuse: no $home/config.toml; run adapters/codex/install.sh $id first"
[ -f "$home/auth.json" ] || die "refuse: no $home/auth.json; run adapters/codex/install.sh $id first"

# Auth lives in the home (`auth.json`, required above): a copied ChatGPT login
# or a provisioned API key. The seed token is only needed at install time, so
# it is optional here — but when present it must still be mode 0600.
token_file=$root/seed/codex-token
if [ -f "$token_file" ]; then
  token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
  [ "$token_mode" = 0 ] || die "refuse: $token_file must be mode 0600"
fi

cwd=$("$python" - "$harness" "$root" <<'CWDEOF'
import json, os, sys
workdir = (json.load(open(sys.argv[1])).get("workdir") or "").strip()
print(workdir if os.path.isabs(workdir) else os.path.join(sys.argv[2], workdir) if workdir
      else sys.argv[2])
CWDEOF
)
[ -d "$cwd" ] || die "refuse: no workdir $cwd; it is \`workdir\` in $harness and must exist"

bin=${HX_CODEX_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/codex.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/codex.json")
fi
[ -n "$bin" ] || bin=$(command -v codex || true)
[ -n "$bin" ] || die "refuse: no codex binary; record {bin, version} in config/codex.json"
[ -x "$bin" ] || die "refuse: pinned codex binary $bin is not executable"

model=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["model"])' "$harness")
effort=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["effort"])' "$harness")
[ -n "$model" ] || die "refuse: $harness has no \`model\`"
[ -n "$effort" ] || die "refuse: $harness has no \`effort\`"

if [ "$mode" = exec ]; then
  . "$root/adapters/load-env.sh"
  mkdir -p "$root/run/$id" "$home/sessions"
  awk -v header="$HEADER" '$0 == header {exit} {print}' "$agents" > "$persona"

  cd "$cwd"
  export HARNESS_ID="$id"
  export HARNESS_ROOT="$root"
  export CODEX_HOME="$home"
  export PATH="$root/bin:$PATH"

  # No prompt argument. -m takes the harness model id; the reasoning effort
  # rides a -c override because the CLI exposes no --effort flag.
  # --dangerously-bypass-approvals-and-sandbox is the whole unattended posture:
  # approvals are gone, so no -a/--ask-for-approval is passed (the current CLI
  # rejects that combination outright). -s stays explicit so the sandbox mode
  # is on the record. --dangerously-bypass-hook-trust runs install-time hooks.
  exec "$bin" --dangerously-bypass-approvals-and-sandbox \
    --dangerously-bypass-hook-trust \
    -s danger-full-access \
    -m "$model" -c "model_reasoning_effort=\"$effort\""
fi

read -r -a TMUX_CMD <<< "${HX_TMUX:-tmux}"
command -v "${TMUX_CMD[0]}" >/dev/null 2>&1 || die "refuse: ${TMUX_CMD[0]} not found; every agent runs in tmux"

env_args=(
  -e HARNESS_ID="$id"
  -e HARNESS_ROOT="$root"
  -e CODEX_HOME="$home"
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
