#!/usr/bin/env bash
# adapters/claude/start.sh <id> — the entire launch; nothing else starts a Claude Code
# process (spec 17.4).
#
#   start.sh <id>          ensure tmux session <id> with the session env of spec 11, and run
#                          the launcher in window `main` (cwd wt/<id>, HARNESS_ROOT for partner)
#   start.sh --exec <id>   the launcher itself: derive run/<id>/persona.md from
#                          config/<id>/AGENTS.md above `## UPDATES BELOW ONLY`, then exec
#
# The argv is exactly spec 17.4: no prompt argument, no --resume, no compaction variables.
# Refuses a home without settings and credentials (spec 11 Auth).
set -euo pipefail

die() { printf 'start.sh: %s\n' "$*" >&2; exit 1; }

HEADER='## UPDATES BELOW ONLY'

mode=session
if [ "${1:-}" = "--exec" ]; then mode=exec; shift; fi
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

home=$root/run/$id/home
agents=$root/config/$id/AGENTS.md
harness=$root/config/$id/harness.json
persona=$root/run/$id/persona.md

# --- refusals, checked in both modes so a bad instance fails before tmux is touched -------
[ -f "$harness" ] || die "refuse: no $harness (spec 05)"
[ -f "$agents" ]  || die "refuse: no $agents; the persona is derived from it at every launch (spec 11)"
grep -qxF "$HEADER" "$agents" || die \
  "refuse: $agents has no \`$HEADER\` line; the persona is the part above it and the agent's
  own memory is the part below it (spec 03, 04). Without the header hx cannot tell them apart"
[ -f "$home/settings.json" ] || die \
  "refuse: no $home/settings.json; run adapters/claude/install.sh $id first (spec 11)"
[ -f "$home/.credentials.json" ] || die \
  "refuse: no $home/.credentials.json; a home without credentials would stop at a login
  prompt (spec 11 Auth). Run adapters/claude/install.sh $id after the seed login"

if [ "$id" = partner ]; then
  cwd=$root
else
  cwd=$root/wt/$id
  [ -d "$cwd" ] || die "refuse: no worktree $cwd; \`hx launch $id\` cuts it from the mirror (spec 17.3)"
fi

bin=${HX_CLAUDE_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/claude.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/claude.json")
fi
[ -n "$bin" ] || bin=$(command -v claude || true)
[ -n "$bin" ] || die "refuse: no claude binary; \`hx install\` records {bin, version} in config/claude.json (spec 17.1)"
[ -x "$bin" ] || die "refuse: pinned claude binary $bin is not executable (spec 17.1)"

model=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["model"])' "$harness")
effort=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["effort"])' "$harness")
[ -n "$model" ] || die "refuse: $harness has no \`model\` (spec 05)"
[ -n "$effort" ] || die "refuse: $harness has no \`effort\` (spec 05)"

if [ "$mode" = exec ]; then
  # Regenerated immediately before exec, so a persona edit takes effect at the next launch
  # and never leaks the agent's own memory below the header into the system prompt.
  mkdir -p "$root/run/$id"
  awk -v header="$HEADER" '$0 == header {exit} {print}' "$agents" > "$persona"

  cd "$cwd"
  # Spec 17.4, exactly. No prompt argument, ever.
  exec env \
    HARNESS_ID="$id" \
    HARNESS_ROOT="$root" \
    CLAUDE_CONFIG_DIR="$home" \
    DISABLE_AUTOUPDATER=1 \
    "$bin" \
    --dangerously-skip-permissions \
    --effort "$effort" \
    --model "$model" \
    --append-system-prompt-file "$persona"
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
)
while IFS='=' read -r name value; do
  case "$name" in HX_*) env_args+=(-e "$name=$value") ;; esac
done < <(env)

launcher=("$here/start.sh" --exec "$id")

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

printf 'start.sh: %s running in tmux session %s window main (cwd %s)\n' "$bin" "$id" "$cwd"
