"""`hx handoff` — what a handoff carries before another session acts on it (docs/multi-instance.md §6).

`check` is exercised on a synthetic handoff directory that meets the contract and on the same
directory broken one clause at a time; `capture` runs against a fake `gh` on PATH; `pushed`
against a real scratch git repository; `sums` seals and `check` verifies.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hx import handoff

HEAD = "a" * 40
OLD_HEAD = "b" * 40


def run_handoff(*args: str, cwd: Path | None = None, path_extra: str | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "HX_"))}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    if path_extra:
        env["PATH"] = f"{path_extra}:{env.get('PATH', '')}"
    return subprocess.run([sys.executable, "-m", "hx", "handoff", *args], env=env,
                          capture_output=True, text=True, cwd=str(cwd) if cwd else None)


def good_handoff(status: str = "CI_PENDING", rows: str | None = None) -> str:
    rows = rows if rows is not None else f"| #1885 | {HEAD} | OPEN | BLOCKED | REVIEW_REQUIRED |"
    return f"""# Handoff

Authority: local evidence index, not merge approval. Captured 2026-10-11T18:00:00Z.

Delivery status: {status}

| PR | head | state | merge | review |
|---|---|---|---|---|
{rows}

## Open findings

- none.
"""


@pytest.fixture
def handoff_dir(tmp_path):
    directory = tmp_path / "handoff"
    directory.mkdir()
    (directory / "HANDOFF.md").write_text(good_handoff())
    (directory / "publication.json").write_text(json.dumps({
        "pushed_at": "2026-10-11T17:00:00+00:00", "head": HEAD, "evidenced": HEAD,
        "product_paths": ["app"], "excluded": ["app/tests"], "production_delta": [],
    }))
    (directory / "final-root-check.json").write_text(json.dumps({
        "captured_at": "2026-10-11T18:00:00+00:00", "repository": "o/r",
        "prs": [{"number": 1885, "state": "OPEN", "headRefOid": HEAD, "mergeStateStatus": "BLOCKED",
                 "reviewDecision": "REVIEW_REQUIRED",
                 "statusCheckRollup": [{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None,
                                        "name": "units"}]}],
    }))
    assert handoff.main(["sums", str(directory)]) == 0
    return directory


def check(directory: Path, capsys) -> tuple[int, str]:
    code = handoff.main(["check", str(directory)])
    return code, capsys.readouterr().out


def test_a_handoff_that_meets_the_contract_passes(handoff_dir, capsys):
    code, out = check(handoff_dir, capsys)
    assert code == 0, out
    assert "RESULT: PASS" in out and "checksums verify" in out


def reseal(directory: Path) -> None:
    assert handoff.main(["sums", str(directory)]) == 0


def test_the_authority_line_and_capture_time_are_required(handoff_dir, capsys):
    (handoff_dir / "HANDOFF.md").write_text(good_handoff().replace(
        "Authority: local evidence index, not merge approval. Captured 2026-10-11T18:00:00Z.", "Hello."))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "S90 authority" in out and "S90 capture time" in out


def test_a_capture_older_than_the_last_push_fails(handoff_dir, capsys):
    record = json.loads((handoff_dir / "publication.json").read_text())
    record["pushed_at"] = "2026-10-11T19:00:00+00:00"
    (handoff_dir / "publication.json").write_text(json.dumps(record))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "is not later than the last push" in out


def test_a_row_with_another_head_must_say_historical(handoff_dir, capsys):
    rows = f"| #1885 | {OLD_HEAD} → {HEAD} | OPEN | BLOCKED | REVIEW_REQUIRED |"
    (handoff_dir / "HANDOFF.md").write_text(good_handoff(rows=rows))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "without the table header or row saying 'historical'" in out
    (handoff_dir / "HANDOFF.md").write_text(good_handoff(rows=rows + " historical head first |"))
    reseal(handoff_dir)
    assert check(handoff_dir, capsys)[0] == 0


def test_a_row_without_the_captured_head_fails(handoff_dir, capsys):
    (handoff_dir / "HANDOFF.md").write_text(good_handoff(rows="| #1885 | (see above) | OPEN | BLOCKED | REVIEW_REQUIRED |"))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "does not carry the captured head" in out


def test_blocked_needs_its_cause(handoff_dir, capsys):
    (handoff_dir / "HANDOFF.md").write_text(good_handoff(rows=f"| #1885 | {HEAD} | OPEN | BLOCKED | - |"))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "is BLOCKED; the handoff must name the cause (REVIEW_REQUIRED)" in out


def test_a_draft_pr_fails(handoff_dir, capsys):
    capture = json.loads((handoff_dir / "final-root-check.json").read_text())
    capture["prs"][0]["isDraft"] = True
    (handoff_dir / "final-root-check.json").write_text(json.dumps(capture))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "S94 PR #1885 is a draft" in out


def test_the_open_section_is_mandatory(handoff_dir, capsys):
    (handoff_dir / "HANDOFF.md").write_text(good_handoff().replace("## Open findings", "## Findings"))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "S91 no 'Open" in out


def test_carry_forward_needs_the_product_delta_and_the_words_not_rerun(handoff_dir, capsys):
    text = good_handoff() + "\nThe later push was test-only; LIVE04 still applies.\n"
    (handoff_dir / "HANDOFF.md").write_text(text)
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "never says the later tree was not rerun" in out
    (handoff_dir / "HANDOFF.md").write_text(text + "The live layer was not rerun on it.\n")
    reseal(handoff_dir)
    assert check(handoff_dir, capsys)[0] == 0


def test_the_delivery_status_must_agree_with_the_captured_checks(handoff_dir, capsys):
    (handoff_dir / "HANDOFF.md").write_text(good_handoff(status="CI_GREEN"))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "S93 Delivery status CI_GREEN but the capture has 0 failed and 1 pending" in out
    capture = json.loads((handoff_dir / "final-root-check.json").read_text())
    capture["prs"][0]["statusCheckRollup"] = [{"__typename": "CheckRun", "status": "COMPLETED",
                                               "conclusion": "FAILURE", "name": "units"}]
    (handoff_dir / "final-root-check.json").write_text(json.dumps(capture))
    (handoff_dir / "HANDOFF.md").write_text(good_handoff(status="LOCAL_GATE_PASSED"))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "say CI_FAILED and name them" in out
    (handoff_dir / "HANDOFF.md").write_text(good_handoff(status="CI_FAILED"))
    reseal(handoff_dir)
    assert check(handoff_dir, capsys)[0] == 0
    (handoff_dir / "HANDOFF.md").write_text(good_handoff().replace("Delivery status: CI_PENDING\n", ""))
    reseal(handoff_dir)
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "S93 no `Delivery status:" in out


def test_a_changed_file_breaks_the_seal(handoff_dir, capsys):
    (handoff_dir / "HANDOFF.md").write_text(good_handoff() + "\nedited after sealing\n")
    code, out = check(handoff_dir, capsys)
    assert code == 1 and "does not verify: ./HANDOFF.md: changed" in out


def test_capture_writes_one_query_per_pr(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    gh = fake / "gh"
    gh.write_text(f"""#!/usr/bin/env python3
