"""`hx persona <id>` — what the `context` hook hands an agent at every boundary.

An agent's prompt is four layers, and each layer has one source (docs/prompt-runtime.md
§ Prompt layers): the invariants (`config/CLAUDE.md`), the persona (`personas/<role>.md`), the
learnings (`LEARNINGS/<category>.md`, and `personas/<role>/<category>.md` for the rules scoped
to a role), and session memory (the work item: the agent's `## Tasks`, hx's `## Status`).
Nothing is copied per id and nothing is rendered into a file. The `context` hook
(`hx.hook_context`; SessionStart, matcher `startup|resume|clear|compact`) prints the chain at
every boundary, so an edit to any source lands at the next one. `boundary` builds it, and
`hx persona <id>` prints the same text:

  config/CLAUDE.md        only where the hook is the invariants' one channel (Pi, Grok, Meta;
                          `config_harness.INVARIANTS_CHANNEL`). Claude loads it as its home's
                          CLAUDE.md, Codex as part of `$CODEX_HOME/AGENTS.md`
  personas/<role>.md      whole, where the hook is the persona's channel (Claude, Meta;
                          `config_harness.PERSONA_CHANNEL`). Pi and Grok are launched with the
                          file itself as their system-prompt flag; Codex reads it in
                          `$CODEX_HOME/AGENTS.md`, which `start.sh` writes at every exec
  identity line           `You are <id> in pod <pod>. <purpose>`: harness.json's id, pod and
                          `purpose`, the one per-id paragraph, then `Your explorer runs on
                          <model> at <effort>.` when harness.json has an `explorer` block
                          (`identity_line`)
  rules                   every `LEARNINGS/<category>.md` that holds a rule, then every
                          `personas/<role>/<category>.md` that does, whole (`hx.learn.rule_files`)
  pointer                 the one Read of the boundary: the `-working` work item (`PARTNER.md`
                          for the Partner). An agent with no task is told its goal comes as a
                          `/goal` pointer

The output is bounded (`hook_bound`). Past the bound the rules are listed as files to read
once instead of printed, and past it even then the persona is too: a harness that caps hook
output hands the model a preview and a path, not the text.

A role without `personas/<role>.md` (a test or bespoke role) gets the identity line, the rules
and the pointer.

What a worker's subagents are told is shared too: `templates/worker/SUBAGENTS.md`
(`templates/contributor/SUBAGENTS.md` for a Meta worker), rendered with the worker's identity
by `render_subagents` when the `SubagentStart` hook composes the subagent's context file. A
`config/<id>/SUBAGENTS.md` (the Partner's, or one written for one id on purpose) is used as is.

`migrate_layout` moves an instance off the layout before it: `personas/<role>/AGENTS.md` and
`config/partner/AGENTS.md` become `personas/<role>.md` (`personas/partner.md`), and a rule an
older hx scoped with `Role:` in its line moves to `personas/<role>/<category>.md`
(`hx.learn.migrate_role_rules`). `migrate` moves it off the per-id `config/<id>/AGENTS.md`: the per-id
paragraph becomes `purpose` while that is still the placeholder, anything else the file held
that no source already says (the agent's memory below `## UPDATES BELOW ONLY`, promoted
directives, a per-id paragraph `purpose` already replaced) becomes one pending text proposal the
Partner reviews with `hx learn pending`, and the file is removed. `hx install`, `hx up`,
`hx launch` and `hx restart` run both; once nothing is left to move they do nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from . import store
from .errors import NotFound, ValidationError
from .ids import ID_RE, PARTNER

#: A fenced region an earlier hx stamped into a persona or a per-id file (`hx:global`,
#: `hx:role`, `hx:learnings`). Nothing writes one now; the migration strips them.
REGION_RE = re.compile(
    r"<!-- hx:(?P<kind>global|role|learnings) begin .*?-->\n"
    r"(?P<body>.*?)"
    r"<!-- hx:(?P=kind) end -->",
    re.DOTALL,
)

VARIABLE_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z_0-9]*)\}\}")

#: What the `context` hook prints is bounded, in UTF-8 bytes (never fewer than characters):
#: 32 KiB wherever hx knows no tighter limit. Claude Code caps SessionStart output at 10,000
#: characters; past that it saves the text to a file and the model gets a 2,000-character
#: preview and the path, and is not told to read it (code.claude.com/docs/en/hooks, "JSON
#: output", checked 2026-10-13). So on Claude the bound is 10,000.
HOOK_BYTES = 32 * 1024
HOOK_BYTES_BY_FLAVOR = {"claude": 10_000}

#: The pointer line of an agent with no task in progress.
IDLE_LINE = ("You have no task in progress. When you get one, a `/goal` pointer names your work "
             "item; until then there is no file to read.")

#: Opens the list of files the bound kept out of the hook's output.
AS_FILES = ("{what} not printed here: with {it} this text would pass hx's {bound}-byte hook bound. "
            "Use the Read tool once on each file below before anything else; do not cat them and "
            "do not read them twice.")

#: The line that split persona from memory before the learnings were the memory channel.
#: Nothing writes it. Only `migrate` and the source readers (here and `prompt_compiler`) still
#: look for it, to cut a file an older hx installed.
LEGACY_HEADER = "## UPDATES BELOW ONLY"

#: Every persona text an hx shipped as `personas/<role>/AGENTS.md` or `config/partner/AGENTS.md`,
#: normalized (`_shipped`), as sha256: an installed copy that is one of them is replaced by the
#: shipped `personas/<role>.md` without a word; one that is not was edited, and what it says that
#: the new persona does not becomes a learnings proposal (`migrate_layout`).
SHIPPED_PERSONAS = frozenset({
    "00be2b70aeb36bf0579a10c0f849eb895e0e59b788c06e41667f6f0b66fc2131",
    "166e62989ee9b2f7467b85eba64e1c6342226bb04abccc60173f6bc54c7f517f",
    "16b2b1b4a8240950419ef878ab5ff05f9e657a7f55158561a2c8066b242d8367",
    "21fb71edaffde8f93e93f639f79a5a2ed53c77c00498ad05d415e42d386311e7",
    "226f3933d8d7f4224180d412da132f3554d79a4a617c53ed1cda19eb5026638c",
    "27e625876592ceef7ce854424782c04006dbb0209c4767abbc90271090437605",
    "2bdec3757bbc07f72115256ca60e4e11cef7ce5be2df4fe8667b56a7c1c90487",
    "33a3bfb53d73719546880a29413550eb435c197bb071183f18172375cfb88bb3",
    "36aaa23c95ff50be607c3abf47dd596f84b007748b92b50a4965a281ab56c79c",
    "39f72f60696b37a883536e91acb28b9b96ef56c0454ac9debf75911ae1d170d9",
    "40ced17ca4c7307b648bb569587ac14cbe1ba80edf060c4293e648ee34abdaef",
    "41296b93d4a9f387cc8b8a1fea8be6802192303a7b5710f2a01806f17476370c",
    "6af82271219d9f7494c45ba0b6b5e0faca3cc8ade844694ea354cab1538829dc",
    "6d4f3b849edeafab1003a01eb4bc7f3779deff20f8b2db3b228164e4cfe7aa0b",
    "70c6f8554f9a21e32454470951df1f8ad5bcfaeb00ad2ac66647bddd6f928f05",
    "7277a5ca123afcd6a9a59abb18aad4d297efe8c4a18742a292e3064038f7171e",
    "75ad0b9ca4f10d332bcfc4c09aa4a23537b04e534327bcfa661ab6567e45cc6b",
    "7af74cde17f3bde6e16b1daaf206bbd3f1d0b27eb1933f593be794eaa19ad81f",
    "7fee8633c37d4231d9b9e092a574e5e2f8b6e2b08db4bf572570e5a2b27fd926",
    "81d0dcd0d78415095346ec1dd50117f83d8d75d6ffcdad2e30aeb64eb1e4f2ae",
    "89a269c954a6a6066f4d80989c9bc292e8a1ea8bad2569e8f29469bfec975bfd",
    "8ca73f52b58eb258252f9e7701a5da47bfe842f7ecf46be5d670de6ab5b1148d",
    "b3ecde46919efba46c4de316cba901acb8a4b7a0af15dc6e74e89309d9c8e813",
    "b6e954a35e7b38f201745bfd29edb1323f69b55b3327da24fdcd61c3663cbeda",
    "c2f1bbc65ad9756a17757410ea634a14fdc4aeeb90e58d4be8af4071ee35ef21",
    "c95e69df99adbb3f58e8b6b7ebbcf8a6d478d68f02e5048679aafc3afe15a029",
    "c999db189ae6a07b6af7974f1d5d1741f09b6f3b6882d67cb1c00bbadae26c38",
    "d7b0301ae25692775181094c9793a778722de6d4c54b6eb1178aa0a7569dde43",
    "dc7fe81534ae113dc9c578a6506d4237358d954fb85d9c7b8c57d53a3263079f",
    "ee0689b3922f7c8e4e464f4d0cba9c67354a917c8683f319344661f40934985e",
    "f8b5575fde5c40e92e2f761ea3bfab791a4f4a3ce2cd78034ef78088484d90ad",
    "fbfe1ef954c5e5c9f3a5f7bb538a95eebae97357e882864c0e39af219711c322",
})

DISTILLED_RE = re.compile(
    r"(?:^## Distilled directives\s*\n\n)?<!-- hx:distilled begin .*?-->\n(?P<body>.*?)\n<!-- hx:distilled end -->",
    re.S | re.M,
)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


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


def _above_header(text: str) -> str:
    return text.split(LEGACY_HEADER, 1)[0]


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
    return _above_header(path.read_text()).strip("\n") or None


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
        tail = [] if item_id == PARTNER else [("pointer", None, IDLE_LINE)]

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

    sources = [role_source(root, values["role"]), skeleton_dir() / "personas" / f"{values['role']}.md"]
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

    Returns None when there is nothing to migrate. The Partner has no per-id file: its persona
    is `personas/partner.md` (`migrate_layout`).
    """
    root = Path(root)
    if item_id == PARTNER:
        return None
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


