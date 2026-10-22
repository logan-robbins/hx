"""Test-run summaries parsed from a runner's declared machine-readable report.

A check recipe (or `hx tool-exec --report`) declares one format. `junit-xml` (pytest
`--junit-xml`, any JUnit XML) and `trx` (`dotnet test --logger trx`) are files: the command names
the hx-owned path with the argument placeholder `{report}`, which `bind` substitutes, so the
report never lands in the checkout. `go-json` (`go test -json`), `cargo-json` (cargo
`--message-format=json` plus libtest `--format json`) and `jest-json` (`jest --json`, vitest
`--reporter=json`) are read from `{report}` when the command names it, else from the captured
output, whose non-JSON lines are skipped.

`summarize(format, path)` returns `{"format", "passed", "failed", "skipped", "errors",
"failing", "truncated"}` counted per test from the report, or None when the report is absent,
malformed, larger than REPORT_BYTES or not in the declared format. A recognised report with no
test is an all-zero summary: a selection that matched nothing is a parse, not a missing report.
`failing` holds at most 20 unique failed or errored names of at most 512 characters in report
order; `truncated` is true when a name was left out. Text output is never parsed.
"""
from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

from .errors import ValidationError

MAX_FAILING = 20
MAX_NAME_CHARS = 512
REPORT_BYTES = 16 * 1024 * 1024
PLACEHOLDER = "{report}"
FILE_FORMATS = ("junit-xml", "trx")
FORMATS = (*FILE_FORMATS, "go-json", "cargo-json", "jest-json")

# VSTest TestOutcome values; anything else (Inconclusive, NotExecuted, Pending…) is skipped.
TRX_PASSED = {"Passed", "PassedButRunAborted", "Completed", "Warning"}
TRX_ERRORS = {"Error", "Timeout", "Aborted", "Disconnected", "NotRunnable"}


def bind(report, argv, path):
    """The argv to run and the report file to read (None: read captured output)."""
    if report is None:
        return list(argv), None
    if report not in FORMATS:
        raise ValidationError(f"report format must be one of {', '.join(FORMATS)}")
    named = any(PLACEHOLDER in part for part in argv)
    if report in FILE_FORMATS and not named:
        raise ValidationError(f"a {report} report needs the {PLACEHOLDER} argument placeholder for its file")
    if not named:
        return list(argv), None
    return [part.replace(PLACEHOLDER, str(path)) for part in argv], Path(path)


class _Counts:
    """Per-test counts; `seen` records that the report was recognised in its declared format."""

    def __init__(self, report):
        self.report = report
        self.counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
        self.names: dict[str, None] = {}
        self.seen = False

    def add(self, kind, name=None):
        self.seen = True
        self.counts[kind] += 1
        if kind in {"failed", "errors"} and name:
            self.names.setdefault(name)

    def summary(self):
        if not self.seen:
            return None
        listed = [name for name in self.names if len(name) <= MAX_NAME_CHARS]
        return {"format": self.report, **self.counts, "failing": listed[:MAX_FAILING],
                "truncated": len(listed) > MAX_FAILING or len(listed) < len(self.names)}


def failed(summary: dict | None) -> bool:
    """Whether a report recorded a failed or errored test."""
    return bool(summary and (summary["failed"] or summary["errors"] or summary["failing"]))


def _local(tag):
    return tag.rpartition("}")[2]


def _junit(root, counts):
    if _local(root.tag) in {"testsuites", "testsuite"}:
        counts.seen = True
    for case in root.iter():
        if _local(case.tag) != "testcase":
            continue
        kinds = {_local(child.tag) for child in case}
        name = case.get("name", "")
        full = f"{case.get('classname')}::{name}" if case.get("classname") else name
        counts.add("errors" if "error" in kinds else "failed" if "failure" in kinds
                   else "skipped" if "skipped" in kinds else "passed", full)


def _trx(root, counts):
    if _local(root.tag) == "TestRun":
        counts.seen = True
    for result in root.iter():
        if _local(result.tag) != "UnitTestResult":
            continue
        outcome = result.get("outcome", "")
        counts.add("failed" if outcome == "Failed" else "errors" if outcome in TRX_ERRORS
                   else "passed" if outcome in TRX_PASSED else "skipped", result.get("testName"))


def _go(event, counts, packages):
    action, package, test = event.get("Action"), event.get("Package"), event.get("Test")
    if isinstance(action, str):
        counts.seen = True
    if action not in {"pass", "fail", "skip"} or not isinstance(package, str):
        return
    if test:
        counts.add({"pass": "passed", "fail": "failed", "skip": "skipped"}[action], test)
        if action == "fail":
            packages.add(package)
    elif action == "fail" and (event.get("FailedBuild") or package not in packages):
        # A package that failed without a failing test: build, setup, TestMain or timeout.
        counts.add("errors", package + (" [build failed]" if event.get("FailedBuild") else ""))


def _cargo(event, counts, state):
    if isinstance(event.get("reason"), str) or isinstance(event.get("type"), str):
        counts.seen = True
    if event.get("reason") == "compiler-message":
        message = event.get("message")
        if isinstance(message, dict) and message.get("level") == "error":
            target = event.get("target") if isinstance(event.get("target"), dict) else {}
            if not state["build_error"]:
                counts.add("errors", f"{target.get('name', '')} [build failed]".lstrip())
            state["build_error"] = True
    elif event.get("reason") == "build-finished" and event.get("success") is False and not state["build_error"]:
        counts.add("errors", "[build failed]")
        state["build_error"] = True
    elif event.get("type") == "test" and isinstance(event.get("name"), str):
        kind = {"ok": "passed", "failed": "failed", "timeout": "failed", "ignored": "skipped"}.get(event.get("event"))
        if kind:
            counts.add(kind, event["name"])


def _jest(report, counts):
    results = report.get("testResults")
    if not isinstance(results, list):
        return
    counts.seen = True
    for suite in results:
        if not isinstance(suite, dict):
            continue
        assertions = [item for item in suite.get("assertionResults") or [] if isinstance(item, dict)]
        for item in assertions:
            titles = item.get("ancestorTitles") if isinstance(item.get("ancestorTitles"), list) else []
            name = " › ".join(str(title) for title in [*titles, item.get("title", "")])
            status = item.get("status")
            counts.add("passed" if status == "passed" else "failed" if status == "failed" else "skipped", name)
        if suite.get("status") == "failed" and not any(item.get("status") == "failed" for item in assertions):
            # A suite that failed outside its tests: syntax, import or setup error.
            counts.add("errors", suite.get("name"))


def summarize(report: str, path: Path) -> dict | None:
    counts = _Counts(report)
    try:
        with Path(path).open("rb") as source:
            if report in FILE_FORMATS:
                data = source.read(REPORT_BYTES + 1)
                if len(data) > REPORT_BYTES:
                    return None
                root = ElementTree.fromstring(data)
                (_junit if report == "junit-xml" else _trx)(root, counts)
                return counts.summary()
            packages, state = set(), {"build_error": False}
            size = 0
            for raw in source:
                size += len(raw)
                if size > REPORT_BYTES:
                    return None
                line = raw.strip()
                if not line.startswith(b"{"):
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if report == "go-json":
                    _go(event, counts, packages)
                elif report == "cargo-json":
                    _cargo(event, counts, state)
                else:
                    _jest(event, counts)
    except (OSError, ElementTree.ParseError, UnicodeError):
        return None
    return counts.summary()
