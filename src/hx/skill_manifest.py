"""The skill manifest: which packaged skill each agent home gets, and the lint behind it.

Visibility is budget (TOOL_SKILL_PLAN §Inventory): every skill an agent carries costs its
description in every listing, so each one is installed for exactly the audience and roles
that need it. `src/hx/skills/manifest.json` is the one place that says so —

    {"hx-worker": {"audiences": ["worker"], "roles": ["*"], "optional": false}, ...}

— and every adapter's `install.sh` asks `python -m hx.skill_manifest select` for its list
instead of naming skills itself. `optional` skills install only when the instance's
`config/hx.json` names them in `skills`; `"planned": false` keeps a skill out of a planned
(ledger) run, which has no episodic memory.

The same module validates what ships, for `hx doctor`'s `skills` row: the Agent Skills
frontmatter rules (agentskills.io/specification: `name` 1-64 lowercase letters, digits and
single hyphens, equal to the directory; `description` 1-1,024 characters; portable fields
only), a 32 KiB bound per file, and a lint that no skill a worker carries names a command
the worker would be refused (`hx.cli.AUDIENCES`). A skill that is also the Partner's may keep
its Partner steps under a heading that starts with `Partner only`; the lint skips that
section (to the next heading of the same or a higher level) and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .errors import ValidationError

MANIFEST = "manifest.json"

#: The audiences of TOOL_SKILL_PLAN §Inventory a skill can be written for.
AUDIENCE_NAMES = ("operator", "partner", "worker", "subagent", "companion")
#: Audiences that run as a worker for the refusal lint: what they carry must not name a
#: command the worker is refused.
WORKER_AUDIENCES = ("worker", "subagent")

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
#: The six portable fields of the open standard; Claude, Codex and Pi extensions are not
#: used, so one SKILL.md serves every adapter.
PORTABLE_FIELDS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")
MAX_SKILL_BYTES = 32 * 1024
#: `hx tools load skill:NAME` carries a skill to an advisory adapter only up to this size
#: (`capability_catalog.load`).
FACADE_BYTES = 8 * 1024

_ROW_KEYS = {"audiences", "roles", "optional", "planned"}


def package_dir() -> Path:
    """The package's `skills/`, which the adapters copy from."""
    return Path(__file__).resolve().parent / "skills"


def skills_dir(env=None) -> Path:
    env = os.environ if env is None else env
    value = env.get("HX_SKILLS_DIR")
    return Path(value) if value else package_dir()


def load(directory: Path) -> dict[str, dict]:
    """Read and validate `manifest.json`. Every message names the file and the rule."""
    path = Path(directory) / MANIFEST
    if not path.is_file():
        raise ValidationError(f"{path}: missing; every skill directory ships a manifest (CONTRACTS.md)")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise ValidationError(f"{path}: must map each skill name to its audiences, roles and optional flag")
    manifest: dict[str, dict] = {}
    for name, row in data.items():
        if not isinstance(name, str) or not NAME_RE.match(name) or len(name) > NAME_MAX:
            raise ValidationError(f"{path}: `{name}` is not a skill name (lowercase, digits, single hyphens)")
        if not isinstance(row, dict) or not {"audiences", "roles", "optional"} <= set(row):
            raise ValidationError(f"{path}: `{name}` needs audiences, roles and optional")
        unknown = sorted(set(row) - _ROW_KEYS)
        if unknown:
            raise ValidationError(f"{path}: `{name}` has unknown key(s) {', '.join(unknown)}")
        audiences, roles = row["audiences"], row["roles"]
        if (not isinstance(audiences, list) or not audiences
                or any(a not in AUDIENCE_NAMES for a in audiences)):
            raise ValidationError(f"{path}: `{name}` audiences must be a non-empty list of "
                                  f"{', '.join(AUDIENCE_NAMES)}")
        if not isinstance(roles, list) or any(not isinstance(r, str) or not r for r in roles):
            raise ValidationError(f"{path}: `{name}` roles must be a list of role names or \"*\"")
        if not isinstance(row["optional"], bool) or not isinstance(row.get("planned", True), bool):
            raise ValidationError(f"{path}: `{name}` optional and planned must be true or false")
        manifest[name] = {"audiences": list(audiences), "roles": list(roles),
                          "optional": row["optional"], "planned": row.get("planned", True)}
    return manifest


def enabled(root: Path) -> list[str]:
    """The optional skills this instance turns on: `config/hx.json` `skills` (CONTRACTS.md)."""
    path = Path(root) / "config" / "hx.json"
    if not path.is_file():
        return []
    try:
        recorded = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: not valid JSON: {exc}") from exc
    value = recorded.get("skills") if isinstance(recorded, dict) else None
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValidationError(f"{path}: `skills` must be a list of optional skill names")
    return list(value)


