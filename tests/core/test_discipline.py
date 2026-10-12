"""Discipline: repo-derived size bounds, orientation load, committable checks."""
from __future__ import annotations

import subprocess
from pathlib import Path

from hx import discipline


def _repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    for i, n in enumerate([50, 100, 150, 200, 250, 300, 400]):
        (src / f"m{i}.py").write_text("\n".join(f"x{i}_{j} = {j}" for j in range(n)) + "\n")
    return tmp_path


def test_profile_bounds_come_from_the_repo(tmp_path):
    result = discipline.profile(_repo(tmp_path))
    assert result["modules"] == 7
    assert result["median_lines"] == 200
    assert result["module_soft"] <= result["module_hard"]
    assert result["oversized"] == []  # nothing above hard bound in fixture


def test_profile_flags_oversized_modules(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src" / "huge.py").write_text("\n".join("y = 1" for _ in range(5000)) + "\n")
    result = discipline.profile(repo)
    assert "huge.py" in result["oversized"]


def test_orient_narrow_goal(tmp_path):
    result = discipline.orient(tmp_path, "fix the login timeout in src/auth.py")
    assert result["orientation"] in {"easy", "normal"}
    assert result["signals"]["named_files"] == 1


def test_orient_broad_goal_recommends_split(tmp_path):
    goal = ("rewrite auth, billing, notifications, reporting, and admin in "
            "src/a.py src/b.py src/c.py src/d.py src/e.py with new APIs and migrations")
    result = discipline.orient(tmp_path, goal)
    assert result["orientation"] == "broad"
    assert result["recommendation"] == "split"


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "base"], cwd=repo, check=True)
    return repo


def test_commit_check_small_change_with_tests(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "src" / "a.py").write_text("x = 2\n")
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n\ndef test_b():\n    assert True\n")
    result = discipline.commit_check(repo)
    assert result["files"] == 2
    assert result["has_tests"] is True
    assert result["verdict"] == "committable"


def test_commit_check_many_modules_needs_split(tmp_path):
    repo = _git_repo(tmp_path)
    for i in range(8):
        (repo / "src" / f"m{i}.py").write_text("y = 1\n")
    result = discipline.commit_check(repo)
    assert result["verdict"] == "split"
    assert any("module" in r for r in result["reasons"])
