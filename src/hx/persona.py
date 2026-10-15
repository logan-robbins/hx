"""Current persona, identity and rules delivered at native context boundaries."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .errors import NotFound, ValidationError
from .ids import ID_RE, PARTNER


COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
VARIABLE_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z_0-9]*)\}\}")

#: What the `context` hook prints is bounded, in UTF-8 bytes (never fewer than characters):
#: 32 KiB wherever hx knows no tighter limit. Claude Code caps SessionStart output at 10,000
#: characters; past that it saves the text to a file and the model gets a 2,000-character
#: preview and the path, and is not told to read it (code.claude.com/docs/en/hooks, "JSON
#: output", checked 2026-10-13). So on Claude the bound is 10,000.
HOOK_BYTES = 32 * 1024
HOOK_BYTES_BY_FLAVOR = {"claude": 10_000}

#: The pointer line of an agent with no task in progress.

#: Opens the list of files the bound kept out of the hook's output.
AS_FILES = ("{what} not printed here: with {it} this text would pass hx's {bound}-byte hook bound. "
            "Use the Read tool once on each file below before anything else; do not cat them and "
            "do not read them twice.")

def expand_identity(text: str, values: dict[str, str]) -> str:
    """Resolve known identity variables once; never ship an invented identity."""
    unknown = set(VARIABLE_RE.findall(text)) - values.keys()
    if unknown:
        raise ValidationError("unresolved prompt identity variables: " + ", ".join(sorted(unknown)))
    return VARIABLE_RE.sub(lambda match: values[match[1]], text)


def identity_values(root: Path, item_id: str) -> dict[str, str]:
    """`id`, `pod`, `role`, `flavor` and `workdir` from `config/<id>/harness.json`."""
    from .config_harness import load_harness, resolve_workdir

    root = Path(root)
    config = load_harness(root / "config" / item_id / "harness.json", check_cross_file=False)
    return {"id": config.id, "pod": config.pod, "role": config.role, "flavor": config.flavor,
            "workdir": str(resolve_workdir(config.workdir, root) if config.workdir else root.resolve())}

def _harness(root: Path, item_id: str) -> dict:
    path = root / "config" / item_id / "harness.json"
    if not path.is_file():
        raise NotFound(f"{path}: no config for {item_id}; every id has a config/<id>/ (spec 03)")
    return json.loads(path.read_text())


def role_source(root: Path, role: str) -> Path:
    """A role's persona, `personas/<role>.md` (the Partner's is `personas/partner.md`)."""
    return Path(root) / "personas" / f"{role}.md"


def role_text(root: Path, role: str) -> str | None:
    """The role persona, or None when the role has none."""
    path = role_source(root, role)
    if not path.is_file():
        return None
    return path.read_text().strip("\n") or None


def identity_line(root: Path, item_id: str) -> str:
    """`You are <id> in pod <pod>. <purpose>`: the one line that makes an agent this agent and
    not the next of its role. No purpose yet (absent, or the template's placeholder): that part
    is left out. `Your explorer runs on <model> at <effort>.` closes it when harness.json has an
    `explorer` block: the persona's first rule spawns that subagent with exactly these."""
    from .config_harness import purpose_text

    data = _harness(Path(root), item_id)
    purpose = purpose_text(data.get("purpose"))
    line = f"You are {item_id} in pod {data.get('pod') or item_id}."
    if purpose:
        line = f"{line} {' '.join(purpose.split())}"
    explorer = data.get("explorer")
    if isinstance(explorer, dict) and explorer.get("model") and explorer.get("effort"):
        line = f"{line} Your explorer runs on {explorer['model']} at {explorer['effort']}."
    return line


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


def hook_bound(flavor: str) -> int:
    """The most the `context` hook prints for an agent of this flavor, in bytes."""
    return HOOK_BYTES_BY_FLAVOR.get(flavor, HOOK_BYTES)


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _joined(parts: list[tuple[str, Path | None, str]]) -> str:
    return "\n\n".join(text for _, _, text in parts if text.strip()) + "\n"


def _as_files(what: str, it: str, bound: int, files: list[Path]) -> tuple[str, None, str]:
    lines = [AS_FILES.format(what=what, it=it, bound=bound)]
    lines += [f"- {path.resolve()}" for path in files]
    return ("as-files", None, "\n".join(lines))


def boundary(root: Path, item_id: str, *, env=None) -> dict:
    """What the `context` hook prints for `item_id` at a boundary, from the sources, in order:
    the invariants and the persona where the hook is their channel, the identity line, the
    rules, and the pointer to the file the boundary reads. Writes nothing.

    Returns `{"id", "flavor", "role", "text", "bytes", "bound", "parts", "as_files", "file"}`:
    `parts` is `(name, source, bytes)` per part as printed, `as_files` the sources the bound
    turned into a list of files to read (empty when everything fit), `file` the boundary file
    or None."""
    from . import learn
    from .compose import status_file
    from .config_harness import invariants_channel, persona_channel
    from .hook_context import CONTEXT_LINE

    root = Path(root)
    values = identity_values(root, item_id)
    flavor, role = values["flavor"], values["role"]
    persona_file = role_source(root, role)
    persona = role_text(root, role)
    if persona is None and item_id == PARTNER:
        raise NotFound(f"{persona_file}: the Partner's persona is missing; `hx install` ships it")

    head: list[tuple[str, Path | None, str]] = []
    if invariants_channel(flavor) == "hook":
        clauses = root / "config" / "CLAUDE.md"
        text = clauses.read_text().strip("\n") if clauses.is_file() else ""
        if text.strip():
            head.append(("invariants", clauses, text))
    if persona_channel(flavor) == "hook" and persona:
        head.append(("persona", persona_file, persona))
    head.append(("identity", root / "config" / item_id / "harness.json", identity_line(root, item_id)))
    rules = [("rules", path, path.read_text().strip("\n")) for path in learn.rule_files(root, env, role)]
    work = status_file(root, item_id)
    if work is not None:
        tail = [("pointer", work, CONTEXT_LINE.format(path=work.resolve()))]
    else:
        tail = []

    bound = hook_bound(flavor)
    as_files: list[Path] = []
    parts = head + rules + tail
    if len(_joined(parts).encode()) > bound and rules:
        as_files = [path for _, path, _ in rules]
        parts = head + [_as_files("Your learnings are", "them", bound, as_files)] + tail
    if len(_joined(parts).encode()) > bound and any(name == "persona" for name, _, _ in head):
        as_files = [persona_file, *as_files]
        head = [part for part in head if part[0] != "persona"]
        parts = head + [_as_files("Your persona and learnings are" if rules else "Your persona is",
                                  "them" if rules else "it", bound, as_files)] + tail
    text = _joined(parts)
    return {
        "id": item_id, "flavor": flavor, "role": role, "text": text,
        "bytes": len(text.encode()), "bound": bound,
        "parts": [(name, _relative(root, path) if path else None, len(body.encode()))
                  for name, path, body in parts],
        "as_files": [_relative(root, path) for path in as_files],
        "file": work,
    }


# --- CLI ----------------------------------------------------------------------------------------


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hx persona",
        description="Print what the `context` hook hands ID at every boundary: the invariants and "
                    "the persona where the hook is their channel, the identity line, the rules, and "
                    "the pointer to the current checkpoint.",
        epilog="example: hx persona eng-001",
    )
    parser.add_argument("id")
    parser.add_argument("--summary", action="store_true",
                        help="one line: bytes, the bound, and how many sources went as files")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = boundary(Path(root), args.id, env=env)
    if args.summary:
        print(f"HX-PERSONA {args.id} bytes={result['bytes']} bound={result['bound']}"
              + (f" as-files={len(result['as_files'])}" if result["as_files"] else ""))
    else:
        sys.stdout.write(result["text"])
    return 0
