"""`hx persona <id>` — the system prompt an agent launches with, rendered from its sources.

An agent's prompt is four layers, and each layer has one source (docs/prompt-runtime.md
§ Prompt layers): the invariants (`config/CLAUDE.md`, delivered through the one channel
`config_harness.INVARIANTS_CHANNEL` names), the persona, the learnings, and session memory (the
work item's `## Tasks`, step state and episodes, which `hx compose` puts in the context file).
This module renders the persona into `run/<id>/persona.md`. Every adapter's `start.sh --exec`
runs it (`python -m hx.persona`) immediately before it execs, so an edit to any source lands at
the next launch or restart, and nothing is copied per id:

  personas/<role>/AGENTS.md   the role persona, shared by every worker of the role
                              (the Partner's is `config/partner/AGENTS.md`)
  harness.json `purpose`      the one per-id paragraph: what this agent is for, placed after
                              the persona's opening paragraph
  config/CLAUDE.md            only where the persona is the invariants' channel (Pi, Grok),
                              as the `hx:global` region, first
  LEARNINGS/                  `hx.learn.persona_region`, last: global rules, then the role's

A role without `personas/<role>/AGENTS.md` (a test or bespoke role) gets one identity line in
its place.

What a worker's subagents are told is shared too: `templates/worker/SUBAGENTS.md`
(`templates/contributor/SUBAGENTS.md` for a Meta worker), rendered with the worker's identity
by `render_subagents` when the `SubagentStart` hook composes the subagent's context file. A
`config/<id>/SUBAGENTS.md` (the Partner's, or one written for one id on purpose) is used as is.

`migrate` moves an instance off the per-id `config/<id>/AGENTS.md` it used before: the per-id
paragraph becomes `purpose` while that is still the placeholder, anything else the file held
that no source already says (the agent's memory below `## UPDATES BELOW ONLY`, promoted
directives, a per-id paragraph `purpose` already replaced) becomes one pending text proposal the
Partner reviews with `hx learn pending`, and the file is removed. The Partner keeps its file —
it is its persona — and loses only the old header and whatever was below it, which becomes a
proposal the same way. `hx install`, `hx up`, `hx launch` and `hx restart` run it; once nothing
is left to move it does nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import store
from .compile import (
    GLOBAL_BEGIN,
    GLOBAL_END,
    LEARNINGS_BEGIN,
    LEARNINGS_END,
    REGION_RE,
    expand_identity,
    identity_values,
)
from .errors import NotFound, ValidationError
from .ids import ID_RE, PARTNER

#: The line that split persona from memory before the learnings were the memory channel.
#: Nothing writes it. Only `migrate` and the source readers (here and `prompt_compiler`) still
#: look for it, to cut a file an older hx installed.
LEGACY_HEADER = "## UPDATES BELOW ONLY"

#: In place of a role persona that does not exist.
FALLBACK = "You are `{id}`, a `{role}` agent in pod `{pod}` of this hx instance."

DISTILLED_RE = re.compile(
    r"(?:^## Distilled directives\s*\n\n)?<!-- hx:distilled begin .*?-->\n(?P<body>.*?)\n<!-- hx:distilled end -->",
    re.S | re.M,
)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def persona_path(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / "persona.md"


def _above_header(text: str) -> str:
    return text.split(LEGACY_HEADER, 1)[0]


def _harness(root: Path, item_id: str) -> dict:
    path = root / "config" / item_id / "harness.json"
    if not path.is_file():
        raise NotFound(f"{path}: no config for {item_id}; every id has a config/<id>/ (spec 03)")
    return json.loads(path.read_text())


def role_source(root: Path, role: str) -> Path:
    return root / "personas" / role / "AGENTS.md"


def role_text(root: Path, role: str) -> str | None:
    """The role persona, or None when the role has none."""
    path = role_source(root, role)
    if not path.is_file():
        return None
    return _above_header(path.read_text()).strip("\n") or None


def partner_text(root: Path) -> str:
    """`config/partner/AGENTS.md`, without anything an earlier hx stamped into it."""
    path = root / "config" / PARTNER / "AGENTS.md"
    if not path.is_file():
        raise NotFound(f"{path}: the Partner's persona is missing; `hx install` ships it")
    kept = REGION_RE.sub("", _above_header(path.read_text()))
    return re.sub(r"\n{3,}", "\n\n", kept).strip("\n")


def subagents_template(root: Path, flavor: str) -> Path | None:
    """The shared SUBAGENTS.md for a flavor: the instance's, else the one hx ships."""
    from .install import skeleton_dir

    kind = "contributor" if flavor == "meta" else "worker"
    for base in (root / "templates", skeleton_dir() / "templates"):
        path = base / kind / "SUBAGENTS.md"
        if path.is_file():
            return path
    return None


