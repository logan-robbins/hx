#!/usr/bin/env bash
# Execute Pi inside the controller's admitted native launch capsule.
# The controller owns tmux/process creation, immutable prompts and checkpoint delivery.
set -euo pipefail

die() { printf 'start.sh: %s\n' "$*" >&2; exit 1; }

[ "${1:-}" = --exec ] || die "worker launch admission refused: use hx launch ID --run RUN --request REQUEST"
shift
[ $# -eq 1 ] || die "usage: start.sh --exec <id>"
mode=exec
id=$1
case "$id" in
  partner|[a-z]*-[0-9][0-9][0-9]) ;;
  *) die "refuse: \`$id\` is not an id (\`partner\` or \`<pod>-NNN\`)" ;;
esac
[ "$id" != partner ] || die "refuse: the Partner stays on claude"

root=${HARNESS_ROOT:?hx exports it for every adapter}
[ -d "$root" ] || die "HARNESS_ROOT $root does not exist"

# The interpreter hx runs on: hx sets HX_PYTHON for every adapter it runs.
python=${HX_PYTHON:-}
[ -n "$python" ] || die "HX_PYTHON is unset; hx runs this adapter with the interpreter it runs on"
command -v "$python" >/dev/null 2>&1 || die "$python not found; hx needs Python 3.14"

# Every worker executable descends from the controller's admitted run supervisor.
"$python" -m hx.adapter_guard "$id" pi "$mode" agent || die "worker launch admission refused"

home=$root/run/$id/home
harness=$root/config/$id/harness.json
# A planned launch's compiled system prompt (`native_launch`); nothing else writes it.
persona=$root/run/$id/persona.md
extension=$home/extensions/hx/index.ts

[ -f "$harness" ] || die "refuse: no $harness"
[ -f "$home/settings.json" ] || die "refuse: no $home/settings.json; run adapters/pi/install.sh $id first"
[ -f "$extension" ] || die "refuse: no $extension; run adapters/pi/install.sh $id first"
[ -f "$home/auth.json" ] || die "refuse: no $home/auth.json; run adapters/pi/install.sh $id first"

token_file=$root/seed/pi-token
[ -f "$token_file" ] || die "refuse: no $token_file"
token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
[ "$token_mode" = 0 ] || die "refuse: $token_file must be mode 0600"

cwd=$("$python" - "$harness" "$root" <<'CWDEOF'
import json, os, sys
# A relative workdir resolves against HARNESS_ROOT; an absolute one stands.
print(os.path.join(sys.argv[2], json.load(open(sys.argv[1]))["workdir"]))
CWDEOF
)
[ -d "$cwd" ] || die "refuse: no workdir $cwd; it is \`workdir\` in $harness and must exist"

bin=${HX_PI_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/pi.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/pi.json")
fi
[ -n "$bin" ] || bin=$(command -v pi || true)
[ -n "$bin" ] || die "refuse: no pi binary; record its path as \`bin\` in config/pi.json"
[ -x "$bin" ] || die "refuse: pi binary $bin is not executable"

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

. "$root/adapters/load-env.sh"
mkdir -p "$root/run/$id" "$home/sessions"
system_args=()
[ -f "$persona" ] || die "refuse: frozen native system instructions are missing"
system_args=(--append-system-prompt "$persona")


cd "$cwd"
export HARNESS_ID="$id"
export HARNESS_ROOT="$HX_CONTINUITY_AUTHORITY"
export PI_CODING_AGENT_DIR="$home"
export PI_CODING_AGENT_SESSION_DIR="$home/sessions"
# Runtime stability: no startup network, so no update check or package update mid-session.
export PI_OFFLINE=1
export PATH="$root/bin:$PATH"

# No prompt argument. --append-system-prompt reads the persona file when the path exists
# (Pi's resolvePromptInput). The invariants, the identity line and the learnings arrive
# through the extension's `context` hook at every boundary.
exec "$bin" "${pi_args[@]}" ${system_args[@]+"${system_args[@]}"}
