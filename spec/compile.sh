#!/usr/bin/env bash
# Recompile HARNESS_SPEC.md from the numbered section files, in order.
# Each section is wrapped in <!-- BEGIN file --> / <!-- END file --> markers,
# so a single section can be replaced deterministically without recompiling.
set -euo pipefail
cd "$(dirname "$0")"
out=HARNESS_SPEC.md
{
  for f in [0-9][0-9]-*.md; do
    printf '<!-- BEGIN %s -->\n' "$f"
    cat "$f"
    [ -n "$(tail -c1 "$f")" ] && printf '\n'
    printf '<!-- END %s -->\n\n' "$f"
  done
} > "$out.tmp" && mv "$out.tmp" "$out"
echo "$out: $(grep -c '^<!-- BEGIN' "$out") sections, $(wc -l < "$out") lines"