def render_subagents(root: Path, item_id: str) -> tuple[str, str]:
    """`(source, text)`: what `item_id`'s subagents are told. Its own `config/<id>/SUBAGENTS.md`
    when it has one, else the shared template with its identity filled in and the template's
    comments dropped. `("", "")` when there is neither."""
    root = Path(root)
    own = root / "config" / item_id / "SUBAGENTS.md"
    if own.is_file():
        return f"config/{item_id}/SUBAGENTS.md", own.read_text().strip("\n")
    values = identity_values(root, item_id)
    template = subagents_template(root, values["flavor"])
    if template is None:
        return "", ""
    text = expand_identity(COMMENT_RE.sub("", template.read_text()), values)
    label = f"templates/{template.parent.name}/SUBAGENTS.md"
    return label, re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def with_purpose(persona: str, purpose: str | None) -> str:
    """The purpose goes after the persona's opening — its title and identity paragraph —
    before the first `## ` section, so the reader learns who it is and what it is for first."""
    if not purpose:
        return persona
    match = re.search(r"^## ", persona, re.M)
    if match is None:
        return persona.rstrip("\n") + "\n\n" + purpose.strip("\n")
    head = persona[:match.start()].rstrip("\n")
    return f"{head}\n\n{purpose.strip(chr(10))}\n\n{persona[match.start():]}".lstrip("\n")


def _region(begin: str, end: str, body: str) -> str:
    return f"{begin}\n{body.strip(chr(10))}\n{end}"


def render(root: Path, item_id: str, *, env=None) -> str:
    """The persona as the agent receives it. Writes nothing."""
    from .config_harness import invariants_channel, purpose_text
    from .learn import persona_region

    root = Path(root)
    data = _harness(root, item_id)
    values = identity_values(root, item_id)
    if item_id == PARTNER:
        body = partner_text(root)
    else:
        source = role_text(root, values["role"])
        persona = expand_identity(source, values) if source else FALLBACK.format(**values)
        body = with_purpose(persona, purpose_text(data.get("purpose")))
    parts = []
    if invariants_channel(values["flavor"]) == "persona":
        clauses = root / "config" / "CLAUDE.md"
        if clauses.is_file():
            parts.append(_region(GLOBAL_BEGIN, GLOBAL_END,
                                 expand_identity(clauses.read_text().strip("\n"), values)))
    parts.append(body)
    # Global rules, then the rules scoped to this agent's role (`hx learn propose --role`).
    parts.append(_region(LEARNINGS_BEGIN, LEARNINGS_END,
                         persona_region(root, env, role=values["role"])))
    return "\n\n".join(part for part in parts if part.strip()) + "\n"


def write(root: Path, item_id: str, *, env=None) -> dict:
    """Render `run/<id>/persona.md`; rewritten only when it changed."""
    root = Path(root)
    text = render(root, item_id, env=env)
    path = persona_path(root, item_id)
    changed = not path.is_file() or path.read_text() != text
    if changed:
        store.atomic_write_text(path, text)
    return {"id": item_id, "path": str(path.relative_to(root)), "bytes": len(text.encode()),
            "written": changed, "text": text}


# --- migration from config/<id>/AGENTS.md -------------------------------------------------------


