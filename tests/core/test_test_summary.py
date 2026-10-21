from __future__ import annotations

import json

import pytest

from hx import test_summary
from hx.errors import ValidationError

# Recorded from `pytest --continue-on-collection-errors --junit-xml=report.xml tests` (pytest 9,
# junit_family xunit2); absolute paths shortened. One collection error, one failure, two passes, one skip.
JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite name="pytest" errors="1" failures="1" skipped="1" tests="5" time="0.070"><testcase classname="" name="tests.test_b" time="0.000"><error message="collection failure">ImportError while importing test module '/work/tests/test_b.py'.
E   ModuleNotFoundError: No module named 'missing_module'</error></testcase><testcase classname="tests.test_a" name="test_one" time="0.001"><failure message="assert 1 == 2">def test_one():
&gt;       assert 1 == 2
E       assert 1 == 2

tests/test_a.py:4: AssertionError</failure></testcase><testcase classname="tests.test_a" name="test_two" time="0.002" /><testcase classname="tests.test_a" name="test_three" time="0.000" /><testcase classname="tests.test_a" name="test_four" time="0.000"><skipped type="pytest.skip" message="later">/work/tests/test_a.py:12: later</skipped></testcase></testsuite></testsuites>
"""

# `dotnet test --logger "trx;LogFileName=…"`: VSTest TeamTest/2010 schema, outcome per UnitTestResult.
TRX = """<?xml version="1.0" encoding="utf-8"?>
<TestRun id="5c1f" name="ci 2026-10-20" xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
    <UnitTestResult executionId="a" testId="1" testName="Calc.Tests.AddTests.AddsNegative" outcome="Failed">
      <Output><ErrorInfo><Message>Assert.Equal() Failure</Message></ErrorInfo></Output>
    </UnitTestResult>
    <UnitTestResult executionId="b" testId="2" testName="Calc.Tests.AddTests.AddsPositive" outcome="Passed" />
    <UnitTestResult executionId="c" testId="3" testName="Calc.Tests.AddTests.AddsZero" outcome="Passed" />
    <UnitTestResult executionId="d" testId="4" testName="Calc.Tests.AddTests.Overflows" outcome="NotExecuted" />
    <UnitTestResult executionId="e" testId="5" testName="Calc.Tests.AddTests.Hangs" outcome="Timeout" />
  </Results>
  <ResultSummary outcome="Failed"><Counters total="5" executed="4" passed="2" failed="1" timeout="1" notExecuted="1" /></ResultSummary>