def select(manifest: dict[str, dict], *, audience: str, role: str | None,
           enabled: list[str] | tuple[str, ...] = (), planned: bool = False) -> list[str]:
    """The skills one home receives, in manifest order."""
    chosen = []
    for name, row in manifest.items():
        if audience not in row["audiences"]:
            continue
        if "*" not in row["roles"] and (role or "") not in row["roles"]:
            continue
        if row["optional"] and name not in enabled:
            continue
        if planned and not row["planned"]:
            continue
        chosen.append(name)
    return chosen


def role_of(root: Path, item_id: str) -> str:
    """`config/<id>/harness.json` `role`, or `""` when it cannot be read."""
    try:
        data = json.loads((Path(root) / "config" / item_id / "harness.json").read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("role") or "") if isinstance(data, dict) else ""


def for_agent(directory: Path, root: Path, item_id: str, *, companion: bool = False,
              env=None) -> list[str]:
    """What `install.sh` installs into `<id>`'s home (or its Companion's home)."""
    env = os.environ if env is None else env
    audience = "companion" if companion else ("partner" if item_id == "partner" else "worker")
    return select(load(directory), audience=audience, role=role_of(root, item_id),
                  enabled=enabled(root), planned=bool(env.get("HX_CONTINUITY_RUN")))


# --- validation (hx doctor `skills`) -------------------------------------------------------------


