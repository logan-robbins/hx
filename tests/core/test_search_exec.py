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


@pytest.mark.parametrize('language,code', [
    ('shell', 'cat source.py'),
    ('shell', 'printf before; head -30 source.py'),
    ('shell', "bash -lc 'git show HEAD:source.py'"),
    ('python', 'from pathlib import Path; print(Path("source.py").read_text())'),
    ('python', 'import sys; sys.stderr.write(open("source.py").read())'),
    ('javascript', 'console.log(require("fs").readFileSync("source.py", "utf8"))'),
])
def test_recognized_source_dump_is_refused_before_isolated_execution(tmp_path, monkeypatch, language, code):
    monkeypatch.setattr(search_exec, '_run', lambda *a, **k: pytest.fail('source dump executed'))
    result = search_exec.execute(tmp_path, language, code)
    assert result['exit_code'] is None
    assert 'complete-file' in result['error']
    assert result['bytes_produced'] == 0
    assert not result['stdout_returned']


@pytest.mark.parametrize('language,code', [
    ('python', 'print(FILE_CONTENT, end="")'),
    ('python', 'print(FILE_CONTENT[:100])'),
    ('python', 'copy = FILE_CONTENT\nprint(copy)'),
    ('python', 'import sys; sys.stdout.write(FILE_CONTENT)'),
    ('python', 'print(FILE_CONTENT.splitlines())'),
    ('python', 'print(str(FILE_CONTENT))'),
    ('javascript', 'console.log(FILE_CONTENT)'),
    ('javascript', 'process.stdout.write(FILE_CONTENT.slice(0, 100))'),
    ('shell', 'printf "%s" "$FILE_CONTENT"'),
])
def test_bound_source_dump_cannot_be_reduced_or_paginated(tmp_path, monkeypatch, language, code):
    source = tmp_path / 'source.py'
    source.write_text('prefix\n' * 1500 + 'def EXISTING_TAIL_CAPABILITY(): pass\n')
    monkeypatch.setattr(search_exec, '_run', lambda *a, **k: pytest.fail('source dump executed'))
    result = search_exec.execute_file(tmp_path, source, language, code, intent='prefix')
    assert result['exit_code'] is None
    assert 'complete-file' in result['error']
    assert result['bytes_produced'] == 0
    assert not result['stdout_returned']


def test_background_source_dump_is_refused_before_process_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(search_exec.subprocess, 'Popen', lambda *a, **k: pytest.fail('source dump launched'))
    result = search_exec.execute(tmp_path, 'shell', 'cat source.py', background=True)
    assert 'complete-file' in result['error']


def test_batch_refuses_source_dump_and_keeps_nonfile_commands(tmp_path):
    result = search_exec.batch_execute(tmp_path, [
        {'label': 'source', 'command': 'cat source.py'},
        {'label': 'computation', 'command': 'printf 42'},
    ], concurrency=2)
    source, computation = result['results']
    assert 'complete-file' in source['error']
    assert source['exit_code'] is None
    assert not source['indexed']
    assert computation['exit_code'] == 0
    assert computation['summary'] == '42'


def test_batch_cli_reports_file_dump_denial_as_failure(tmp_path, capsys):
    import json
    commands = tmp_path / 'commands.json'
    commands.write_text(json.dumps([{'command': 'cat source.py'}]))
    assert search_exec.main(['batch', '--file', str(commands)], tmp_path) == 1
    assert 'complete-file' in capsys.readouterr().out


def test_direct_file_computation_retains_isolated_execution(tmp_path):
    source = tmp_path / 'source.py'
    source.write_text('secret source text')
    result = search_exec.execute(tmp_path, 'python',
        f'from pathlib import Path; print(len(Path({str(source)!r}).read_text()))')
    assert result['exit_code'] == 0
    assert result['stdout_returned'].strip() == str(source.stat().st_size)


def test_bound_javascript_file_length_retains_isolated_execution(tmp_path):
    if not shutil.which('node'):
        pytest.skip('node not available')
    source = tmp_path / 'source.py'
    source.write_text('secret source text')
    result = search_exec.execute_file(tmp_path, source, 'javascript', 'console.log(FILE_CONTENT.length)')
    assert result['exit_code'] == 0
    assert result['stdout_returned'].strip() == str(source.stat().st_size)
