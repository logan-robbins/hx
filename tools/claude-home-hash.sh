#!/usr/bin/env bash
# Manifest of the user's Claude Code configuration surface (~/.claude by default).
# Prints "<sha256>  <relative path>" per file, sorted, for every file that is
# configuration rather than session-volatile state. Pipe through sha256sum for one hash.
#
# Excluded (written by the user's own Claude sessions, never by hx, so not a signal):
#   projects/ sessions/ shell-snapshots/ telemetry/ cache/ paste-cache/ session-env/
#   backups/ plugins/cache/ plugins/marketplaces/ plugins/repos/ history.jsonl .last-* *.lock
# Everything else is included: settings*.json, CLAUDE.md, skills/, agents/, commands/,
# hooks/, plugins/*.json, keybindings.json, .credentials.json, statusline, etc.
set -euo pipefail
dir="${1:-$HOME/.claude}"
cd "$dir"
{
find . -type l \
  -not -path './projects/*' -not -path './plugins/cache/*' -not -path './plugins/marketplaces/*' \
  -print0 | sort -z | while IFS= read -r -d '' l; do printf 'link:%s  %s\n' "$(readlink "$l")" "${l#./}"; done
find . -type f \
  -not -path './projects/*' -not -path './sessions/*' -not -path './shell-snapshots/*' \
  -not -path './telemetry/*' -not -path './cache/*' -not -path './paste-cache/*' \
  -not -path './session-env/*' -not -path './backups/*' \
  -not -path './plugins/cache/*' -not -path './plugins/marketplaces/*' -not -path './plugins/repos/*' \
  -not -name 'history.jsonl' -not -name '.last-*' -not -name '*.lock' -not -name '.DS_Store' \
  -print0 | sort -z | xargs -0 shasum -a 256 | sed 's#  \./#  #'
} | sort -k2