def _norm_persona(text: str) -> str:
    return _norm(COMMENT_RE.sub("", _above_header(text)))


def _shipped(text: str) -> bool:
    """Whether an installed persona file is one an hx shipped, untouched."""
    return hashlib.sha256(_norm_persona(text).encode()).hexdigest() in SHIPPED_PERSONAS


def _neutral(text: str) -> str:
    """A persona without per-id variables: an older one said `{{id}}` where the identity line
    now names the agent; the markers read as what they stand for."""
    return re.sub(r"\{\{(id|pod|role|flavor|workdir)\}\}", r"<\1>", text)


def _migrate_persona_file(root: Path, role: str, old: Path, *, env=None) -> dict:
    """One persona file of the old layout into `personas/<role>.md`, then removed."""
    from . import learn

    text = old.read_text()
    upper, found, lower = text.partition(LEGACY_HEADER)
    kept = _neutral(re.sub(r"\n{3,}", "\n\n", REGION_RE.sub("", upper)).strip("\n"))
    new = role_source(root, role)
    relative = old.relative_to(root)
    result = {"persona": str(relative), "role": role, "proposal": None}
    leftover: list[tuple[str, str]] = []
    if found:
        # Only the Partner's file ever had a memory below the old header.
        memory = _unsaid(lower, set())
        if memory:
            leftover.append(("Memory below the old header", memory))
    if not new.is_file():
        store.atomic_write_text(new, kept + "\n")
        result["moved"] = True
    elif not _shipped(text):
        known = {_norm(block) for block in _paragraphs(COMMENT_RE.sub("", new.read_text()))}
        edited = _unsaid(kept, known)
        if edited:
            leftover.append((f"What {relative} said that personas/{role}.md does not", edited))
    if leftover:
        body = "\n\n".join(f"### {title}\n\n{block}" for title, block in leftover)
        result["proposal"] = learn.propose_text(
            root, text=body, author=PARTNER if role == PARTNER else "hx-migration", role=role,
            source=f"migrated from {relative}", env=env,
        )["id"]
    old.unlink()
    if old.parent.parent == root / "personas" and not any(old.parent.iterdir()):
        old.parent.rmdir()
    return result


