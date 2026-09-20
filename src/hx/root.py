"""HARNESS_ROOT resolution and the standing refusal.

Every hx command calls `resolve_root()` before doing anything else. hx refuses any root
that is, or resolves (symlinks included) inside, the user's `~/.claude`, and never derives
an instance path from the user's Claude home (ORCHESTRATION.md standing rule 2,
tests/guard/test_harness_root_refusal.py).
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import HxRefusal

#: Where the instance lives when `HARNESS_ROOT` is not set. Spec 17.1 says `/srv/hx` on a
#: server and `~/hx` on a workstation; hx only ever defaults to the workstation form and
#: a server install passes `HARNESS_ROOT` explicitly.
DEFAULT_ROOT_NAME = "hx"


def user_home(env: os._Environ[str] | dict[str, str] | None = None) -> Path:
    """The user's home directory, honouring `HOME` so tests can redirect it."""
    env = os.environ if env is None else env
    home = env.get("HOME")
    if home:
        return Path(home)
    return Path(os.path.expanduser("~"))


def user_claude_home(env: os._Environ[str] | dict[str, str] | None = None) -> Path:
    """The user's own Claude configuration directory. hx never writes here, ever."""
    return user_home(env) / ".claude"


def expand(path: str | os.PathLike[str], env: os._Environ[str] | dict[str, str] | None = None) -> Path:
    """Expand a leading `~` against the given environment's HOME, not the process's."""
    text = str(path)
    if text == "~":
        return user_home(env)
    if text.startswith("~/"):
        return user_home(env) / text[2:]
    return Path(text)


def _resolve(path: str | os.PathLike[str], env: os._Environ[str] | dict[str, str] | None = None) -> Path:
    """Absolute path with every symlink resolved, for parts that exist and parts that do not."""
    return expand(path, env).resolve()


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def check_root(root: Path, env: os._Environ[str] | dict[str, str] | None = None) -> None:
    """Raise `HxRefusal` when `root` is, or lies inside, the user's `~/.claude`.

    Both the literal path and the fully symlink-resolved path are checked, so neither a
    symlink pointing into `~/.claude` nor a `~/.claude` that is itself a symlink gets past.
    """
    claude = user_claude_home(env)
    literal = expand(root, env).absolute()
    for candidate, reference in (
        (literal, claude.absolute()),
        (_resolve(root, env), _resolve(claude, env)),
    ):
        if _is_within(candidate, reference):
            raise HxRefusal(
                f"refuse to use {root} as HARNESS_ROOT: it is inside the user's Claude "
                f"home {claude}; hx never touches the user's ~/.claude (spec 17.3). "
                f"Set HARNESS_ROOT to a directory outside it."
            )


def resolve_root(
    override: str | os.PathLike[str] | None = None,
    env: os._Environ[str] | dict[str, str] | None = None,
) -> Path:
    """The one function every command calls first.

    Order: an explicit `--root`, else `HARNESS_ROOT`, else `~/hx`. The result is absolute
    and has passed `check_root`. It is not required to exist: `hx install` creates it and
    `hx doctor` reports on a root that does not.
    """
    env = os.environ if env is None else env
    raw = override if override is not None else env.get("HARNESS_ROOT")
    if raw:
        root = expand(raw, env)
    else:
        root = user_home(env) / DEFAULT_ROOT_NAME
    check_root(root, env)
    return root.absolute()
