from __future__ import annotations

import subprocess

import pytest

from hx import code_index, file_limits
from hx.errors import ValidationError


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_whole_file_boundary_counts_utf8_bytes_and_never_returns_prefix(tmp_path):
    target = tmp_path / "instruction.md"
    target.write_bytes(b"a" * file_limits.MAX_FILE_BYTES)
    assert len(file_limits.read_file(target)) == file_limits.MAX_FILE_BYTES
    assert file_limits.validate_file(target)["estimated_tokens"] == 10_000
    target.write_bytes(b"a" * file_limits.MAX_FILE_BYTES + b"z")
    with pytest.raises(ValidationError, match="partial reads are prohibited"):
        file_limits.read_file(target)
    assert file_limits.file_record("界".encode() * 13_334)["oversized"]


def test_audit_lists_every_legacy_outlier_without_following_external_symlinks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text("ignored/\n")
    for number in range(105):
        (repo / f"{number:03}.py").write_text("#" * 40_001)
    (repo / "binary.dat").write_bytes(b"\0" + b"x" * 50_000)
    (repo / "ignored").mkdir()
    (repo / "ignored/hidden.py").write_text("x" * 50_000)
    external = tmp_path / "external.py"
    external.write_text("x" * 50_000)
    (repo / "external.py").symlink_to(external)
    audit = file_limits.scan_repo(repo)
    assert audit["complete"] and not audit["clean"]
    assert len(audit["outliers"]) == 105
    assert audit["outliers"][0]["path"] == "000.py"
    assert audit["outliers"][-1]["path"] == "104.py"
    assert audit["binary_paths"] == ["binary.dat"]
    assert audit == file_limits.scan_repo(repo)
    with pytest.raises(ValidationError, match="000.py.*104.py"):
        file_limits.require_changed(repo, ["."])
    assert file_limits.require_changed(repo, ["external.py"])["clean"]
    assert file_limits.require_changed(repo, [])["files_scanned"] == 0


def test_index_keeps_tail_symbols_of_legacy_outlier_and_reports_on_scoped_refresh(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = "#" + "p" * 40_000 + "\n\ndef existing_at_end():\n return 1\n"
    (repo / "legacy.py").write_text(source)
    (repo / "small.py").write_text("small = True\n")
    root = tmp_path / "state"
    result = code_index.index_repo(root, repo)
    assert [row["path"] for row in result["file_sizes"]["outliers"]] == ["legacy.py"]
    assert code_index.symbols_for(root, "existing_at_end")
    assert code_index.index_repo(root, repo, paths=["small.py"])["file_sizes"]["outliers"] == result["file_sizes"]["outliers"]
    status, audit = code_index.execute(["audit", "--repo", str(repo)], root)
    assert status == 3 and audit["outliers"] == result["file_sizes"]["outliers"]


def test_explicit_index_budget_fails_instead_of_certifying_missing_tail(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "large.py").write_text("#" * 1_000 + "\ndef Hidden(): return 1\n")
    root = tmp_path / "state"
    with pytest.raises(ValueError, match="index aborted without publishing partial coverage"):
        code_index.index_repo(root, repo, max_file_bytes=128)
    assert code_index.symbols_for(root, "Hidden") == []
    result = code_index.index_repo(root, repo)
    assert result["skipped"] == 0
    assert code_index.symbols_for(root, "Hidden")


def test_historical_audit_pins_all_outliers_and_full_source_tail(tmp_path):
    from hx.code_build import index_revision
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "legacy.py").write_text("#" * 40_001 + "\ndef historical_tail(): return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "complete legacy source")
    root = tmp_path / "state"
    with pytest.raises(ValueError, match="index aborted"):
        index_revision(root, repo, "HEAD", max_file_bytes=128)
    captured = index_revision(root, repo, "HEAD")
    assert captured["file_sizes"]["outliers"][0]["path"] == "legacy.py"
    assert captured["file_sizes"]["commit"] == captured["head"]
    assert code_index.symbols_for(root, "historical_tail", at=captured["snapshot"])
    assert index_revision(root, repo, "HEAD")["file_sizes"] == captured["file_sizes"]
