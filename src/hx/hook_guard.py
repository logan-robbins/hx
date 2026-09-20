"""The `guard` hook: `PreToolUse`, every tool, every agent (spec 09.2).

Bypass permissions removes every prompt, so these eight rules are the only enforcement, and
they are not bypassable by permission mode. They implement the ownership table of spec 04:
one writer per artifact, with the boundary drawn mechanically.

First match wins. Every string in `.tool_input` is evaluated, paths resolved with symlinks
followed (`realpath -m`) relative to the payload's `.cwd`, and compared against the
`HARNESS_ROOT` subtrees — never against a bare relative name, so a `config/` directory inside
the product worktree is the agent's own business.

A deny is exit 2 with the reason on stderr and nothing on stdout (spec 09.1). Never exit 1.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .ids import ID_PATTERN, PARTNER

#: Tools whose `.tool_input` names a file the tool is about to change.
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update"}
#: Where a tool's target path lives in `.tool_input`, by key, in priority order.
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

HEADER = "## UPDATES BELOW ONLY"

_AGENTS_MD_RE = re.compile(rf"^config/(?P<id>{ID_PATTERN})/AGENTS\.md$")
_WORK_ITEM_RE = re.compile(rf"^pods/[^/]+/(?P<id>{ID_PATTERN})-working\.md$")

#: Rule 6: everything hx owns (spec 04).
HX_OWNED_DIRS = ("pods", "logs", "state", "run", "archive")
HX_OWNED_FILES = ("tasks.json",)

#: Leading `VAR=value` assignments and a leading `cd <root> &&`, stripped before rule 7 looks
#: at what the command actually runs (goal build-3 item 3).
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(?:[^\s'\"]*|'[^']*'|\"[^\"]*\")\s+")
_CD_PREFIX_RE = re.compile(r"^cd\s+(?:'[^']*'|\"[^\"]*\"|[^\s;&|]+)\s*(?:&&|;)\s*")


@dataclass(frozen=True)
class Decision:
    allow: bool
    rule: int
    reason: str = ""


def _strings(value: object) -> list[str]:
    """Every string anywhere in `.tool_input` (spec 09.2)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _strings(item)]
    return []


def _looks_like_path(token: str) -> bool:
    return "/" in token or token.startswith((".", "~"))


def _path_tokens(text: str) -> list[str]:
    """Path-ish tokens in a command line, globs cut back to their literal prefix."""
    try:
        tokens = shlex.split(text, comments=False, posix=True)
    except ValueError:
        tokens = text.split()
    found = []
    for token in tokens:
        for piece in re.split(r"[;&|<>()]+", token):
            piece = piece.strip()
            if not piece or not _looks_like_path(piece):
                continue
            # `../../config/*/AGENTS.md` has no literal path, but `../../config/` does.
            literal = re.split(r"[*?\[]", piece)[0]
            if literal:
                found.append(literal)
    return found


def resolve(candidate: str, cwd: Path) -> Path:
    """`realpath -m`: absolute, symlinks followed, parts that do not exist appended."""
    expanded = os.path.expanduser(candidate)
    base = Path(expanded) if os.path.isabs(expanded) else cwd / expanded
    return Path(base).resolve()


def _relative_to_root(path: Path, root: Path) -> str | None:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return None


def _under(relative: str | None, *prefixes: str) -> bool:
    if relative is None:
        return False
    return any(relative == p or relative.startswith(p.rstrip("/") + "/") for p in prefixes)


def _target_of(tool_name: str, tool_input: dict, cwd: Path, root: Path) -> tuple[Path, str | None] | None:
    """The file a write tool is about to change, as (absolute, root-relative)."""
    if tool_name not in WRITE_TOOLS:
        return None
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            absolute = resolve(value, cwd)
            return absolute, _relative_to_root(absolute, root)
    return None