def _partner_role(root: Path) -> str:
    try:
        return str(_harness(root, PARTNER).get("role") or PARTNER)
    except (NotFound, json.JSONDecodeError):
        return PARTNER


def migrate_layout(root: Path, *, env=None) -> list[dict]:
    """Move an instance onto the layout where a persona is `personas/<role>.md` and a role's
    rules are `personas/<role>/<category>.md`: each `personas/<role>/AGENTS.md`, and
    `config/partner/AGENTS.md`, becomes `personas/<role>.md` when there is none yet; when the
    shipped one is already there (`hx install` copies it), an installed file hx shipped is
    simply removed, and one somebody edited leaves what the new persona does not say as one
    learnings proposal scoped to the role. Rules scoped with `Role:` in the line move
    (`hx.learn.migrate_role_rules`). Returns what moved; nothing once the instance is moved."""
    from . import learn

    root = Path(root)
    olds = [(path.parent.name, path) for path in sorted((root / "personas").glob("*/AGENTS.md"))]
    partner = root / "config" / PARTNER / "AGENTS.md"
    if partner.is_file():
        olds.append((_partner_role(root), partner))
    results = [_migrate_persona_file(root, role, old, env=env) for role, old in olds]
    moved = learn.migrate_role_rules(root, env)
    if moved:
        results.append({"rules_moved": moved})
    return results


def migrate_all(root: Path, *, env=None) -> list[dict]:
    """`migrate_layout`, then `migrate` for every `config/<id>/`; only what had something to move."""
    directory = Path(root) / "config"
    results = migrate_layout(root, env=env)
    if not directory.is_dir():
        return results
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
    if result.get("persona"):
        parts.append(f"{result['persona']} " + (f"is now personas/{result['role']}.md" if result.get("moved")
                                                else f"removed (personas/{result['role']}.md replaces it)"))
    if result.get("rules_moved"):
        parts.append(f"{result['rules_moved']} rule(s) scoped with `Role:` moved to personas/<role>/<category>.md")
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
        description="Print what the `context` hook hands ID at every boundary: the invariants and "
                    "the persona where the hook is their channel, the identity line, the rules, and "
                    "the pointer to the work item.",
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
