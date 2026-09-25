"""The Partner's `guard` hook: `hx-hook --id partner guard` over `config/partner/guard.json`.

Driven the way Claude Code drives it: a `PreToolUse` payload on stdin, exit 2 with the reason
on stderr to block, exit 0 with no output to allow. Every rule is run in the direction that
must deny and in the neighbouring direction that must allow.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import clean_env

DENY_ROOT = "/deny/tree"
RULES = {
    "deny_paths": [DENY_ROOT, "~/product"],
    "deny_commands": [r"\bdotnet\b", r"\bpytest\b", r"\bgit\s+grep\b"],
    "allow_commands": [r"^hx "],
}


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "root"
    (root / "config" / "partner").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "config" / "partner" / "guard.json").write_text(json.dumps(RULES))
    return root


def run_guard(root, payload, *, item_id="partner", home=None):
    env = clean_env(HARNESS_ROOT=str(root), HARNESS_ID=item_id)
    env["HOME"] = str(home or root.parent / "home")
    return subprocess.run(
        [sys.executable, "-m", "hx.hooks", "--id", item_id, "guard", "--root", str(root)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
    )


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/somewhere"}


def tool(name, **tool_input):
    return {"tool_name": name, "tool_input": tool_input, "cwd": "/somewhere"}


PATCH_UNDER_ROOT = (
    "*** Begin Patch ***\n"
    "*** Update File: /deny/tree/a.cs\n"
    "@@\n"
    "-old\n"
    "+new\n"
    "*** End Patch ***"
)


def patch(command):
    return {"tool_name": "apply_patch", "tool_input": {"command": command}, "cwd": "/somewhere"}


DENIED = {
    "bash-deny-regex": bash("dotnet test app/tests"),
    "bash-deny-in-a-later-segment": bash("hx board && dotnet test"),
    "bash-deny-in-a-subshell": bash("echo $(pytest -q)"),
    "bash-deny-on-a-later-line": bash("hx board\npytest -q"),
    "bash-git-grep": bash("git grep -n Handler"),
    "bash-names-the-root": bash("sed -n 1,5p /deny/tree/a.cs"),
    "bash-cd-into-the-root": bash("cd /deny/tree && ls"),
    "bash-quoted-root": bash('cat "/deny/tree/x y.cs"'),
    "bash-home-relative-root": bash("cat ~/product/src/a.cs"),
    "bash-dollar-home-root": bash("wc -l $HOME/product/a.cs"),
    "read-under-root": tool("Read", file_path="/deny/tree/Handlers/X.cs"),
    "read-the-root-dotdot": tool("Read", file_path="/deny/elsewhere/../tree/a.cs"),
    "read-relative-under-root": {"tool_name": "Read", "tool_input": {"file_path": "tree/a.cs"}, "cwd": "/deny"},
    "edit-under-root": tool("Edit", file_path="/deny/tree/a.cs", old_string="a", new_string="b"),
    "write-under-root": tool("Write", file_path="/deny/tree/new.cs", content="x"),
    "grep-path-under-root": tool("Grep", pattern="foo", path="/deny/tree"),
    "grep-path-above-root": tool("Grep", pattern="foo", path="/deny"),
    "glob-absolute-pattern": tool("Glob", pattern="/deny/tree/**/*.cs"),
    "glob-path-under-root": tool("Glob", pattern="**/*.cs", path="/deny/tree/src"),
    "patch-names-the-root": patch(PATCH_UNDER_ROOT),
    "patch-home-relative-root": patch("*** Update File: ~/product/src/a.cs ***"),
}

ALLOWED = {
    "hx-board": bash("hx board"),
    "allow-regex-wins-in-its-segment": bash("hx dispatch eng-001 goal-with-pytest-in-name.md"),
    "separator-inside-quotes": bash('hx wake partner "eng-001 done; pytest ran green"'),
    "a-sibling-of-the-root": bash("cat /deny/treehouse/a.md"),
    "a-longer-path-containing-the-root": bash("ls /x/deny/tree/a"),
    "read-elsewhere": tool("Read", file_path="/allowed/PARTNER.md"),
    "read-a-sibling": tool("Read", file_path="/deny/treehouse/a.md"),
    "grep-pattern-is-not-a-path": tool("Grep", pattern="/deny/tree", path="/allowed"),
    "glob-relative-pattern-elsewhere": tool("Glob", pattern="**/*.md", path="/allowed"),
    "another-tool": tool("Agent", prompt="dotnet test /deny/tree"),
    "no-tool-input": {"tool_name": "Bash"},
    "empty-payload": "",
    "patch-without-a-root": patch("*** Update File: /allowed/a.cs ***"),
    "patch-mentioning-a-denied-word-is-not-a-command": patch("*** Update File: /allowed/a.cs ***\n// run dotnet test here"),
    "patch-without-command": tool("apply_patch"),
}


@pytest.mark.parametrize("payload", DENIED.values(), ids=DENIED.keys())
def test_guard_denies_with_one_line_on_stderr(root, payload):
    result = run_guard(root, payload)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    reason = result.stderr.strip()
    assert reason.startswith("hx guard: denied: ") and "\n" not in reason, reason
    assert "denied" in (root / "logs" / "partner" / "guard.log").read_text()


@pytest.mark.parametrize("payload", ALLOWED.values(), ids=ALLOWED.keys())
def test_guard_allows_with_no_output(root, payload):
    result = run_guard(root, payload)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""


@pytest.mark.parametrize(
    "contents",
    [None, "{not json", "[]", '{"deny_commands": ["("]}', '{"deny_paths": ["relative/tree"]}',
     '{"deny_commands": "dotnet"}', '{"deny_command": ["dotnet"]}'],
    ids=["absent", "not-json", "not-an-object", "bad-regex", "relative-root", "not-a-list", "unknown-key"],
)
def test_guard_is_inactive_and_logged_without_a_valid_file(root, contents):
    path = root / "config" / "partner" / "guard.json"
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    for payload in (bash("dotnet test"), tool("Read", file_path="/deny/tree/a.cs")):
        result = run_guard(root, payload)
        assert result.returncode == 0 and result.stdout == "" and result.stderr == "", result.stderr
    assert "inactive:" in (root / "logs" / "partner" / "guard.log").read_text()


def test_guard_never_denies_a_worker(root):
    """The adapter installs it for the Partner only; a worker id reaching it is inert."""
    result = run_guard(root, bash("dotnet test"), item_id="eng-001")
    assert result.returncode == 0 and result.stderr == ""


def test_guard_denial_stands_when_its_log_cannot_be_written(root):
    """A match is never allowed: not even when recording it fails."""
    (root / "logs").rmdir()
    (root / "logs").write_text("a file where the logs directory should be")
    result = run_guard(root, bash("dotnet test"))
    assert result.returncode == 2 and result.stderr.startswith("hx guard: denied: ")


def test_guard_crash_is_logged_and_allowed(root, monkeypatch):
    """The failure policy of hooks.py holds for the guard's own crashes."""
    import hx.hook_guard as hook_guard
    import hx.hooks as hooks

    def broken(payload, rules):
        raise RuntimeError("the guard broke")

    monkeypatch.setattr(hook_guard, "judge", broken)
    code = hooks.main(
        ["--id", "partner", "guard", "--root", str(root)],
        stdin=io.StringIO(json.dumps(bash("dotnet test"))),
        env={"HARNESS_ID": "partner", "HARNESS_ROOT": str(root)},
    )
    assert code == 0
    assert "the guard broke" in (root / "logs" / "partner" / "hook-errors.log").read_text()


def test_guard_segments_respect_quotes():
    from hx.hook_guard import segments

    assert segments('hx wake partner "a; b | c" && dotnet test') == [
        "hx wake partner a; b | c", "dotnet test",
    ]
    assert segments("unbalanced 'quote") == ["unbalanced 'quote"]