def _paragraphs(text: str) -> list[str]:
    return [block.strip("\n") for block in re.split(r"\n[ \t]*\n", text) if block.strip()]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _known(root: Path, values: dict[str, str]) -> tuple[set[str], set[str]]:
    """Paragraphs some shared source already says, above and below the old header: the role
    persona and the worker templates, as shipped and as this instance has them, raw and with
    the identity filled in. A per-id file copied from them repeats these; they are not per-id."""
    from .install import skeleton_dir

    sources = [role_source(root, values["role"]), skeleton_dir() / "personas" / values["role"] / "AGENTS.md"]
    for base in (root / "templates", skeleton_dir() / "templates"):
        sources += [base / "worker" / "AGENTS.md", base / "contributor" / "AGENTS.md"]
    upper: set[str] = set()
    lower: set[str] = set()
    for path in sources:
        if not path.is_file():
            continue
        text = path.read_text()
        filled = text
        for key, value in values.items():
            filled = filled.replace("{{" + key + "}}", value)
        for variant in (text, filled):
            above, _, below = variant.partition(LEGACY_HEADER)
            upper |= {_norm(block) for block in _paragraphs(COMMENT_RE.sub("", above))}
            lower |= {_norm(block) for block in _paragraphs(COMMENT_RE.sub("", below))}
    return upper, lower


_EMPTY_MEMORY = re.compile(r"^[-*]?\s*\(?(?:things i learned:\s*)?nothing yet\.?\)?$", re.I)


def _unsaid(text: str, known: set[str]) -> str:
    kept = [block for block in _paragraphs(COMMENT_RE.sub("", text))
            if _norm(block) not in known and not _EMPTY_MEMORY.match(_norm(block))]
    return "\n\n".join(kept)


def migrate(root: Path, item_id: str, *, env=None) -> dict | None:
    """Move a worker off what an older hx copied per id: `config/<id>/AGENTS.md` into the
    sources that replace it (then removed), and a `config/<id>/SUBAGENTS.md` that is a copy of
    the shared template (removed).

    Returns None when there is nothing to migrate. The Partner keeps its persona file and loses
    only the old header (`_migrate_partner`).
    """
    root = Path(root)
    if item_id == PARTNER:
        return _migrate_partner(root, env=env)
    result = _migrate_agents(root, item_id, env=env)
    if _migrate_subagents(root, item_id):
        result = result or {"id": item_id, "purpose_moved": False, "proposal": None}
        result["subagents_removed"] = True
    return result


def _migrate_subagents(root: Path, item_id: str) -> bool:
    """Remove a `config/<id>/SUBAGENTS.md` that is a copy of a shared template (shipped or
    installed, worker or contributor, raw or identity-filled; comments ignored). One that says
    something else was written for this id on purpose: it stays, and is used as is."""
    from .install import skeleton_dir

    path = root / "config" / item_id / "SUBAGENTS.md"
    if not path.is_file():
        return False
    values = identity_values(root, item_id)
    mine = _norm(COMMENT_RE.sub("", path.read_text()))
    for base in (root / "templates", skeleton_dir() / "templates"):
        for kind in ("worker", "contributor"):
            template = base / kind / "SUBAGENTS.md"
            if not template.is_file():
                continue
            text = COMMENT_RE.sub("", template.read_text())
            filled = text
            for key, value in values.items():
                filled = filled.replace("{{" + key + "}}", value)
            if mine in (_norm(text), _norm(filled)):
                path.unlink()
                return True
    return False


def _migrate_agents(root: Path, item_id: str, *, env=None) -> dict | None:
    """The per-id `config/<id>/AGENTS.md` half of `migrate`."""
    from . import learn
    from .config_harness import purpose_text

    agents = root / "config" / item_id / "AGENTS.md"
    if not agents.is_file():
        return None
    text = agents.read_text()
    upper, found, lower = text.partition(LEGACY_HEADER)
    if not found:
        upper, lower = text, ""
    values = identity_values(root, item_id)
    known_upper, known_lower = _known(root, values)

    leftover: list[tuple[str, str]] = []
    distilled = [match["body"].strip("\n") for match in DISTILLED_RE.finditer(upper)]
    if any(distilled):
        leftover.append(("Distilled directives", "\n\n".join(block for block in distilled if block)))
    upper = DISTILLED_RE.sub("", REGION_RE.sub("", upper))
    per_id = _unsaid(upper, known_upper)

    harness_path = root / "config" / item_id / "harness.json"
    data = json.loads(harness_path.read_text())
    moved = False
    if per_id:
        current = purpose_text(data.get("purpose"))
        if current is None:
            data["purpose"] = per_id
            store.atomic_write_text(harness_path, json.dumps(data, indent=2) + "\n")
            moved = True
        elif _norm(current) != _norm(per_id):
            leftover.append(("Per-id text (harness.json already had a purpose)", per_id))
    memory = _unsaid(lower, known_lower)
    if memory:
        leftover.append(("Memory below the old header", memory))

    proposal = None
    if leftover:
        body = "\n\n".join(f"### {title}\n\n{block}" for title, block in leftover)
        # Scoped to the agent's role: what one worker learned is owed to every worker of
        # its role, and the reviewer widens it with `--global` when it is everyone's.
        proposal = learn.propose_text(
            root, text=body, author=item_id, role=values["role"],
            source=f"migrated from config/{item_id}/AGENTS.md", env=env,
        )["id"]
    agents.unlink()
    return {"id": item_id, "agents_removed": True, "purpose_moved": moved, "proposal": proposal}


