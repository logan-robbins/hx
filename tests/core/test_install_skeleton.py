"""Layout creation: `hx install --skeleton-only` (spec 03, 17.2 step 2)."""

from __future__ import annotations

import json
import os
import stat

from hx.install import EXPECTED_SKELETON_FILES, LAYOUT_DIRS, install_skeleton, skeleton_dir


def test_the_layout_directories_of_spec_03_are_created(tmp_path):
    root = tmp_path / "srv" / "hx"
    install_skeleton(root)
    for name in LAYOUT_DIRS:
        assert (root / name).is_dir(), f"{name} was not created"


def test_the_package_skeleton_is_copied(tmp_path):
    root = tmp_path / "instance"
    install_skeleton(root)
    for relative in sorted(skeleton_dir().rglob("*")):
        if relative.is_file():
            target = root / relative.relative_to(skeleton_dir())
            assert target.is_file(), f"{target} missing"
            assert target.read_bytes() == relative.read_bytes()


def test_the_adapters_are_copied_executable(tmp_path):
    root = tmp_path / "instance"
    install_skeleton(root)
    for name in ("install.sh", "start.sh"):
        target = root / "adapters" / "claude" / name
        assert target.is_file(), f"{name} missing"
        assert os.stat(target).st_mode & stat.S_IXUSR, f"{name} is not executable"


def test_models_json_lands_and_validates(tmp_path):
    from hx.config_models import load_models

    root = tmp_path / "instance"
    install_skeleton(root)
    models = load_models(root / "config" / "models.json")
    assert models


def test_the_instance_gitignore_keeps_runtime_state_out_of_git(tmp_path):
    root = tmp_path / "instance"
    install_skeleton(root)
    ignored = (root / ".gitignore").read_text()
    for entry in ("pods/", "logs/", "state/", "run/", "tasks.json"):
        assert entry in ignored


def test_install_is_idempotent_and_never_overwrites(tmp_path):
    root = tmp_path / "instance"
    install_skeleton(root)
    edited = root / "config" / "models.json"
    edited.write_text(json.dumps({"claude-opus-5": {"window": 400000, "threshold": 100000}}))
    result = install_skeleton(root)
    assert json.loads(edited.read_text())["claude-opus-5"]["window"] == 400000
    assert "config/models.json" in result["skipped"]
    assert result["created"] == [], result["created"]
    assert "config/hx.json" in result["skipped"]


def test_missing_gtm_files_are_reported_not_fatal(tmp_path, monkeypatch):
    """Until the gtm lane's texts land, install tolerates their absence (goal build-1 item 2)."""
    stripped = tmp_path / "skeleton"
    (stripped / "adapters" / "claude").mkdir(parents=True)
    (stripped / "adapters" / "claude" / "install.sh").write_text("#!/usr/bin/env bash\n")
    (stripped / "adapters" / "claude" / "start.sh").write_text("#!/usr/bin/env bash\n")
    (stripped / "config").mkdir()
    (stripped / "config" / "models.json").write_text('{"m": {"window": 100, "threshold": 10}}')
    monkeypatch.setattr("hx.install.skeleton_dir", lambda: stripped)

    root = tmp_path / "instance"
    result = install_skeleton(root)
    assert "config/CLAUDE.md" in result["missing"]
    assert "templates/work-item.md" in result["missing"]
    assert "config/models.json" not in result["missing"]
    assert set(result["missing"]) <= set(EXPECTED_SKELETON_FILES)


def test_install_stops_for_the_seed_token(run_hx, tmp_path, fake_claude_on_path):
    """Step 3 is the one thing hx cannot do for the human (spec 17.2, 11 Auth)."""
    from hx.install import TOKEN_WAIT_EXIT

    root = tmp_path / "instance"
    result = run_hx("install", "--root", str(root), env_extra=fake_claude_on_path)
    assert result.returncode == TOKEN_WAIT_EXIT == 4
    assert "claude setup-token" in result.stdout
    assert str(root / "seed" / "token") in result.stdout
    assert (root / "config" / "models.json").is_file(), "steps 1 and 2 still ran"


def test_cli_creates_a_scratch_root(run_hx, tmp_path):
    root = tmp_path / "instance"
    result = run_hx("install", "--skeleton-only", "--root", str(root))
    assert result.returncode == 0, result.stderr
    assert f"instance at {root}" in result.stdout
    assert (root / "adapters" / "claude" / "start.sh").is_file()


def test_config_hx_json_records_the_entry_points(tmp_path):
    """CONTRACTS.md `config/hx.json`: absolute `hx_bin`, `hook_bin`, `python_bin`."""
    import json as _json
    import os

    root = tmp_path / "instance"
    install_skeleton(root)
    recorded = _json.loads((root / "config" / "hx.json").read_text())
    assert set(recorded) == {"hx_bin", "hook_bin", "python_bin"}
    for key, value in recorded.items():
        assert value, f"{key} is empty"
        assert os.path.isabs(value), f"{key} is not absolute: {value}"
        assert os.access(value, os.X_OK), f"{key} is not executable: {value}"


def test_doctor_fails_when_an_entry_point_is_missing(tmp_path):
    import json as _json

    from hx.doctor import FAIL, run_checks

    root = tmp_path / "instance"
    install_skeleton(root)
    recorded = _json.loads((root / "config" / "hx.json").read_text())
    recorded["hook_bin"] = str(tmp_path / "gone" / "hx-hook")
    (root / "config" / "hx.json").write_text(_json.dumps(recorded))
    failures = [c for c in run_checks(root) if c[0] == FAIL]
    assert any("hook_bin" in detail for _, _, detail in failures), failures


def test_cli_honours_harness_root_env(run_hx, tmp_path):
    root = tmp_path / "from-env"
    result = run_hx("install", "--skeleton-only", env_extra={"HARNESS_ROOT": str(root)})
    assert result.returncode == 0, result.stderr
    assert (root / "config" / "models.json").is_file()
