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
    for entry in ("pods/", "logs/", "state/", "run/", "orders/", "tasks.json"):
        assert entry in ignored


def test_install_is_idempotent_and_never_overwrites(tmp_path):
    root = tmp_path / "instance"
    install_skeleton(root)
    edited = root / "config" / "models.json"
    edited.write_text(json.dumps({"claude-opus-5": {"window": 400000, "threshold": 100000}}))
    result = install_skeleton(root)
    assert json.loads(edited.read_text())["claude-opus-5"]["window"] == 400000
    assert "config/models.json" in result["skipped"]
    assert result["created"] == []


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


def test_cli_requires_skeleton_only_for_now(run_hx, tmp_path):
    root = tmp_path / "instance"
    result = run_hx("install", "--root", str(root))
    assert result.returncode == 2
    assert "not implemented (build-11)" in result.stderr
    assert "--skeleton-only" in result.stderr
    assert not root.exists()


def test_cli_creates_a_scratch_root(run_hx, tmp_path):
    root = tmp_path / "instance"
    result = run_hx("install", "--skeleton-only", "--root", str(root))
    assert result.returncode == 0, result.stderr
    assert f"root {root}" in result.stdout
    assert (root / "adapters" / "claude" / "start.sh").is_file()


def test_cli_honours_harness_root_env(run_hx, tmp_path):
    root = tmp_path / "from-env"
    result = run_hx("install", "--skeleton-only", env_extra={"HARNESS_ROOT": str(root)})
    assert result.returncode == 0, result.stderr
    assert (root / "config" / "models.json").is_file()
