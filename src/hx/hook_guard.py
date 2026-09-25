"""The `guard` hook: `PreToolUse`, the Partner only (spec 09.1).

The Partner directs by status and dispatch. Verification, control runs, test runs and code
reads belong to QA and the engineers, and the rule is enforced here rather than remembered.
The rules live in `config/partner/guard.json`, written by the human or the Partner:

    {"deny_paths": ["/abs/product/tree", ...],
     "deny_commands": ["\\\\bdotnet\\\\b", ...],
     "allow_commands": ["^hx ", ...]}

A call is denied (exit 2, one line on stderr, which Claude Code shows the model; the
Codex adapter additionally translates the denial into Codex's `permissionDecision` JSON)
when

  - a `Bash` command has a segment — the command split at `;`, `&`, `|`, `(`, `)`, `<`, `>` and
    newlines outside quotes — that matches a `deny_commands` regex and no `allow_commands`
    regex, so `hx board && dotnet test` is not let through by `^hx `; or
  - a `Bash` command names a path under a `deny_paths` root (as an absolute path, `~/…` or
    `$HOME/…`); or a `Read`, `Edit` or `Write` `file_path`, a `Grep` or `Glob` `path`, or an
    absolute `Glob` `pattern`, is under a deny root — or, for `Grep` and `Glob`, which
    recurse, contains one; or
  - a Codex `apply_patch` names a path under a `deny_paths` root anywhere in its
    `tool_input.command` patch text. Command regexes are not applied to patch text: a
    patch does not execute, so a denied word inside it is not a denied act.

Everything else exits 0 with no output. An absent or invalid `guard.json` leaves the guard
inactive, and says so in `logs/<id>/guard.log`, as does every denial. The module's failure
policy holds for the guard's own crashes (log and allow, `hooks.py`); a match is never allowed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from .ids import PARTNER

GUARD_FILE = "guard.json"
GUARD_LOG = "guard.log"
KEYS = ("deny_paths", "deny_commands", "allow_commands")
DENY = 2

#: The tools the adapter's matcher sends here, and the tool_input keys that name a path.
PATH_KEYS = {
    "Read": ("file_path",),
    "Edit": ("file_path",),
    "Write": ("file_path",),
    "Grep": ("path",),
    "Glob": ("path", "pattern"),
}
#: `Grep` and `Glob` descend into what they are pointed at.
RECURSIVE = ("Grep", "Glob")
MATCHER = "Bash|" + "|".join(PATH_KEYS)

_SEPARATORS = ";&|()<>\n"
_GLOB_CHARS = re.compile(r"[*?\[{]")


class Rules:
    def __init__(self, deny_paths: list[str], deny_commands: list[re.Pattern], allow_commands: list[re.Pattern]):
        self.deny_paths = deny_paths
        self.deny_commands = deny_commands
        self.allow_commands = allow_commands


def guard_path(root: Path) -> Path:
    return root / "config" / PARTNER / GUARD_FILE


def log(root: Path, item_id: str, line: str) -> None:
    """Never raises: a denial must stand even when its record cannot be written."""
    try:
        from . import timestamps

        path = root / "logs" / item_id / GUARD_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(f"{timestamps.now()} {line}\n")
    except Exception:
        pass


def _normal(path: str, cwd: str | None = None) -> str:
    path = os.path.expanduser(path)
    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)
    return os.path.normpath(path)


def load_rules(root: Path) -> tuple[Rules | None, str]:
    """The rules, or None and why the guard is inactive."""
    path = guard_path(root)
    if not path.is_file():
        return None, f"inactive: no {path}"
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"inactive: {path} is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"inactive: {path} must be an object, got {type(data).__name__}"
    unknown = sorted(set(data) - set(KEYS))
    if unknown:
        return None, f"inactive: {path}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(KEYS)}"
    lists = {}
    for key in KEYS:
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            return None, f"inactive: {path}: `{key}` must be a list of non-empty strings"
        lists[key] = value
    compiled = {}
    for key in ("deny_commands", "allow_commands"):
        try:
            compiled[key] = [re.compile(v) for v in lists[key]]
        except re.error as exc:
            return None, f"inactive: {path}: `{key}` has an invalid regex: {exc}"
    roots = []
    for value in lists["deny_paths"]:
        normal = _normal(value)
        if not os.path.isabs(normal):
            return None, f"inactive: {path}: deny_paths entry `{value}` is not an absolute path"
        roots.append(normal)
    return Rules(roots, compiled["deny_commands"], compiled["allow_commands"]), ""


def segments(command: str) -> list[str]:
    """The simple commands in `command`, split at shell separators outside quotes."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=_SEPARATORS)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        # Unbalanced quotes: judge the command whole rather than not at all.
        return [command]
    found, current = [], []
    for token in tokens:
        if token and all(ch in _SEPARATORS for ch in token):
            if current:
                found.append(" ".join(current))
            current = []
        else:
            current.append(token)
    if current:
        found.append(" ".join(current))
    return found or [command]


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _forms(root: str) -> list[str]:
    """How a command line can spell `root`: absolute, and relative to the home directory."""
    forms = [root]
    home = os.path.expanduser("~")
    if home and home != "~" and _under(root, home) and root != home:
        rest = root[len(home.rstrip(os.sep)):]
        forms += ["~" + rest, "$HOME" + rest, "${HOME}" + rest]
    return forms


