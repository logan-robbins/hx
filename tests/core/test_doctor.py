"""`hx doctor` (spec 08, goal build-1 item 1).

It prints the resolved root, the Python version, the tmux version, and the pinned binary
from `config/claude.json` when present, and exits 0. `fail` is reserved for what M0 owns;
everything a later milestone brings is `warn` (handoff/orchestrator-to-build.md answer 5).
"""

from __future__ import annotations

import json

from hx.doctor import FAIL, OK, WARN, bare_claude_version, run_checks


def statuses(checks):
    return {(check, status) for status, check, _ in checks}


def detail_for(checks, name):
    return [detail for _, check, detail in checks if check == name]


def test_bare_claude_version_strips_the_suffix():
    """CONTRACTS.md "Claude Code version strings"."""
    assert bare_claude_version("2.1.278 (Claude Code)\n") == "2.1.278"
    assert bare_claude_version("2.1.278") == "2.1.278"


def test_reports_root_python_tmux_and_git(instance):
    checks = run_checks(instance)
    names = {check for _, check, _ in checks}
    assert {"root", "python", "tmux", "git"} <= names
    assert detail_for(checks, "root") == [str(instance)]
    assert all(status != FAIL for status, check, _ in checks if check in ("python", "tmux", "git"))


def test_reports_the_pinned_binary(instance):
    checks = run_checks(instance)
    assert any("fake-0" in detail or "reports" in detail for _, check, detail in checks if check == "claude")


def test_an_unpinned_instance_only_warns(instance):
    (instance / "config" / "claude.json").unlink()
    checks = run_checks(instance)
    assert ("claude", WARN) in statuses(checks)
    assert not [c for c in checks if c[0] == FAIL]


def test_a_pinned_binary_that_is_gone_fails(instance):
    (instance / "config" / "claude.json").write_text(json.dumps({"bin": "/nope/claude", "version": "9"}))
    checks = run_checks(instance)
    assert ("claude", FAIL) in statuses(checks)


def test_missing_skeleton_files_are_warnings_not_failures(instance):
    """The gtm lane owns these; doctor reports each missing one (goal build-1 item 2)."""
    (instance / "PARTNER.md").unlink()
    (instance / "templates" / "work-item.md").unlink()
    checks = run_checks(instance)
    warned = {detail for status, check, detail in checks if check == "skeleton" and status == WARN}
    assert "PARTNER.md missing" in warned
    assert "templates/work-item.md missing" in warned
    assert not [c for c in checks if c[0] == FAIL]


def test_a_missing_root_warns_and_says_what_to_run(run_hx, tmp_path):
    root = tmp_path / "not-yet"
    result = run_hx("doctor", env_extra={"HARNESS_ROOT": str(root)})
    assert result.returncode == 0
    assert "hx install --skeleton-only" in result.stdout


def test_a_broken_models_file_fails(instance):
    (instance / "config" / "models.json").write_text('{"m": {"window": 10, "threshold": 99}}')
    checks = run_checks(instance)
    assert ("models", FAIL) in statuses(checks)


def test_seed_and_home_gaps_are_warnings(instance):
    (instance / "seed" / "home" / ".credentials.json").unlink()
    home = instance / "run" / "eng-001" / "home"
    home.mkdir(parents=True)
    checks = run_checks(instance)
    assert ("seed", WARN) in statuses(checks)
    assert ("home:eng-001", WARN) in statuses(checks)
    assert not [c for c in checks if c[0] == FAIL]


def test_a_fresh_skeleton_root_exits_0(run_hx, tmp_path):
    """The Done-when of goal build-1: doctor works on a `--skeleton-only` scratch root."""
    root = tmp_path / "scratch"
    created = run_hx("install", "--skeleton-only", "--root", str(root))
    assert created.returncode == 0, created.stderr
    result = run_hx("doctor", env_extra={"HARNESS_ROOT": str(root)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(root) in result.stdout
    assert "tmux" in result.stdout and "python" in result.stdout


def test_json_form(run_hx, instance):
    result = run_hx("doctor", "--json", env_extra={"HARNESS_ROOT": str(instance)})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["root_abs"] == str(instance)
    assert {c["check"] for c in payload["checks"]} >= {"root", "python", "tmux", "git"}
    assert {c["status"] for c in payload["checks"]} <= {OK, WARN, FAIL}