def parse_frontmatter(text: str, path: str | Path) -> tuple[dict[str, object], str]:
    """The YAML subset a SKILL.md uses: `key: value` lines and one level of `metadata`.

    Quoted scalars are unquoted; a `metadata:` line opens a string map of indented
    `key: value` lines. Anything else is rejected by name, never guessed at.
    """
    path = str(path)
    if not text.startswith("---\n"):
        raise ValidationError(f"{path}: frontmatter must open with `---` on the first line")
    end = text.find("\n---\n", 3)
    if end < 0:
        raise ValidationError(f"{path}: frontmatter opened with `---` but never closed")
    fields: dict[str, object] = {}
    current_map: str | None = None
    for number, line in enumerate(text[4:end].split("\n"), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[:1] == " "
        match = re.match(r"^\s*([A-Za-z0-9_][\w-]*):(?:\s+(.*))?$", line)
        if not match:
            raise ValidationError(f"{path}: frontmatter line {number} is not `key: value`")
        key, value = match.group(1), (match.group(2) or "").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif value and (": " in value or " #" in value or value[0] in "*&!|>%@`[{,"):
            # Every adapter parses this as YAML: `a: b` inside a value, a comment marker or a
            # leading indicator is not the plain string it looks like.
            raise ValidationError(f"{path}: frontmatter line {number}: `{key}` is not a plain YAML "
                                  "scalar; quote the value")
        if indented:
            if current_map is None:
                raise ValidationError(f"{path}: frontmatter line {number}: indented `{key}` outside a map")
            mapping = fields[current_map]
            assert isinstance(mapping, dict)
            mapping[key] = value
            continue
        if key in fields:
            raise ValidationError(f"{path}: frontmatter line {number}: duplicate `{key}`")
        if value == "":
            fields[key] = {}
            current_map = key
        else:
            fields[key] = value
            current_map = None
    return fields, text[end + 5:]


def validate_skill(directory: Path) -> list[str]:
    """Problems with one skill directory against the Agent Skills rules, or `[]`."""
    directory = Path(directory)
    path = directory / "SKILL.md"
    label = directory.name
    if not path.is_file():
        return [f"{label}: no SKILL.md"]
    size = path.stat().st_size
    if size > MAX_SKILL_BYTES:
        return [f"{label}: SKILL.md is {size} bytes, over the {MAX_SKILL_BYTES // 1024} KiB bound"]
    try:
        fields, body = parse_frontmatter(path.read_text(), path)
    except ValidationError as exc:
        return [f"{label}: {exc.message}"]
    problems = []
    name = fields.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= NAME_MAX or not NAME_RE.match(name):
        problems.append(f"{label}: name {name!r} must be 1-{NAME_MAX} lowercase letters, digits "
                        "and single hyphens, not starting or ending with one")
    elif name != label:
        problems.append(f"{label}: name {name!r} does not match its directory")
    description = fields.get("description")
    if not isinstance(description, str) or not 1 <= len(description) <= DESCRIPTION_MAX:
        length = len(description) if isinstance(description, str) else 0
        problems.append(f"{label}: description is {length} characters; 1-{DESCRIPTION_MAX} required")
    unknown = sorted(set(fields) - set(PORTABLE_FIELDS))
    if unknown:
        problems.append(f"{label}: non-portable field(s) {', '.join(unknown)}; only "
                        f"{', '.join(PORTABLE_FIELDS)} port to every adapter")
    compatibility = fields.get("compatibility")
    if isinstance(compatibility, str) and len(compatibility) > COMPATIBILITY_MAX:
        problems.append(f"{label}: compatibility is over {COMPATIBILITY_MAX} characters")
    if "metadata" in fields and not isinstance(fields["metadata"], dict):
        problems.append(f"{label}: metadata must be a map of strings")
    if not body.strip():
        problems.append(f"{label}: frontmatter but no instructions")
    return problems


#: `hx <command> [<sub>]` as written in a skill's prose or code blocks.
_MENTION_RE = re.compile(r"(?<![\w/.-])hx\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?")
#: A Markdown heading outside a code fence, and the title that marks a Partner-only section.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
PARTNER_ONLY = "partner only"


def _linted_lines(text: str, *, partner_sections: bool):
    """The lines the worker lint reads: all of them, or (for a skill that is also the
    Partner's) all but the sections headed `Partner only…`."""
    fenced, skip_level = False, None
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
        heading = None if fenced else _HEADING_RE.match(line)
        if heading and partner_sections:
            level = len(heading.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            title = heading.group(2).strip().strip("*_`").lower()
            if skip_level is None and title.startswith(PARTNER_ONLY):
                skip_level = level
        if skip_level is None:
            yield line


def refused_mentions(text: str, audiences: dict[str, set[str]], *,
                     partner_sections: bool = False) -> list[str]:
    """Every `hx …` the text names that a worker caller would be refused (sorted, unique).

    With `partner_sections` (the skill's audiences include the Partner), a section headed
    `Partner only…` is the Partner's and is not linted.
    """
    from .cli import resolve_capability, worker_may_run

    found = set()
    for line in _linted_lines(text, partner_sections=partner_sections):
        for match in _MENTION_RE.finditer(line):
            command, sub = match.group(1), match.group(2)
            rest = [sub] if sub else []
            # A flag-level entry (`memory search --all-roles`) counts when the flag sits on
            # the same line as the command it qualifies.
            rest += re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", line[match.end():])
            capability = resolve_capability(command, rest, audiences)
            if capability is not None and not worker_may_run(command, capability, audiences):
                found.add(capability)
    return sorted(found)


def check(directory: Path, root: Path | None = None) -> list[tuple[str, str]]:
    """(status, detail) rows for `hx doctor`: one `ok` row, or one `fail` row per problem."""
    from .cli import AUDIENCES

    directory = Path(directory)
    try:
        manifest = load(directory)
    except ValidationError as exc:
        return [("fail", exc.message)]
    problems: list[str] = []
    shipped = sorted(p.name for p in directory.iterdir() if p.is_dir() and not p.name.startswith("."))
    for name in shipped:
        if name not in manifest:
            problems.append(f"{name}: not in {MANIFEST}; every skill names its audience there")
    for name in manifest:
        if name not in shipped:
            problems.append(f"{name}: in {MANIFEST} but not shipped")
    over_facade = 0
    for name in shipped:
        problems.extend(validate_skill(directory / name))
        path = directory / name / "SKILL.md"
        if path.is_file() and path.stat().st_size > FACADE_BYTES:
            over_facade += 1
        row = manifest.get(name)
        if row and any(a in WORKER_AUDIENCES for a in row["audiences"]) and path.is_file():
            for capability in refused_mentions(path.read_text(), AUDIENCES,
                                               partner_sections="partner" in row["audiences"]):
                problems.append(f"{name}: a worker carries it, and it names `hx {capability}`, "
                                "which a worker is refused")
    turned_on: list[str] = []
    if root is not None:
        try:
            turned_on = enabled(root)
        except ValidationError as exc:
            problems.append(exc.message)
        for name in turned_on:
            if name not in manifest:
                problems.append(f"config/hx.json skills names `{name}`, which does not ship")
            elif not manifest[name]["optional"]:
                problems.append(f"config/hx.json skills names `{name}`, which is not optional "
                                "(it is installed by its audience already)")
    if problems:
        return [("fail", problem) for problem in problems]
    return [("ok", f"{len(shipped)} skill(s) valid (frontmatter, size, worker lint); "
                   f"{over_facade} over the {FACADE_BYTES // 1024} KiB facade bound; "
                   f"optional on: {', '.join(turned_on) or 'none'}")]


def main(argv: list[str] | None = None) -> int:
    """`python -m hx.skill_manifest select --skills DIR --root ROOT --id ID [--companion]`.

    Prints one skill name per line: what the adapters' `install.sh` copies into a home.
    """
    parser = argparse.ArgumentParser(prog="python -m hx.skill_manifest")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("select", help="the skills one agent home receives")
    p.add_argument("--skills", required=True, help="the package skills directory")
    p.add_argument("--root", required=True, help="HARNESS_ROOT")
    p.add_argument("--id", required=True, help="the agent id")
    p.add_argument("--companion", action="store_true", help="the Companion's home instead")
    args = parser.parse_args(argv)
    try:
        names = for_agent(Path(args.skills), Path(args.root), args.id, companion=args.companion)
    except ValidationError as exc:
        print(f"skill_manifest: {exc.message}", file=sys.stderr)
        return 2
    for name in names:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