def command_names_root(command: str, root: str) -> bool:
    for form in _forms(root):
        pattern = r"(?<![\w.~/$-])" + re.escape(form) + r"(?![\w.-])"
        if re.search(pattern, command):
            return True
    return False


def _glob_base(pattern: str) -> str | None:
    match = _GLOB_CHARS.search(pattern)
    base = pattern[: match.start()] if match else pattern
    base = os.path.expanduser(base)
    if not os.path.isabs(base):
        return None
    return os.path.normpath(base if base.endswith(os.sep) or not match else os.path.dirname(base))


def _path_denial(tool: str, tool_input: dict, rules: Rules, cwd: str | None) -> str | None:
    for key in PATH_KEYS[tool]:
        value = tool_input.get(key)
        if not isinstance(value, str) or not value:
            continue
        if key == "pattern":
            base = _glob_base(value)
            if base is None:
                continue
            candidates = [base]
        else:
            candidates = [_normal(value, cwd)]
            try:
                candidates.append(os.path.realpath(candidates[0]))
            except (OSError, ValueError):
                pass
        for root in rules.deny_paths:
            real_root = os.path.realpath(root)
            for candidate in candidates:
                for r in {root, real_root}:
                    if _under(candidate, r):
                        return f"{tool} `{key}` {value} is under the deny root {root}"
                    if tool in RECURSIVE and _under(r, candidate):
                        return f"{tool} `{key}` {value} would search the deny root {root}"
    return None


def _patch_denial(command: object, rules: Rules) -> str | None:
    """Deny a Codex `apply_patch` whose patch text names a deny root.

    Only the path half applies: the patch is text to write, not a command to run,
    so `deny_commands` regexes are never matched against it.
    """
    if not isinstance(command, str) or not command:
        return None
    for root in rules.deny_paths:
        if command_names_root(command, root):
            return f"apply_patch names the deny root {root}"
    return None


def _bash_denial(command: str, rules: Rules) -> str | None:
    for segment in segments(command):
        deny = next((p for p in rules.deny_commands if p.search(segment)), None)
        if deny is not None and not any(p.search(segment) for p in rules.allow_commands):
            return f"Bash `{segment}` matches deny_commands `{deny.pattern}` and no allow_commands"
    for root in rules.deny_paths:
        if command_names_root(command, root):
            return f"Bash command names the deny root {root}"
    return None


def judge(payload: dict, rules: Rules) -> str | None:
    """The reason a call is denied, or None."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    if tool == "Bash":
        command = tool_input.get("command")
        return _bash_denial(command, rules) if isinstance(command, str) and command else None
    if tool == "apply_patch":
        return _patch_denial(tool_input.get("command"), rules)
    if tool in PATH_KEYS:
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
        return _path_denial(tool, tool_input, rules, cwd)
    return None


def handle(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    if item_id != PARTNER:
        # The adapter installs it for the Partner alone; a worker is never guarded (spec 09.1).
        log(root, item_id, "inactive: the guard is the Partner's")
        return 0, ""
    rules, why = load_rules(root)
    if rules is None:
        log(root, item_id, why)
        return 0, ""
    reason = judge(payload, rules)
    if reason is None:
        return 0, ""
    line = (
        f"hx guard: denied: {reason} ({guard_path(root)}). The Partner directs by status and "
        f"dispatch; test runs, control runs and code reads belong to QA or the engineer"
    )
    log(root, item_id, line)
    return DENY, line
