"""Isolated execution: only stdout returns, large output is indexed not dumped."""

from __future__ import annotations

import shutil
import time

import pytest

from hx import search_exec


def test_python_execute_returns_only_stdout(tmp_path):
    result = search_exec.execute(
        tmp_path, "python",
        "import sys; print('hello'); print('noise', file=sys.stderr)")
    assert result["exit_code"] == 0
    assert result["stdout_returned"].strip() == "hello"
    assert "noise" not in result["stdout_returned"]
    assert "stderr" not in result
    assert result["bytes_produced"] > 0
    assert result["indexed"] is False


def test_shell_execute(tmp_path):
    result = search_exec.execute(tmp_path, "shell", "echo from-shell")
    assert result["exit_code"] == 0
    assert "from-shell" in result["stdout_returned"]


def test_large_output_with_intent_returns_indexed_sections(tmp_path):
    code = (
        "for i in range(200):\n"
        "    print(f'line {i} ' + 'filler ' * 6)\n"
        "print('UNIQUE_MARKER needle line')\n"
    )
    result = search_exec.execute(tmp_path, "python", code, intent="UNIQUE_MARKER")
    assert result["bytes_produced"] > 5000
    assert result["indexed"] is True
    assert "UNIQUE_MARKER" in result["stdout_returned"]
    assert "Indexed" in result["stdout_returned"]
    assert len(result["stdout_returned"]) < result["bytes_produced"]
    assert len(result["stdout_returned"]) <= 6100


def test_large_output_without_intent_truncates(tmp_path):
    code = "for i in range(200):\n    print(f'line {i} ' + 'filler ' * 6)\n"
    result = search_exec.execute(tmp_path, "python", code)
    assert result["bytes_produced"] > 5000
    assert "truncated" in result["stdout_returned"]
    assert len(result["stdout_returned"]) <= 6100


def test_huge_output_returns_pointer_only(tmp_path):
    code = "for i in range(900):\n    print('x' * 200)\n"
    result = search_exec.execute(tmp_path, "python", code, intent="x")
    assert result["bytes_produced"] > 102400
    assert result["indexed"] is True
    assert "Indexed" in result["stdout_returned"]
    assert len(result["stdout_returned"]) < 1000
    assert result["stdout_returned"].count("x") < 200


def test_execute_file_counts_lines_without_returning_content(tmp_path):
    data = tmp_path / "data.txt"
    secret = "SECRET_LINE_CONTENT"
    data.write_text("\n".join(f"{secret} {i}" for i in range(42)), encoding="utf-8")
    result = search_exec.execute_file(
        tmp_path, data, "python", "print(len(FILE_CONTENT.splitlines()))")
    assert result["exit_code"] == 0
    assert result["stdout_returned"].strip() == "42"
    assert secret not in result["stdout_returned"]


def test_timeout_kills(tmp_path):
    start = time.monotonic()
    result = search_exec.execute(
        tmp_path, "python", "import time; time.sleep(30)", timeout=1)
    elapsed = time.monotonic() - start
    assert elapsed < 10
    assert result["timed_out"] is True
    assert result["exit_code"] != 0


def test_failure_includes_stderr(tmp_path):
    result = search_exec.execute(
        tmp_path, "python", "import sys; sys.stderr.write('boom'); sys.exit(3)")
    assert result["exit_code"] == 3
    assert "boom" in result["stderr"]


def test_batch_two_commands_with_query(tmp_path):
    result = search_exec.batch_execute(
        tmp_path,
        [{"label": "a", "command": "printf 'apple banana\\ncherry\\n'"},
         {"label": "b", "command": "printf 'dog\\n'"}],
        queries=["banana"])
    by_label = {r["label"]: r for r in result["results"]}
    assert by_label["a"]["exit_code"] == 0
    assert by_label["b"]["exit_code"] == 0
    hits = result["search"]["banana"]
    assert hits and any("banana" in h["section"] for h in hits)
    assert all(len(r["summary"]) <= 500 for r in result["results"])


def test_javascript_execute_or_skip(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node not available")
    result = search_exec.execute(tmp_path, "javascript", "console.log('js-hi')")
    assert result["exit_code"] == 0
    assert "js-hi" in result["stdout_returned"]
