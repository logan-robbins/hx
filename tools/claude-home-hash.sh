#!/usr/bin/env bash
# Manifest of the user's Claude Code configuration surface (~/.claude by default).
# Prints "<sha256>  <relative path>" per file (and "link:<target>  <path>" per symlink), sorted,
# for every file that is configuration rather than session-volatile or cloud-synced state.
# Pipe through shasum for one hash.
#
# Excluded (written by the user's own Claude sessions or by Claude's cloud sync, never by hx):
#   projects/ sessions/ shell-snapshots/ telemetry/ cache/ paste-cache/ session-env/ backups/ file-history/
#   plugins/cache/ plugins/marketplaces/ plugins/repos/ plugins/synced/ skills/synced/
#   history.jsonl .last-* *.lock .DS_Store remote-settings.json policy-limits.json(.stamp.json)
#   (the last three are Claude Code's server-pushed account settings, refreshed by the client)
# plugins/known_marketplaces.json is hashed with its lastUpdated timestamps removed.
# Everything else is included: settings*.json, CLAUDE.md, skills/ (user-authored and symlinked),
# agents/, commands/, hooks/, plugins/*.json, keybindings.json, .credentials.json, statusline, etc.
set -euo pipefail
dir="${1:-$HOME/.claude}"
cd "$dir"
prune=( -path './projects' -o -path './sessions' -o -path './shell-snapshots' -o -path './telemetry'
        -o -path './cache' -o -path './paste-cache' -o -path './session-env' -o -path './backups'
        -o -path './plugins/cache' -o -path './plugins/marketplaces' -o -path './plugins/repos'
        -o -path './plugins/synced' -o -path './skills/synced' -o -path './file-history' )
{
find . \( "${prune[@]}" \) -prune -o -type l -print0 \
  | sort -z | while IFS= read -r -d '' l; do printf 'link:%s  %s\n' "$(readlink "$l")" "${l#./}"; done
find . \( "${prune[@]}" \) -prune -o -type f \
  -not -name 'history.jsonl' -not -name '.last-*' -not -name '*.lock' -not -name '.DS_Store' \
  -not -name 'remote-settings.json' -not -name 'policy-limits.json' -not -name 'policy-limits.json.stamp.json' \
  -not -path './plugins/known_marketplaces.json' -print0 \
  | sort -z | xargs -0 shasum -a 256 | sed 's#  \./#  #'
if [ -f plugins/known_marketplaces.json ]; then
  printf '%s  %s\n' "$(sed -E 's/"lastUpdated": *"[^"]*"/"lastUpdated": ""/g' plugins/known_marketplaces.json | shasum -a 256 | cut -d' ' -f1)" "plugins/known_marketplaces.json"
fi
} | sort -k2