import json, sys
number = int(sys.argv[3])
print(json.dumps({{"number": number, "state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
    "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED", "headRefOid": "{HEAD}", "baseRefOid": "{OLD_HEAD}",
    "statusCheckRollup": [{{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS", "name": "units"}}]}}))
""")
    gh.chmod(0o755)
    out_dir = tmp_path / "evidence"
    before = dt.datetime.now(dt.timezone.utc)
    result = run_handoff("capture", str(out_dir), "--repo", "o/r", "--pr", "1885", "1910", path_extra=str(fake))
    assert result.returncode == 0, result.stderr
    capture = json.loads((out_dir / "final-root-check.json").read_text())
    assert [pr["number"] for pr in capture["prs"]] == [1885, 1910]
    assert handoff.parse_iso(capture["captured_at"]) >= before.replace(microsecond=0)
    assert "#1885 head aaaaaaaaaaaa OPEN CLEAN APPROVED checks" in result.stdout


def test_pushed_records_the_product_delta_without_test_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    for rel in ("app/src/A.cs", "app/tests/ATests.cs", "docs/x.md"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("v1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    evidenced = git("rev-parse", "HEAD").stdout.strip()
    (repo / "app/tests/ATests.cs").write_text("v2\n")
    git("commit", "-q", "-am", "tests only")
    test_only = git("rev-parse", "HEAD").stdout.strip()
    (repo / "app/src/A.cs").write_text("v2\n")
    git("commit", "-q", "-am", "product")
    product = git("rev-parse", "HEAD").stdout.strip()

    out_dir = tmp_path / "ev"
    assert handoff.main(["pushed", str(out_dir), "--repo-dir", str(repo), "--head", test_only,
                         "--evidenced", evidenced]) == 0
    assert json.loads((out_dir / "publication.json").read_text())["production_delta"] == []
    assert handoff.main(["pushed", str(out_dir), "--repo-dir", str(repo), "--head", product,
                         "--evidenced", evidenced]) == 0
    assert json.loads((out_dir / "publication.json").read_text())["production_delta"] == ["app/src/A.cs"]
    assert handoff.main(["pushed", str(out_dir), "--repo-dir", str(repo), "--head", product]) == 0
    assert "production_delta" not in json.loads((out_dir / "publication.json").read_text())


def test_check_summary_reads_both_rollup_shapes():
    pr = {"statusCheckRollup": [
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"__typename": "CheckRun", "status": "QUEUED", "conclusion": None},
        {"__typename": "StatusContext", "state": "FAILURE"},
        {"__typename": "StatusContext", "state": "PENDING"},
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SKIPPED"},
    ]}
    assert handoff.check_summary(pr) == {"total": 5, "success": 1, "pending": 2, "failed": 1, "skipped": 1}
