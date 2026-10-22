"""Discipline: repo-derived size bounds, orientation load, committable checks."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hx import discipline, streams
from hx.errors import ValidationError


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


def test_commit_range_small_unit_is_committable(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "src" / "a.py").write_text("x = 2\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "one behaviour"], check=True)
    result = discipline.commit_range(repo)
    assert result["commits"] == 1
    assert result["verdict"] in {"committable", "finish-slice"}


def test_commit_range_many_modules_is_split(tmp_path):
    repo = _git_repo(tmp_path)
    for i in range(8):
        (repo / "src" / f"big{i}.py").write_text("\n".join("y = 1" for _ in range(50)) + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "everything at once"], check=True)
    result = discipline.commit_range(repo)
    assert result["verdict"] == "split"
    gate = discipline.granularity_gate(repo)
    assert gate["allowed"] is False
    assert "split" in gate["instruction"]


# --- hx discipline pull ------------------------------------------------------------------------

FORK_DATE = "2026-01-01T00:00:00Z"


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = None
    if date is not None:
        import os
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True, env=env).stdout


def _two_branch_repo(tmp_path: Path, *, principal: str = "main") -> Path:
    """`principal` with a base commit, `feature` forked from it with one commit, then two
    more commits on `principal` (src/b.py added, then src/a.py changed); HEAD is `feature`."""
    repo = tmp_path / "two"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("x = 1\n")
    _git(tmp_path, "init", "-q", "-b", principal, str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base", date=FORK_DATE)
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "src" / "c.py").write_text("z = 3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature work")
    _git(repo, "checkout", "-q", principal)
    (repo / "src" / "b.py").write_text("y = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "principal adds b")
    (repo / "src" / "a.py").write_text("x = 2\n")
    _git(repo, "commit", "-qam", "principal changes a")
    _git(repo, "checkout", "-q", "feature")
    return repo


def test_pull_reports_commits_behind_and_the_files_the_base_changed(tmp_path):
    repo = _two_branch_repo(tmp_path)
    result = discipline.pull(repo)
    assert result["base"] == "main"
    assert (result["behind"], result["ahead"]) == (2, 1)
    assert sorted(result["diverged_files"]) == ["src/a.py", "src/b.py"]
    assert result["fresh"] is False
    assert result["base_age_s"] >= 0
    import time
    from hx import timestamps
    fork = timestamps.parse(FORK_DATE).timestamp()
    assert abs(result["base_age_s"] - (time.time() - fork)) < 120
    assert set(result) == {"base", "base_sha", "merge_base", "behind", "ahead",
                           "diverged_files", "base_age_s", "fresh", "instruction"}
    assert "2 commit(s) behind" in result["instruction"]


def test_pull_prefers_dev_over_main(tmp_path):
    repo = _two_branch_repo(tmp_path, principal="dev")
    result = discipline.pull(repo)
    assert result["base"] == "dev" and result["behind"] == 2


def test_pull_expect_fresh_exits_3_when_behind(tmp_path, capsys):
    repo = _two_branch_repo(tmp_path)
    code = discipline.main(["pull", "--repo", str(repo), "--expect", "fresh"], tmp_path)
    captured = capsys.readouterr()
    assert code == 3
    assert json.loads(captured.out)["behind"] == 2
    assert "--expect fresh" in captured.err and "pull main" in captured.err


def test_pull_expect_fresh_passes_after_merging_the_base(tmp_path, capsys):
    repo = _two_branch_repo(tmp_path)
    _git(repo, "merge", "-q", "--no-edit", "main")
    code = discipline.main(["pull", "--repo", str(repo), "--expect", "fresh"], tmp_path)
    doc = json.loads(capsys.readouterr().out)
    assert code == 0
    assert (doc["behind"], doc["diverged_files"], doc["fresh"]) == (0, [], True)
    assert doc["instruction"] == "fresh"


def test_pull_without_expect_reports_and_exits_0(tmp_path, capsys):
    repo = _two_branch_repo(tmp_path)
    assert discipline.main(["pull", "--repo", str(repo)], tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["behind"] == 2


def test_pull_without_a_principal_branch_is_a_validation_error(tmp_path):
    repo = tmp_path / "lone"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _git(tmp_path, "init", "-q", "-b", "trunk", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "only")
    with pytest.raises(ValidationError, match="principal branch"):
        discipline.pull(repo)


def test_pull_help_carries_an_example(capsys):
    with pytest.raises(SystemExit):
        discipline.main(["pull", "--help"], Path("."))
    assert "example: hx discipline pull --repo . --expect fresh" in capsys.readouterr().out


# --- hx discipline orient --post-seam ID ---------------------------------------------------------


def _tool(tool: str, **data) -> dict:
    return {"event": "post_tool", "tool": tool, "input": json.dumps(data), "output": "",
            "ts": "2026-09-20T12:51:00Z"}


def _write_stream(root: Path, item_id: str, records: list[dict]) -> Path:
    path = streams.main_stream(root, item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for seq, record in enumerate(records, start=1):
            handle.write(json.dumps({"seq": seq, **record}) + "\n")
    return path


SEAM = {"event": "seam", "ts": "2026-09-20T12:50:00Z", "source": "clear",
        "context_file_bytes": 4200}

POST_SEAM = [
    _tool("Read", file_path="/w/pods/engineers/PARTNER.md"),
    _tool("Bash", command="hx task"),
    _tool("Grep", pattern="def pull"),
    _tool("Read", file_path="/w/src/a.py"),
    _tool("Bash", command="cat src/b.py src/c.py | wc -l"),
    _tool("Bash", command="hx code search pull"),
    _tool("Read", file_path="/w/src/a.py"),
    _tool("Edit", file_path="/w/src/a.py"),
    _tool("Grep", pattern="after the edit"),
    _tool("Read", file_path="/w/src/d.py"),
    _tool("Bash", command=".venv/bin/hx task"),
]


def test_post_seam_counts_orientation_after_the_last_seam(tmp_path):
    before = [_tool("Grep", pattern="before the seam"), _tool("Bash", command="hx task")]
    _write_stream(tmp_path, "partner", before + [SEAM] + POST_SEAM)
    doc = discipline.post_seam(tmp_path, "partner")
    assert doc["id"] == "partner" and doc["seam_seq"] == 3 and doc["source"] == "clear"
    assert doc["tool_calls"] == len(POST_SEAM)
    assert doc["searches_before_first_edit"] == 2      # Grep + hx code search; not the later one
    assert doc["modules_read_before_first_write"] == 3  # a, b, c; the context file is not one
    assert doc["task_reprints"] == 2                    # both `hx task` calls after the seam
    assert doc["first_edit_seq"] == 3 + 8


def test_post_seam_uses_only_the_last_seam(tmp_path):
    _write_stream(tmp_path, "partner", [SEAM] + POST_SEAM + [SEAM, _tool("Edit", file_path="x")])
    doc = discipline.post_seam(tmp_path, "partner")
    assert (doc["tool_calls"], doc["searches_before_first_edit"], doc["task_reprints"]) == (1, 0, 0)


def test_post_seam_without_a_seam_is_a_validation_error(tmp_path):
    _write_stream(tmp_path, "partner", POST_SEAM)
    with pytest.raises(ValidationError, match="no seam"):
        discipline.post_seam(tmp_path, "partner")


def test_orient_post_seam_cli_prints_the_document(tmp_path, capsys):
    _write_stream(tmp_path, "partner", [SEAM] + POST_SEAM)
    assert discipline.main(["orient", "--post-seam", "partner"], tmp_path) == 0
    doc = json.loads(capsys.readouterr().out)
    assert {"searches_before_first_edit", "modules_read_before_first_write",
            "task_reprints"} <= set(doc)


def test_classify_call_reads_codex_style_shell_and_truncated_inputs():
    assert discipline.classify_call({"tool": "exec_command",
                                     "input": json.dumps({"cmd": ["bash", "-lc", "rg pull src"]})})[0] == "search"
    truncated = {"tool": "Bash", "input": '{"command": "sed -i s/a/b/ src/a.py && echo done', "output": ""}
    assert discipline.classify_call(truncated)[0] == "edit"
    assert discipline.classify_call({"tool": "Bash", "input": json.dumps({"command": "ls"})}) == ("other", [])


def test_post_seam_refuses_worker_file_stream(tmp_path):
    _write_stream(tmp_path, "eng-001", [SEAM] + POST_SEAM)
    with pytest.raises(ValidationError, match="ledger-owned"):
        discipline.post_seam(tmp_path, "eng-001")