def _proposed_content(tool_name: str, tool_input: dict, current: str) -> str | None:
    """The file as it would be after the edit, for rule 1's byte-identical check.

    Returns None when the shape is one this guard cannot simulate, which is treated as a
    change above the header: the guard denies rather than guesses.
    """
    if tool_name in ("Write", "Update"):
        content = tool_input.get("content")
        return content if isinstance(content, str) else None
    if tool_name == "Edit":
        old, new = tool_input.get("old_string"), tool_input.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if tool_input.get("replace_all"):
            return current.replace(old, new)
        if current.count(old) != 1:
            return None
        return current.replace(old, new, 1)
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        result = current
        for edit in edits:
            if not isinstance(edit, dict):
                return None
            old, new = edit.get("old_string"), edit.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                return None
            if edit.get("replace_all"):
                result = result.replace(old, new)
            elif result.count(old) == 1:
                result = result.replace(old, new, 1)
            else:
                return None
        return result
    return None


def _above_header(text: str) -> str:
    return text.split(HEADER, 1)[0] if HEADER in text else text


def strip_command_prefixes(command: str) -> str:
    """Drop leading env assignments and a leading `cd <dir> &&` (goal build-3 item 3)."""
    text = command.strip()
    while True:
        stripped = _ENV_ASSIGNMENT_RE.sub("", text, count=1)
        stripped = _CD_PREFIX_RE.sub("", stripped, count=1)
        if stripped == text:
            return text
        text = stripped.strip()


def decide(payload: dict, item_id: str, root: Path) -> Decision:
    """The eight rules of spec 09.2, in order. First match wins."""
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    cwd = Path(payload.get("cwd") or root)
    root = Path(root).resolve()

    target = _target_of(tool_name, tool_input, cwd, root)

    # 1. Your own AGENTS.md: the part below the header is yours, the part above it is not.
    if target is not None:
        absolute, relative = target
        match = _AGENTS_MD_RE.match(relative or "")
        if match:
            owner = match.group("id")
            if item_id == PARTNER:
                # The Partner writes personas, rarely and on direct human instruction
                # (spec 04; spec 13 M3 "partner | Edit config/eng-001/AGENTS.md above | allow").
                return Decision(True, 1)
            if owner == item_id:
                current = absolute.read_text() if absolute.is_file() else ""
                proposed = _proposed_content(tool_name, tool_input, current)
                if proposed is not None and _above_header(proposed) == _above_header(current):
                    return Decision(True, 1)
                return Decision(False, 1, "only the section below the header is yours")

    # 2. Identity is delivered by hook, never read or written directly.
    for text in _strings(tool_input):
        if "hx-hook" in text:
            return Decision(False, 2, "identity is delivered by hook")
        candidates = [text] if _looks_like_path(text) else []
        if tool_name == "Bash":
            candidates += _path_tokens(text)
        for candidate in candidates:
            relative = _relative_to_root(resolve(candidate, cwd), root)
            if _under(relative, "config", "companion"):
                return Decision(False, 2, "identity is delivered by hook")

    if target is not None:
        absolute, relative = target

        # 3. PARTNER.md is the Partner's state doc.
        if relative == "PARTNER.md":
            if item_id == PARTNER:
                return Decision(True, 3)
            return Decision(False, 3, "PARTNER.md is the Partner's")

        # 4. Your own work item, while you are working on it: yours to keep current.
        match = _WORK_ITEM_RE.match(relative or "")
        if match and match.group("id") == item_id:
            return Decision(True, 4)

        # 5. Orders are the Partner's, its own included.
        if _under(relative, "orders"):
            if item_id == PARTNER:
                return Decision(True, 5)
            return Decision(False, 5, "orders are the Partner's")

        # 6. Everything else hx owns.
        if _under(relative, *HX_OWNED_DIRS) or relative in HX_OWNED_FILES:
            return Decision(False, 6, "managed by hx")

    # 7. A Bash command that reaches into the instance runs through hx or not at all.
    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            mentions_root = "$HARNESS_ROOT" in command or "${HARNESS_ROOT" in command or str(root) in command
            if mentions_root and not strip_command_prefixes(command).startswith("hx "):
                return Decision(False, 7, "the instance is hx's; use an `hx` command")

    # 8. Everything else is the agent's own work.
    return Decision(True, 8)
