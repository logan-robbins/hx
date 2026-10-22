#!/usr/bin/env bash
# Execute Muse Code inside the controller's admitted native launch capsule.
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

# Every worker executable descends from the controller's admitted run supervisor.
"$python" -m hx.adapter_guard "$id" meta "$mode" agent || die "worker launch admission refused"

home=$root/run/$id/home
harness=$root/config/$id/harness.json
# A planned launch's compiled system prompt (`native_launch`); nothing else writes it.
persona=$root/run/$id/persona.md

[ -f "$harness" ] || die "refuse: no $harness"
[ -f "$home/muse/settings.json" ] || die "refuse: no $home/muse/settings.json; run adapters/meta/install.sh $id first"

token_file=$root/seed/meta-token
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

bin=${HX_META_BIN:-}
if [ -z "$bin" ] && [ -f "$root/config/meta.json" ]; then
  bin=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("bin") or "")' \
    "$root/config/meta.json")
fi
[ -n "$bin" ] || bin=$(command -v muse || true)
[ -n "$bin" ] || die "refuse: no muse binary; record its path as \`bin\` in config/meta.json"
[ -x "$bin" ] || die "refuse: muse binary $bin is not executable"

model=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["model"])' "$harness")
effort=$("$python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["effort"])' "$harness")
[ -n "$model" ] || die "refuse: $harness has no \`model\`"
[ -n "$effort" ] || die "refuse: $harness has no \`effort\`"

. "$root/adapters/load-env.sh"
mkdir -p "$root/run/$id" "$home/data"
[ -f "$persona" ] || die "refuse: frozen native system instructions are missing"


cd "$cwd"
export HARNESS_ID="$id"
export HARNESS_ROOT="$HX_CONTINUITY_AUTHORITY"
export XDG_CONFIG_HOME="$home"
export XDG_DATA_HOME="$home/data"
export META_API_KEY="$(cat "$token_file")"
export PATH="$root/bin:$PATH"

# No prompt argument. --yolo disables approval and sandboxing and trusts the
# workspace for this run. Muse takes no system prompt: the invariants, the persona, the
# identity line and the learnings arrive through the `context` hook at every boundary.
exec "$bin" --yolo --model "$model" --reasoning-effort "$effort"
