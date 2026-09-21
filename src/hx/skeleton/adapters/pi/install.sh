#!/usr/bin/env bash
# adapters/pi/install.sh <id> — write the per-agent Pi home.
#
# The home is PI_CODING_AGENT_DIR for this agent. It holds settings (project trust
# never, compaction reserve below the native window), the hx extension, skills, and
# auth.json built from seed/pi-token. Nothing is read from ~/.pi.
#
# The Companion is still a Claude session. After the Pi home is written this runs
# adapters/claude/install.sh with HX_COMPANION_ONLY=1, which needs seed/token.
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

token_file=$root/seed/pi-token
[ -f "$token_file" ] || die \
  "refuse: no $token_file; paste the Pi provider API key there, mode 0600"
token_mode=$("$python" -c 'import os,sys;print(os.stat(sys.argv[1]).st_mode & 0o77)' "$token_file")
[ "$token_mode" = 0 ] || die \
  "refuse: $token_file is readable by group or other; it must be mode 0600. Run: chmod 600 $token_file"

claude_token=$root/seed/token
[ -f "$claude_token" ] || die \
  "refuse: no $claude_token; the Companion is a Claude session and needs it, mode 0600"

harness=$root/config/$id/harness.json
[ -f "$harness" ] || die "refuse: no $harness"

home=$root/run/$id/home
mkdir -p "$home/extensions/hx" "$home/sessions"
cp "$here/extension/index.ts" "$home/extensions/hx/index.ts"

cwd=$("$python" - "$harness" "$root" <<'CWDEOF'
import json, os, sys
workdir = (json.load(open(sys.argv[1])).get("workdir") or "").strip()
print(workdir if os.path.isabs(workdir) else os.path.join(sys.argv[2], workdir) if workdir
      else sys.argv[2])
CWDEOF
)

HX_ID=$id HX_ROOT=$root HX_HOME=$home HX_HARNESS=$harness HX_TOKEN=$token_file \
"$python" - <<'PYEOF'
import json
import os

home = os.environ["HX_HOME"]
root = os.environ["HX_ROOT"]
harness = json.load(open(os.environ["HX_HARNESS"]))
model = harness.get("model") or ""
provider = (harness.get("harness") or {}).get("provider")
if not provider and "/" in model:
    provider = model.split("/", 1)[0]
provider = provider or "anthropic"

key = open(os.environ["HX_TOKEN"]).read().strip()
auth_path = os.path.join(home, "auth.json")
with open(auth_path, "w") as handle:
    json.dump({provider: {"type": "api_key", "key": key}}, handle, indent=2)
    handle.write("\n")
os.chmod(auth_path, 0o600)

reserve = None
models_path = os.path.join(root, "config", "models.json")
if os.path.isfile(models_path):
    try:
        row = json.load(open(models_path)).get(model) or {}
    except (json.JSONDecodeError, OSError):
        row = {}
    window = row.get("window")
    auto = row.get("autocompact_window")
    if isinstance(window, int) and isinstance(auto, int) and window > auto > 0:
        reserve = window - auto

compaction = {"enabled": True, "keepRecentTokens": 20000}
if reserve is not None:
    compaction["reserveTokens"] = reserve

settings = {
    "defaultProjectTrust": "never",
    "compaction": compaction,
}
with open(os.path.join(home, "settings.json"), "w") as handle:
    json.dump(settings, handle, indent=2)
    handle.write("\n")
PYEOF

skills_src=${HX_SKILLS_DIR:-}
if [ -n "$skills_src" ] && [ -d "$skills_src" ]; then
  mkdir -p "$home/skills"
  for want in hx-worker hx-memory; do
    [ -d "$skills_src/$want" ] || continue
    rm -rf "$home/skills/$want"
    cp -R "$skills_src/$want" "$home/skills/$want"
  done
fi

HX_COMPANION_ONLY=1 bash "$root/adapters/claude/install.sh" "$id"

printf 'install.sh: wrote %s (Pi home, trust never, hx extension)\n' "$home/settings.json"
printf 'install.sh: auth is %s, written to %s\n' "$token_file" "$home/auth.json"
