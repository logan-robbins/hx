from __future__ import annotations

import pytest

from hx import test_summary

PYTEST = """============================= test session starts ==============================
collected 4 items

tests/test_a.py .F.s                                                     [100%]

=================================== FAILURES ===================================
___________________________________ test_one ___________________________________

    def test_one():
>       assert 1 == 2
E       assert 1 == 2

tests/test_a.py:3: AssertionError
=========================== short test summary info ============================
FAILED tests/test_a.py::test_one - assert 1 == 2
ERROR tests/test_b.py - ImportError: no module
==================== 1 failed, 2 passed, 1 skipped, 1 error in 0.12s ====================
"""

UNITTEST = """F.E
======================================================================
ERROR: test_load (pkg.tests.LoaderTest.test_load)
----------------------------------------------------------------------
Traceback (most recent call last):
ValueError: bad
======================================================================
FAIL: test_add (pkg.tests.MathTest.test_add)
----------------------------------------------------------------------
AssertionError: 3 != 4
----------------------------------------------------------------------
Ran 3 tests in 0.002s

FAILED (failures=1, errors=1, skipped=1)
"""

JEST = """ FAIL  src/sum.test.js
  math
    ✓ adds (2 ms)
    ✕ subtracts (3 ms)

  ● math › subtracts

    expect(received).toBe(expected)

Test Suites: 1 failed, 1 total
Tests:       1 failed, 1 skipped, 1 passed, 3 total
"""

VITEST = """ ❯ src/basic.test.ts (2 tests | 1 failed) 5ms
   × Math.sqrt() 3ms
⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯
 FAIL  src/basic.test.ts > Math.sqrt()
AssertionError: expected 2 to be 3
 Test Files  1 failed (1)
      Tests  1 failed | 1 passed (2)
"""

GO = """--- FAIL: TestAdd (0.00s)
    add_test.go:9: expected 3, got 4
    --- FAIL: TestAdd/negative (0.00s)
FAIL
FAIL\texample.com/calc\t0.010s
ok  \texample.com/util\t(cached)
FAIL\texample.com/broken [build failed]
"""

GO_VERBOSE = """=== RUN   TestA
--- PASS: TestA (0.00s)
=== RUN   TestB
--- SKIP: TestB (0.00s)
PASS
ok  \texample.com/calc\t0.010s
"""

CARGO = """running 3 tests
test tests::adds ... ok
test tests::subtracts ... FAILED
test tests::slow ... ignored

failures:

---- tests::subtracts stdout ----
thread 'tests::subtracts' panicked at src/lib.rs:10:9:
assertion `left == right` failed

test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

DOTNET = """  Failed Calc.Tests.AddTests.AddsNegative [12 ms]
  Error Message:
   Assert.Equal() Failure
Failed!  - Failed:     1, Passed:     4, Skipped:     1, Total:     6, Duration: 50 ms - Calc.Tests.dll (net8.0)
Passed!  - Failed:     0, Passed:     2, Skipped:     0, Total:     2, Duration: 9 ms - Util.Tests.dll (net8.0)
"""


def expected(framework, passed, failed, skipped, errors, failing, truncated=False):
    return {"framework": framework, "passed": passed, "failed": failed, "skipped": skipped,
            "errors": errors, "failing": failing, "truncated": truncated}


@pytest.mark.parametrize("text,summary", [
    (PYTEST, expected("pytest", 2, 1, 1, 1, ["tests/test_a.py::test_one", "tests/test_b.py"])),
    ("3 passed in 0.01s\n", expected("pytest", 3, 0, 0, 0, [])),
    (UNITTEST, expected("unittest", None, 1, 1, 1, ["test_load (pkg.tests.LoaderTest.test_load)",
                                                    "test_add (pkg.tests.MathTest.test_add)"])),
    ("..s\n----\nRan 3 tests in 0.001s\n\nOK (skipped=1)\n", expected("unittest", None, 0, 1, 0, [])),
    (JEST, expected("jest", 1, 1, 1, None, ["math › subtracts"])),
    (VITEST, expected("vitest", 1, 1, 0, None, ["src/basic.test.ts > Math.sqrt()"])),
    (GO, expected("go", None, 2, None, 1, ["TestAdd", "TestAdd/negative", "example.com/broken [build failed]"])),
    (GO_VERBOSE, expected("go", 1, 0, 1, 0, [])),
    (CARGO, expected("cargo", 1, 1, 1, None, ["tests::subtracts"])),
    (DOTNET, expected("dotnet", 6, 1, 1, None, ["Calc.Tests.AddTests.AddsNegative"])),
], ids=["pytest", "pytest-pass", "unittest", "unittest-pass", "jest", "vitest", "go", "go-verbose", "cargo", "dotnet"])
def test_each_runner_summary_reports_only_printed_counts_and_failing_names(text, summary):
    assert test_summary.summarize(text) == summary
    assert test_summary.failed(summary) == bool(summary["failed"] or summary["errors"])


def test_ansi_color_and_carriage_return_progress_do_not_hide_the_summary():
    text = "\x1b[31m\x1b[1m1 failed\x1b[0m, \x1b[32m2 passed\x1b[0m in 0.40s\nrunning 1/3\rrunning 3/3\r\n"
    assert test_summary.summarize(text) == expected("pytest", 2, 1, 0, 0, [])


@pytest.mark.parametrize("text", [
    "", "building...\ndone in 3.2s\n12 files in 0.5s\n", "ok   done 3s\n",
    "FAILED to connect\nERROR: tests/x.py\n", "Tests: pending\n",
])
def test_unrecognized_output_has_no_summary(text):
    assert test_summary.summarize(text) is None
    assert not test_summary.failed(None)


def test_failing_names_stop_at_twenty_and_report_truncation():
    summary = test_summary.summarize("\n".join(f"--- FAIL: Test{index} (0.00s)" for index in range(25)))
    assert summary["failed"] == 25 and summary["truncated"]
    assert summary["failing"] == [f"Test{index}" for index in range(20)]


def test_an_overlong_name_is_left_out_and_reported_as_truncated():
    long = "Test" + "x" * test_summary.MAX_NAME_CHARS
    summary = test_summary.summarize(f"--- FAIL: {long} (0.00s)\n--- FAIL: TestShort (0.00s)\n")
    assert summary["failing"] == ["TestShort"] and summary["truncated"]


def test_repeated_names_are_listed_once_and_several_runs_are_summed():
    summary = test_summary.summarize(CARGO + CARGO.replace("tests::subtracts", "tests::divides"))
    assert summary == expected("cargo", 2, 2, 2, None, ["tests::subtracts", "tests::divides"])
    assert test_summary.summarize(PYTEST + PYTEST)["failing"] == ["tests/test_a.py::test_one", "tests/test_b.py"]


def test_a_failed_runner_is_reported_ahead_of_a_passing_one():
    passing_cargo = "test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
    assert test_summary.summarize("3 passed in 0.01s\n" + CARGO)["framework"] == "cargo"
    assert test_summary.summarize(passing_cargo + "3 passed in 0.01s\n")["framework"] == "pytest"
