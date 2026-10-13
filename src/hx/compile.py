"""`hx compile <id> --planned` — versioned prompts for the planned runtime, and the shared
prompt-text helpers.

The persona a session launches with is not compiled into a per-id file any more: every
`start.sh` renders `run/<id>/persona.md` from its sources at exec time (`hx.persona`,
`hx persona <id>`), so the only `compile` left is the planned one (`hx.prompt_compiler`,
docs/prompt-runtime.md). This module keeps what both read:

  - the fenced-region markers a rendered persona carries — `hx:global` (the invariants, only
    where the persona is their channel: `config_harness.INVARIANTS_CHANNEL`) and
    `hx:learnings` (`hx.learn.persona_region`) — which `hx cache report` uses to tell what the
    persona already holds, and `REGION_RE`, which strips regions an earlier hx stamped into a
    source file;
  - `expand_identity` and `identity_values`: the `{{id}}`, `{{pod}}`, `{{role}}`, `{{flavor}}`
    and `{{workdir}}` a persona may name, resolved once, never invented;
  - `strip_known_blocks`, which removes exact copies of a shared source from operator text.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .errors import ValidationError

GLOBAL_BEGIN = "<!-- hx:global begin (from config/CLAUDE.md — edit the source, not this copy) -->"
GLOBAL_END = "<!-- hx:global end -->"
ROLE_BEGIN_TMPL = "<!-- hx:role begin (from personas/{role}/AGENTS.md — edit the source, not this copy) -->"
ROLE_END = "<!-- hx:role end -->"
LEARNINGS_BEGIN = "<!-- hx:learnings begin (written by hx from the LEARNINGS directory — not by hand) -->"
LEARNINGS_END = "<!-- hx:learnings end -->"

#: A fenced region. `role` is only ever read: earlier versions stamped it into per-id files.
REGION_RE = re.compile(
    r"<!-- hx:(?P<kind>global|role|learnings) begin .*?-->\n"
    r"(?P<body>.*?)"
    r"<!-- hx:(?P=kind) end -->",
    re.DOTALL,
)

VARIABLE_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z_0-9]*)\}\}")


def expand_identity(text: str, values: dict[str, str]) -> str:
    """Resolve known identity variables once; never ship an invented identity."""
    unknown = set(VARIABLE_RE.findall(text)) - values.keys()
    if unknown:
        raise ValidationError("unresolved prompt identity variables: " + ", ".join(sorted(unknown)))
    return VARIABLE_RE.sub(lambda match: values[match[1]], text)


def identity_values(root: Path, item_id: str) -> dict[str, str]:
    from .config_harness import load_harness, resolve_workdir
    config = load_harness(root / "config" / item_id / "harness.json", check_cross_file=False)
    return {"id": config.id, "pod": config.pod, "role": config.role, "flavor": config.flavor,
            "workdir": str(resolve_workdir(config.workdir, root) if config.workdir else root.resolve())}


def strip_known_blocks(text: str, known: list[str]) -> str:
    result = text.strip("\n")
    for body in sorted(set(filter(None, known)), key=len, reverse=True):
        result = re.sub(r"(?:\A|(?<=\n\n))" + re.escape(body.strip("\n")) + r"(?=\n\n|\Z)", "", result)
    return result.strip("\n")


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx compile", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--planned", action="store_true", help="build versioned prompts for the planned runtime without activating them")
    parser.add_argument("--audience", choices=("worker", "partner", "companion", "subagent"))
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not args.planned:
        raise ValidationError(
            "hx compile without --planned is gone: the persona is rendered from its sources at "
            f"every launch (`hx persona {args.id}` renders it now, `--print` shows it)"
        )
    from .prompt_compiler import build
    from .continuity_store import canonical
    print(canonical(build(root, args.id, audience=args.audience)))
    return 0