</TestRun>
"""


def lines(*events):
    return "".join(json.dumps(event) + "\n" for event in events)


# `go test -json ./...` (test2json): one event per line; a build failure names FailedBuild.
GO = lines(
    {"Action": "start", "Package": "example.com/calc"},
    {"Action": "run", "Package": "example.com/calc", "Test": "TestAdd"},
    {"Action": "output", "Package": "example.com/calc", "Test": "TestAdd", "Output": "    add_test.go:9: expected 3, got 4\n"},
    {"Action": "run", "Package": "example.com/calc", "Test": "TestAdd/negative"},
    {"Action": "fail", "Package": "example.com/calc", "Test": "TestAdd/negative", "Elapsed": 0},
    {"Action": "fail", "Package": "example.com/calc", "Test": "TestAdd", "Elapsed": 0},
    {"Action": "pass", "Package": "example.com/calc", "Test": "TestSub", "Elapsed": 0},
    {"Action": "skip", "Package": "example.com/calc", "Test": "TestDiv", "Elapsed": 0},
    {"Action": "fail", "Package": "example.com/calc", "Elapsed": 0.01},
    {"ImportPath": "example.com/broken", "Action": "build-output", "Output": "broken.go:3:1: syntax error\n"},
    {"ImportPath": "example.com/broken", "Action": "build-fail"},
    {"Action": "start", "Package": "example.com/broken"},
    {"Action": "output", "Package": "example.com/broken", "Output": "FAIL\texample.com/broken [build failed]\n"},
    {"Action": "fail", "Package": "example.com/broken", "Elapsed": 0, "FailedBuild": "example.com/broken"},
) + "FAIL\n"

# `cargo test --message-format=json -- -Z unstable-options --format json`: cargo build messages
# and libtest events share stdout; stderr progress lines are not JSON.
CARGO = "   Compiling calc v0.1.0\n" + lines(
    {"reason": "compiler-artifact", "target": {"name": "calc"}, "fresh": False},
    {"reason": "build-finished", "success": True},
    {"type": "suite", "event": "started", "test_count": 3},
    {"type": "test", "event": "started", "name": "tests::adds"},
    {"type": "test", "event": "ok", "name": "tests::adds"},
    {"type": "test", "event": "failed", "name": "tests::subtracts", "stdout": "assertion `left == right` failed\n"},
    {"type": "test", "event": "ignored", "name": "tests::slow"},
    {"type": "suite", "event": "failed", "passed": 1, "failed": 1, "ignored": 1, "measured": 0, "filtered_out": 0},
)

CARGO_BUILD_ERROR = lines(
    {"reason": "compiler-message", "target": {"name": "calc"}, "message": {"level": "error", "message": "mismatched types"}},
    {"reason": "compiler-message", "target": {"name": "calc"}, "message": {"level": "warning", "message": "unused variable"}},
    {"reason": "build-finished", "success": False},
)

# `jest --json --outputFile=…` (vitest `--reporter=json` writes the same shape).
JEST = json.dumps({
    "numTotalTests": 4, "numPassedTests": 1, "numFailedTests": 1, "numPendingTests": 1, "numTodoTests": 1,
    "numRuntimeErrorTestSuites": 1, "success": False,
    "testResults": [
        {"name": "/work/src/sum.test.js", "status": "failed", "message": "", "assertionResults": [
            {"ancestorTitles": ["math"], "title": "adds", "fullName": "math adds", "status": "passed"},
            {"ancestorTitles": ["math"], "title": "subtracts", "fullName": "math subtracts", "status": "failed"},
            {"ancestorTitles": ["math"], "title": "divides", "fullName": "math divides", "status": "pending"},
            {"ancestorTitles": [], "title": "later", "fullName": "later", "status": "todo"}]},
        {"name": "/work/src/broken.test.js", "status": "failed", "message": "SyntaxError: Unexpected token",
         "assertionResults": []}]})


def expected(report, passed, failed, skipped, errors, failing, truncated=False):
    return {"format": report, "passed": passed, "failed": failed, "skipped": skipped,
            "errors": errors, "failing": failing, "truncated": truncated}


def parse(tmp_path, report, text):
    path = tmp_path / "report"
    path.write_text(text)
    return test_summary.summarize(report, path)


@pytest.mark.parametrize("report,text,summary", [
    ("junit-xml", JUNIT, expected("junit-xml", 2, 1, 1, 1, ["tests.test_b", "tests.test_a::test_one"])),
    ("trx", TRX, expected("trx", 2, 1, 1, 1, ["Calc.Tests.AddTests.AddsNegative", "Calc.Tests.AddTests.Hangs"])),
    ("go-json", GO, expected("go-json", 1, 2, 1, 1, ["TestAdd/negative", "TestAdd", "example.com/broken [build failed]"])),
    ("cargo-json", CARGO, expected("cargo-json", 1, 1, 1, 0, ["tests::subtracts"])),
    ("cargo-json", CARGO_BUILD_ERROR, expected("cargo-json", 0, 0, 0, 1, ["calc [build failed]"])),
    ("jest-json", JEST, expected("jest-json", 1, 1, 2, 1, ["math › subtracts", "/work/src/broken.test.js"])),
    ("jest-json", "Determining test suites...\n" + JEST + "\n", expected("jest-json", 1, 1, 2, 1,
                                                                         ["math › subtracts", "/work/src/broken.test.js"])),
], ids=["junit", "trx", "go", "cargo", "cargo-build-error", "jest", "jest-on-stdout"])
def test_each_report_format_counts_every_category_and_lists_failing_names(tmp_path, report, text, summary):
    assert parse(tmp_path, report, text) == summary
    assert test_summary.failed(summary)


def test_a_passing_report_is_not_a_failure(tmp_path):
    passing = '<testsuites><testsuite><testcase classname="t" name="a"/><testcase classname="t" name="b"/></testsuite></testsuites>'
    summary = parse(tmp_path, "junit-xml", passing)
    assert summary == expected("junit-xml", 2, 0, 0, 0, [])
    assert not test_summary.failed(summary) and not test_summary.failed(None)


@pytest.mark.parametrize("report,text", [
    ("junit-xml", ""), ("junit-xml", "<testsuites><testcase"), ("junit-xml", "<report/>"),
    ("trx", "not xml"), ("trx", "<testsuites/>"),
    ("go-json", "ok  \texample.com/calc\t0.01s\n"), ("go-json", '{"Package": "p"}\n'),
    ("cargo-json", "test result: ok. 3 passed;\n"), ("cargo-json", '{"name": "calc"}\n'),
    ("jest-json", "Tests: 1 failed, 1 total\n"), ("jest-json", "[1, 2]\n"), ("jest-json", '{"numTotalTests": 0}\n'),
])
def test_text_malformed_or_unrecognised_reports_have_no_summary(tmp_path, report, text):
    assert parse(tmp_path, report, text) is None


# Each runner's report for a selection that matched nothing.
EMPTY = [
    ("junit-xml", '<testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="0" skipped="0" tests="0"/></testsuites>'),
    ("junit-xml", '<testsuite name="surefire" tests="0"/>'),
    ("trx", '<TestRun id="1" name="ci" xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results/>'
            '<ResultSummary outcome="Completed"><Counters total="0" executed="0"/></ResultSummary></TestRun>'),
    ("go-json", lines({"Action": "start", "Package": "example.com/calc"},
                      {"Action": "output", "Package": "example.com/calc", "Output": "ok  \texample.com/calc\t0.012s [no tests to run]\n"},
                      {"Action": "pass", "Package": "example.com/calc", "Elapsed": 0.012})),
    ("cargo-json", "   Compiling calc v0.1.0\n" + lines(
        {"reason": "compiler-artifact", "target": {"name": "calc"}, "fresh": False},
        {"reason": "build-finished", "success": True},
        {"type": "suite", "event": "started", "test_count": 0},
        {"type": "suite", "event": "ok", "passed": 0, "failed": 0, "ignored": 0, "measured": 0, "filtered_out": 3})),
    ("jest-json", json.dumps({"numTotalTests": 0, "success": True, "testResults": []})),
]


@pytest.mark.parametrize("report,text", EMPTY, ids=["junit", "junit-suite-root", "trx", "go", "cargo", "jest"])
def test_a_recognised_report_with_no_test_is_a_zero_summary_not_an_absent_one(tmp_path, report, text):
    summary = parse(tmp_path, report, text)
    assert summary == expected(report, 0, 0, 0, 0, [])
    assert not test_summary.failed(summary)


def test_a_missing_or_oversized_report_has_no_summary(tmp_path, monkeypatch):
    assert test_summary.summarize("junit-xml", tmp_path / "absent.xml") is None
    monkeypatch.setattr(test_summary, "REPORT_BYTES", 64)
    assert parse(tmp_path, "junit-xml", JUNIT) is None


def test_failing_names_stop_at_twenty_and_report_truncation(tmp_path):
    text = lines(*({"Action": "fail", "Package": "p", "Test": f"Test{index}"} for index in range(25)))
    summary = parse(tmp_path, "go-json", text)
    assert summary["failed"] == 25 and summary["truncated"]
    assert summary["failing"] == [f"Test{index}" for index in range(20)]


def test_an_overlong_name_is_left_out_and_reported_as_truncated(tmp_path):
    long = "Test" + "x" * test_summary.MAX_NAME_CHARS
    summary = parse(tmp_path, "go-json", lines({"Action": "fail", "Package": "p", "Test": long},
                                               {"Action": "fail", "Package": "p", "Test": "TestShort"}))
    assert summary["failing"] == ["TestShort"] and summary["truncated"]


def test_repeated_names_are_listed_once(tmp_path):
    rerun = lines({"type": "test", "event": "failed", "name": "tests::subtracts"})
    assert parse(tmp_path, "cargo-json", CARGO + rerun)["failing"] == ["tests::subtracts"]


def test_binding_substitutes_the_report_path_and_requires_it_for_file_formats(tmp_path):
    path = tmp_path / "run.report"
    argv, source = test_summary.bind("junit-xml", ["python3", "-m", "pytest", "--junit-xml={report}"], path)
    assert argv == ["python3", "-m", "pytest", f"--junit-xml={path}"] and source == path
    argv, source = test_summary.bind("go-json", ["go", "test", "-json", "./..."], path)
    assert argv == ["go", "test", "-json", "./..."] and source is None
    assert test_summary.bind(None, ["make"], path) == (["make"], None)
    with pytest.raises(ValidationError, match="{report}"):
        test_summary.bind("trx", ["dotnet", "test", "--logger", "trx"], path)
    with pytest.raises(ValidationError, match="report format"):
        test_summary.bind("tap", ["prove"], path)
