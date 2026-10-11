"""`hx compile <id>` — the template compiler (spec 11 Identity).

Base rules live in exactly two Partner-managed sources and nowhere else:

  `config/CLAUDE.md`         the global invariants, for every agent
  `personas/<role>/AGENTS.md` the role persona, above `## UPDATES BELOW ONLY`

plus one line hx writes itself: where the central `LEARNINGS.md` is and how many rules it
holds (`hx.learn.persona_region`), so every agent is told to read it and how to propose to
it without a persona having to say so.

`compile` distributes them into the worker's final `config/<id>/AGENTS.md` as
fenced regions, preserving the per-id text the Partner wrote there and the
agent's own memory below the header. It runs on every `hx launch` and
`hx restart`, so a base edit lands at the next (re)start — the Partner never
hand-copies a persona to push a change out. The Partner itself is exempt:
`config/partner/AGENTS.md` stays the shipped persona byte for byte.

A role with no `personas/<role>/AGENTS.md` still compiles: the global region
is distributed and the role region is left out, so test and bespoke roles keep
working.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .errors import HxError, NotFound, ValidationError
from .ids import PARTNER

HEADER = "## UPDATES BELOW ONLY"

GLOBAL_BEGIN = "<!-- hx:global begin (from config/CLAUDE.md — edit the source, not this copy) -->"
GLOBAL_END = "<!-- hx:global end -->"
ROLE_BEGIN_TMPL = "<!-- hx:role begin (from personas/{role}/AGENTS.md — edit the source, not this copy) -->"
ROLE_END = "<!-- hx:role end -->"
LEARNINGS_BEGIN = "<!-- hx:learnings begin (written by hx compile from the LEARNINGS file — not by hand) -->"
LEARNINGS_END = "<!-- hx:learnings end -->"

#: The regions in the order they are rendered.
KINDS = ("global", "role", "learnings")

#: A fenced region, replaced whole on every compile.
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


def role_of(root: Path, item_id: str) -> str:
    """This worker's role from its `harness.json`."""
    from .config_harness import load_harness

    harness = root / "config" / item_id / "harness.json"
    if not harness.is_file():
        raise NotFound(f"{harness}: no config for {item_id}")
    return load_harness(harness, check_cross_file=False).role


def base_texts(root: Path, role: str) -> tuple[str, str | None]:
    """The (global, role-or-None) source texts a compile distributes."""
    Clauses = root / "config" / "CLAUDE.md"
    if not Clauses.is_file():
        raise NotFound(f"{Clauses}: no global invariants to compile")
    role_path = root / "personas" / role / "AGENTS.md"
    role_text = None
    if role_path.is_file():
        role_text = role_path.read_text().split(HEADER, 1)[0].strip("\n")
    return Clauses.read_text().strip("\n"), role_text


def _region(begin: str, end: str, body: str) -> str:
    return f"{begin}\n{body.strip(chr(10))}\n{end}"


def render(global_text: str, role: str | None, role_text: str | None,
           upper: str, learnings_text: str | None = None) -> str:
    """Rebuild the above-header text: fresh fenced regions, per-id text kept.

    Existing fenced regions are replaced; a region the file never had (the
    `learnings` region on an instance compiled before it existed) is added
    after the last one it does have; on a file that has none (created by hand
    before the compiler existed) the regions are prepended and no other line
    is touched. The below-header memory never reaches this function.
    """
    regions: dict[str, str] = {"global": _region(GLOBAL_BEGIN, GLOBAL_END, global_text)}
    if role is not None and role_text is not None:
        regions["role"] = _region(ROLE_BEGIN_TMPL.format(role=role), ROLE_END, role_text)
    if learnings_text:
        regions["learnings"] = _region(LEARNINGS_BEGIN, LEARNINGS_END, learnings_text)
    ordered = [regions[kind] for kind in KINDS if kind in regions]
    compiled = "\n\n".join(ordered) + "\n"

    def replace(match: re.Match) -> str:
        # A region the sources no longer call for is removed, not kept stale.
        return regions.get(match.group("kind"), "")

    if REGION_RE.search(upper):
        present = {match.group("kind") for match in REGION_RE.finditer(upper)}
        out = REGION_RE.sub(replace, upper)
        missing = [regions[kind] for kind in KINDS if kind in regions and kind not in present]
        if missing:
            last = max(REGION_RE.finditer(out), key=lambda match: match.end())
            out = out[:last.end()] + "\n\n" + "\n\n".join(missing) + out[last.end():]
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip("\n") + "\n"
    if not upper.strip():
        return compiled
    return compiled + "\n" + upper.strip("\n") + "\n"


def compile_agent(root: Path, item_id: str) -> dict:
    """Compile base rules into `config/<id>/AGENTS.md`. Returns a small report."""
    if item_id == PARTNER:
        return {"id": item_id, "compiled": False, "reason": "partner-exempt"}
    agents = root / "config" / item_id / "AGENTS.md"
    if not agents.is_file():
        raise NotFound(f"{agents}: no AGENTS.md to compile into")
    role = role_of(root, item_id)
    global_text, role_text = base_texts(root, role)
    text = agents.read_text()
    if HEADER not in text:
        raise HxError(
            f"{agents}: no `{HEADER}` line; the compiler cannot tell base from memory"
        )
    upper, _, lower = text.partition(HEADER)
    values = identity_values(root, item_id)
    # A copied canonical persona is generated boilerplate, not per-id policy.
    # Remove only an exact known copy outside the generated regions. Preserve
    # every other operator-authored byte; never fuzzy-match policy away.
    previous_roles = [match["body"].strip("\n") for match in REGION_RE.finditer(upper) if match["kind"] == "role"]
    custom = REGION_RE.sub("", upper)
    if role_text:
        custom = strip_known_blocks(custom, [role_text, expand_identity(role_text, values), *previous_roles])
    from .learn import persona_region
    new_upper = render(expand_identity(global_text, values), role,
                       expand_identity(role_text, values) if role_text else None,
                       expand_identity(custom, values), persona_region(root))
    new_text = new_upper + HEADER + lower if lower else new_upper + HEADER + "\n"
    if new_text != text:
        agents.write_text(new_text)
        return {"id": item_id, "compiled": True, "role": role,
                "role_region": role_text is not None}
    return {"id": item_id, "compiled": False, "reason": "up-to-date",
            "role": role, "role_region": role_text is not None}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx compile", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--planned", action="store_true", help="build versioned prompts for the planned runtime without activating them")
    parser.add_argument("--audience", choices=("worker", "partner", "companion", "subagent"))
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.planned:
        from .prompt_compiler import build
        from .continuity_store import canonical
        print(canonical(build(root, args.id, audience=args.audience)))
        return 0
    if args.audience:
        raise ValidationError("--audience requires --planned")

    report = compile_agent(root, args.id)
    if report["compiled"]:
        print(f"HX-COMPILE {args.id} compiled role={report['role']}")
    else:
        print(f"HX-COMPILE {args.id} kept {report['reason']}")
    return 0