def _migrate_partner(root: Path, *, env=None) -> dict | None:
    """`config/partner/AGENTS.md` written by an older hx: the persona above the old header
    stays (without stamped regions), and anything below it becomes one text proposal scoped
    to the Partner. The Partner's working memory is `PARTNER.md`; this file is persona only."""
    from . import learn

    path = root / "config" / PARTNER / "AGENTS.md"
    if not path.is_file():
        return None
    text = path.read_text()
    upper, found, lower = text.partition(LEGACY_HEADER)
    if not found:
        return None
    values = identity_values(root, PARTNER)
    _, known_lower = _known(root, values)
    memory = _unsaid(lower, known_lower)
    proposal = None
    if memory:
        proposal = learn.propose_text(
            root, text=f"### Memory below the old header\n\n{memory}", author=PARTNER,
            role=values["role"], source=f"migrated from config/{PARTNER}/AGENTS.md", env=env,
        )["id"]
    kept = re.sub(r"\n{3,}", "\n\n", REGION_RE.sub("", upper)).strip("\n") + "\n"
    store.atomic_write_text(path, kept)
    return {"id": PARTNER, "purpose_moved": False, "proposal": proposal, "kept": True}


def migrate_all(root: Path, *, env=None) -> list[dict]:
    """`migrate` for every `config/<id>/`; only the ids that had something to move."""
    directory = Path(root) / "config"
    if not directory.is_dir():
        return []
    results = []
    for entry in sorted(directory.iterdir()):
        if entry.is_dir() and ID_RE.match(entry.name) and (entry / "harness.json").is_file():
            try:
                result = migrate(root, entry.name, env=env)
            except (ValidationError, json.JSONDecodeError):
                # A config that does not validate is `hx doctor`'s to report, and its own
                # `hx launch` migrates it once it does.
                continue
            if result is not None:
                results.append(result)
    return results


def describe_migration(result: dict) -> str:
    parts = []
    if result.get("kept"):
        parts.append(f"config/{result['id']}/AGENTS.md lost its `{LEGACY_HEADER}` header")
    if result.get("agents_removed"):
        parts.append(f"config/{result['id']}/AGENTS.md removed")
    if result.get("purpose_moved"):
        parts.append("its per-id paragraph is now harness.json `purpose`")
    if result.get("proposal"):
        parts.append(f"the rest is learnings proposal {result['proposal']} (hx learn pending)")
    if result.get("subagents_removed"):
        parts.append(f"config/{result['id']}/SUBAGENTS.md removed (a copy of the shared template, "
                     "which is rendered at delivery)")
    return "; ".join(parts)


# --- CLI ----------------------------------------------------------------------------------------


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hx persona",
        description="Render run/ID/persona.md from the role persona, harness.json `purpose` and "
                    "the learnings; every start.sh runs this before it execs.",
        epilog="example: hx persona eng-001 --print",
    )
    parser.add_argument("id")
    parser.add_argument("--print", action="store_true", dest="show",
                        help="print the rendered persona instead of the summary line")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = write(Path(root), args.id, env=env)
    if args.show:
        sys.stdout.write(result["text"])
    else:
        print(f"HX-PERSONA {args.id} {result['path']} bytes={result['bytes']}"
              + ("" if result["written"] else " unchanged"))
    return 0


if __name__ == "__main__":  # `python -m hx.persona --root ROOT ID`, what start.sh runs
    import os

    from .cli import _root_for
    from .errors import HxError

    try:
        raise SystemExit(main(sys.argv[1:], _root_for(sys.argv[1:], os.environ), env=os.environ))
    except HxError as exc:
        print(f"hx: persona: {exc.message}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
