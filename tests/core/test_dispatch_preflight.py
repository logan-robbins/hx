"""`hx dispatch`'s gate preflight (spec 08).

A goal's `### Checks` run in the id's workdir before the goal is sent. A block that exits 0 on
the untouched tree gates nothing and the dispatch is refused with `HX-GATE-EMPTY`; a non-zero
exit is the direction a gate must fail in, and the dispatch goes ahead with one line saying so;
a block that outruns the budget is not judged, and the dispatch goes ahead with that said.
"""

from __future__ import annotations

import io
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from .conftest import clean_env
from .test_transitions import control_manifest, item_state, tasks_of, wait_for_goal


@pytest.mark.parametrize(
    "checks",
    ["true", "test -f README.md"],
    ids=["always-true", "passes-in-the-workdir"],
)
def test_preflight_refuses_a_gate_that_already_passes(instance, hx, launched, goals, checks):
    """`test -f README.md` passes only in the worktree, so this also proves where it runs."""
    launched("eng-001")
    goal_file = goals("eng-001", checks=checks)
    before = control_manifest(instance)

    result = hx("dispatch", "eng-001", str(goal_file), cwd=instance)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.startswith("HX-GATE-EMPTY eng-001\n"), result.stdout
    assert "gates nothing" in result.stdout
    assert "HX-DISPATCH" not in result.stdout

    assert item_state(instance, "eng-001") == "idle"
    assert goal_file.exists(), "a refused goal is not consumed"
    assert control_manifest(instance) == before, "a refused dispatch writes nothing"


def test_preflight_passes_a_gate_that_fails_first(instance, hx, launched, goals):
    launched("eng-001")
    goals("eng-001", checks='echo "working on it"\necho "FAIL: DELIVERABLE.md is missing" >&2\nexit 3')
    result = hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().split("\n")
    assert len(lines) == 2, lines
    assert lines[0] == "gate preflight: exit 3 [eng-001], first FAIL line: FAIL: DELIVERABLE.md is missing"
    assert lines[1].startswith("HX-DISPATCH eng-001 working goal=")
    assert item_state(instance, "eng-001") == "working"
    wait_for_goal(instance, "eng-001")


def test_preflight_names_a_failure_without_a_fail_line(instance, hx, launched, goals):
    launched("eng-001")
    goals("eng-001", checks="test -f DELIVERABLE.md")
    result = hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[0] == (
        "gate preflight: exit 1 [eng-001], first FAIL line: no FAIL line in the output"
    )


def test_preflight_one_empty_gate_refuses_the_whole_dispatch(instance, hx, launched, goals, agent):
    agent("eng-002")
    launched("eng-001", "eng-002")
    goals("eng-001")
    goals("eng-002", checks="true")
    before = control_manifest(instance)
    result = hx("dispatch", "eng-001", "run/goal-eng-001.md", "eng-002", "run/goal-eng-002.md",
                cwd=instance)
    assert result.returncode == 1
    assert result.stdout.startswith("HX-GATE-EMPTY eng-002\n")
    assert item_state(instance, "eng-001") == "idle" and item_state(instance, "eng-002") == "idle"
    assert control_manifest(instance) == before


def test_preflight_over_budget_is_not_judged_and_dispatches(instance, launched, goals, tmux_server,
                                                          monkeypatch):
    """The budget is 120 s; lowered in-process here so the test does not wait two minutes.
    Everything the block started is killed with it, not left running."""
    import hx.dispatch as dispatch_mod

    assert dispatch_mod.PREFLIGHT_BUDGET == 120
    launched("eng-001")
    pid_file = instance / "preflight-child.pid"
    goals("eng-001", checks=f'sleep 300 &\necho $! > "{pid_file}"\nwait')
    monkeypatch.setattr(dispatch_mod, "PREFLIGHT_BUDGET", 1)

    env = clean_env(HARNESS_ROOT=str(instance), HX_TMUX=" ".join(tmux_server), HARNESS_ID="partner")
    out = io.StringIO()
    started = time.monotonic()
    with redirect_stdout(out):
        code = dispatch_mod.main(["eng-001", str(instance / "run" / "goal-eng-001.md")], instance, env=env)
    elapsed = time.monotonic() - started

    assert code == 0, out.getvalue()
    lines = out.getvalue().strip().split("\n")
    assert len(lines) == 2, lines
    assert lines[0] == "gate preflight: not judged after 1s [eng-001]"
    assert lines[1].startswith("HX-DISPATCH eng-001 working goal=")
    assert elapsed < 60, f"the budget did not stop the block: {elapsed:.0f}s"
    assert item_state(instance, "eng-001") == "working"
    assert tasks_of(instance)["eng-001"]["goal"]

    child = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_preflight_budget_has_no_skip(run_hx, tmp_path):
    """There is no flag to skip it: `hx dispatch --help` offers none."""
    result = run_hx("dispatch", "--help", env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 0
    assert "skip" not in result.stdout.lower() and "preflight" not in result.stdout.lower()
