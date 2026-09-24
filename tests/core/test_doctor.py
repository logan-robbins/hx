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
    assert ("claude", OK) in statuses(checks), [c for c in checks if c[1] == "claude"]


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
    assert not [c for c in checks if c[0] == FAIL], [c for c in checks if c[0] == FAIL]


def test_a_missing_root_warns_and_says_what_to_run(run_hx, tmp_path):
    root = tmp_path / "not-yet"
    result = run_hx("doctor", env_extra={"HARNESS_ROOT": str(root)})
    assert result.returncode == 0
    assert "hx install --skeleton-only" in result.stdout


def test_a_broken_models_file_fails(instance):
    (instance / "config" / "models.json").write_text('{"m": {"window": 10, "threshold": 99}}')
    checks = run_checks(instance)
    assert ("models", FAIL) in statuses(checks)


def test_a_missing_token_fails(instance):
    """Tightened in build-4: an instance with no token cannot launch anything."""
    (instance / "seed" / "token").unlink()
    assert ("token", FAIL) in statuses(run_checks(instance))


def test_a_home_without_settings_fails(instance):
    home = instance / "run" / "eng-001" / "home"
    home.mkdir(parents=True)
    assert ("home:eng-001", FAIL) in statuses(run_checks(instance))


def test_it_does_not_inspect_work_items(instance, work_item):
    """spec 14 D25: `hx doctor` polices no work item; the board is a listing."""
    work_item("eng-001", "working", outcome="done")
    (instance / "pods" / "engineers" / "eng-001-extra.md").write_text("---\nid: nope\n---\n")
    assert [c for c in run_checks(instance) if c[0] == FAIL] == []


def test_there_is_no_repo_check(instance):
    """`hx repo add`, the mirror and `config/repo.json` are gone (spec 14 D25)."""
    (instance / "config" / "repo.json").write_text('{"name": "product"}')
    assert [c for c in run_checks(instance) if c[1] == "repo"] == []


def test_a_token_readable_by_anyone_else_fails(instance):
    """CONTRACTS.md `seed/token`: mode 0600."""
    (instance / "seed" / "token").chmod(0o644)
    checks = run_checks(instance)
    assert ("token", FAIL) in statuses(checks)


def test_an_empty_token_fails(instance):
    (instance / "seed" / "token").write_text("\n")
    assert ("token", FAIL) in statuses(run_checks(instance))


def test_a_good_token_is_ok(instance):
    assert ("token", OK) in statuses(run_checks(instance))


def test_a_fresh_skeleton_root_says_what_is_still_missing(run_hx, tmp_path):
    """A `--skeleton-only` root is not a working instance: no token, no homes (spec 17.2)."""
    root = tmp_path / "scratch"
    created = run_hx("install", "--skeleton-only", "--root", str(root))
    assert created.returncode == 0, created.stderr
    result = run_hx("doctor", env_extra={"HARNESS_ROOT": str(root)})
    assert result.returncode == 1, result.stdout
    assert "seed/token" in result.stdout, "it names the one thing the human has to do"
    assert str(root) in result.stdout
    assert "tmux" in result.stdout and "python" in result.stdout


def test_json_form(run_hx, instance):
    result = run_hx("doctor", "--json", env_extra={"HARNESS_ROOT": str(instance)})
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    assert payload["root_abs"] == str(instance)
    assert {c["check"] for c in payload["checks"]} >= {"root", "python", "tmux", "git"}
    assert {c["status"] for c in payload["checks"]} <= {OK, WARN, FAIL}


# --- build-8 item 9: the pre-seeded first-launch state ------------------------------------------


def seed_home(instance, item_id="eng-001", **state):
    import json

    home = instance / "run" / item_id / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text("{}\n")
    if state:
        (home / ".claude.json").write_text(json.dumps(state))
    return home


def home_checks(instance, item_id="eng-001"):
    return [c for c in run_checks(instance) if c[1] == f"home:{item_id}"]


def test_doctor_fails_when_the_first_launch_state_is_missing(instance):
    """spec 08: each home's settings **and its pre-seeded first-launch state file**."""
    seed_home(instance)
    failures = [c for c in home_checks(instance) if c[0] == FAIL]
    assert failures and ".claude.json missing" in failures[0][2]


def test_doctor_fails_when_onboarding_is_not_marked_complete(instance):
    seed_home(instance, projects={"/w": {"hasTrustDialogAccepted": True}})
    failures = [c for c in home_checks(instance) if c[0] == FAIL]
    assert failures and "hasCompletedOnboarding" in failures[0][2]


def test_doctor_fails_when_no_workspace_trust_is_accepted(instance):
    seed_home(instance, hasCompletedOnboarding=True, projects={})
    failures = [c for c in home_checks(instance) if c[0] == FAIL]
    assert failures and "trust" in failures[0][2].lower()


def test_doctor_passes_a_properly_seeded_home(instance):
    seed_home(
        instance,
        hasCompletedOnboarding=True,
        projects={"/w": {"hasTrustDialogAccepted": True, "hasClaudeMdExternalIncludesApproved": True}},
    )
    assert [c for c in home_checks(instance) if c[0] == FAIL] == []


def _codex_home(instance, config_text):
    from hx.doctor import _codex_home

    home = instance / "run" / "eng-001" / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(config_text)
    return _codex_home(instance, home, "eng-001")


def test_codex_instance_without_a_token_fails(instance):
    from hx.doctor import _codex_instance

    assert ("codex-token", FAIL) in statuses(_codex_instance(instance))


def test_codex_instance_with_token_and_pin_passes(instance, tmp_path):
    from hx.doctor import _codex_instance

    token = instance / "seed" / "codex-token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("sk-test\n")
    token.chmod(0o600)
    probe = tmp_path / "codex"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    (instance / "config" / "codex.json").write_text(
        json.dumps({"bin": str(probe), "version": "0.156.1"})
    )
    assert ("codex-token", OK) in statuses(_codex_instance(instance))
    assert ("codex", OK) in statuses(_codex_instance(instance))


def test_codex_home_checks_posture_and_hooks(instance):
    good = _codex_home(
        instance,
        'approval_policy = "never"\n'
        'sandbox_mode = "danger-full-access"\n'
        "[[hooks.SessionStart]]\n"
        "[[hooks.PostToolUse]]\n"
        "[[hooks.Stop]]\n",
    )
    assert not [c for c in good if c[0] == FAIL]
    bad = _codex_home(instance, 'approval_policy = "never"\n')
    assert [c for c in bad if c[0] == FAIL]
