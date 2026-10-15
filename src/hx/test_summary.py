"""Deterministic test-run summaries recognized from a runner's own printed lines.

`summarize(text)` returns `{"framework", "passed", "failed", "skipped", "errors", "failing",
"truncated"}`, or None when no supported runner's summary line appears. A count is what the
runner printed: a category the runner reports by omission is 0, a category it never prints is
None (unittest prints no passed count; go prints test counts only with `-v`; jest, vitest,
cargo and dotnet print no error count). Totals from several runs in one output are summed.
`failing` holds at most 20 unique failed or errored names of at most 512 characters in output
order; `truncated` is true when a name was left out. Unsupported formats are not guessed.
"""
from __future__ import annotations

import re

MAX_FAILING = 20
MAX_NAME_CHARS = 512
LINE_CHARS = 4096
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
COUNT = re.compile(r"(\d+) ([a-z]+)")

PYTEST_TOTAL = re.compile(r"^=*\s*(?P<body>(?:\d+ (?:passed|failed|skipped|errors?|xfailed|xpassed|deselected|"
                          r"warnings?|rerun)(?:, )?)+|no tests ran) in \d+(?:\.\d+)?s\b")
PYTEST_SECTION = re.compile(r"^=+ short test summary info =+$")
PYTEST_FAILING = re.compile(r"^(?:FAILED|ERROR) (?P<name>[^\s(].*?)(?: - .*)?$")
UNITTEST_RAN = re.compile(r"^Ran \d+ tests? in \d+(?:\.\d+)?s$")
UNITTEST_RESULT = re.compile(r"^(?:OK|FAILED)(?: \((?P<body>[^)]*)\))?$")
UNITTEST_FAILING = re.compile(r"^(?:FAIL|ERROR): (?P<name>\S.*)$")
JEST_TOTAL = re.compile(r"^Tests:\s+(?P<body>.*\d+ total)$")
JEST_NAMED = re.compile(r"^\s*●\s+(?P<name>\S.*)$")
MARKED = re.compile(r"^\s*[✕×]\s+(?P<name>\S.*?)(?:\s+\(?\d+(?:\.\d+)?\s*m?s\)?)?$")
VITEST_TOTAL = re.compile(r"^\s*Tests\s+(?P<body>\d+ [a-z]+(?: \| \d+ [a-z]+)*) \(\d+\)")
VITEST_NAMED = re.compile(r"^\s*FAIL\s+(?P<name>\S.*? > \S.*)$")
GO_RUN = re.compile(r"^=== RUN\s")
GO_TEST = re.compile(r"^\s*--- (?P<status>PASS|FAIL|SKIP): (?P<name>\S+) \(")
GO_PACKAGE = re.compile(r"^(?:ok  |FAIL)\t(?P<package>\S+)(?:\t(?:\d+(?:\.\d+)?s|\(cached\))| \[(?P<reason>[^\]]+)\])")
CARGO_TOTAL = re.compile(r"^test result: (?:ok|FAILED)\. (?P<passed>\d+) passed; (?P<failed>\d+) failed; "
                         r"(?P<ignored>\d+) ignored;")
CARGO_FAILING = re.compile(r"^test (?P<name>\S+) \.\.\. FAILED$")
DOTNET_TOTAL = re.compile(r"^\s*(?:Passed|Failed)!\s+-\s+Failed:\s+(?P<failed>\d+),\s+Passed:\s+(?P<passed>\d+),"
                          r"\s+Skipped:\s+(?P<skipped>\d+),\s+Total:\s+\d+")
DOTNET_FAILING = re.compile(r"^\s*Failed (?P<name>\S.*?) \[[^\]]*\]$")

#: Reporting order when several runners appear; a runner reporting a failure comes first.
FRAMEWORKS = ("pytest", "unittest", "jest", "vitest", "go", "cargo", "dotnet")


class _Runner:
    def __init__(self, name):
        self.name, self.seen = name, False
        self.counts = {"passed": None, "failed": None, "skipped": None, "errors": None}
        self.names: dict[str, None] = {}
        self.marked: dict[str, None] = {}

    def add(self, **counts):
        self.seen = True
        for key, value in counts.items():
            self.counts[key] = (self.counts[key] or 0) + value

    def summary(self):
        names = list(self.names or self.marked)
        listed = [name for name in names if len(name) <= MAX_NAME_CHARS]
        return {"framework": self.name, **self.counts, "failing": listed[:MAX_FAILING],
                "truncated": len(listed) > MAX_FAILING or len(listed) < len(names)}


def _counts(body):
    totals = {}
    for count, word in COUNT.findall(body):
        totals[word] = totals.get(word, 0) + int(count)
    return totals


def failed(summary: dict | None) -> bool:
    """Whether a recognized runner reported a failed or errored test."""
    return bool(summary and (summary["failed"] or summary["errors"] or summary["failing"]))


def summarize_lines(lines) -> dict | None:
    """The summary of an iterable of output lines, consumed once."""
    runners = {name: _Runner(name) for name in FRAMEWORKS}
    pytest, unittest, jest, vitest, go, cargo, dotnet = runners.values()
    pytest_section = unittest_result = go_verbose = go_seen = False
    go_tests = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    go_errors = 0
    for raw in lines:
        line = raw[:LINE_CHARS].rstrip("\r\n")
        if "\r" in line:
            line = line.rsplit("\r", 1)[1]
        if "\x1b" in line:
            line = ANSI.sub("", line)
        line = line.rstrip()
        if not line:
            continue
        if PYTEST_SECTION.match(line):
            pytest_section = True
        elif match := PYTEST_TOTAL.match(line):
            counts = _counts(match["body"])
            pytest.add(passed=counts.get("passed", 0), failed=counts.get("failed", 0),
                       skipped=counts.get("skipped", 0), errors=counts.get("error", 0) + counts.get("errors", 0))
            pytest_section = False
        elif pytest_section and (match := PYTEST_FAILING.match(line)):
            pytest.names.setdefault(match["name"])
        elif UNITTEST_RAN.match(line):
            unittest.seen = unittest_result = True
        elif unittest_result and (match := UNITTEST_RESULT.match(line)):
            fields = dict(part.strip().split("=", 1) for part in (match["body"] or "").split(",") if "=" in part)
            unittest.add(failed=int(fields.get("failures", 0)), skipped=int(fields.get("skipped", 0)),
                         errors=int(fields.get("errors", 0)))
            unittest_result = False
        elif match := UNITTEST_FAILING.match(line):
            unittest.names.setdefault(match["name"])
        elif match := JEST_TOTAL.match(line):
            counts = _counts(match["body"])
            jest.add(passed=counts.get("passed", 0), failed=counts.get("failed", 0), skipped=counts.get("skipped", 0))
        elif match := VITEST_TOTAL.match(line):
            counts = _counts(match["body"])
            vitest.add(passed=counts.get("passed", 0), failed=counts.get("failed", 0), skipped=counts.get("skipped", 0))
        elif match := JEST_NAMED.match(line):
            if match["name"] != "Console":
                jest.names.setdefault(match["name"])
        elif match := VITEST_NAMED.match(line):
            vitest.names.setdefault(match["name"])
        elif match := MARKED.match(line):
            jest.marked.setdefault(match["name"])
            vitest.marked.setdefault(match["name"])
        elif GO_RUN.match(line):
            go_verbose = True
        elif match := GO_TEST.match(line):
            go_seen = True
            go_tests[match["status"]] += 1
            if match["status"] == "FAIL":
                go.names.setdefault(match["name"])
        elif match := GO_PACKAGE.match(line):
            go_seen = True
            if match["reason"]:
                go_errors += 1
                go.names.setdefault(f"{match['package']} [{match['reason']}]")
        elif match := CARGO_TOTAL.match(line):
            cargo.add(passed=int(match["passed"]), failed=int(match["failed"]), skipped=int(match["ignored"]))
        elif match := CARGO_FAILING.match(line):
            cargo.names.setdefault(match["name"])
        elif match := DOTNET_TOTAL.match(line):
            dotnet.add(passed=int(match["passed"]), failed=int(match["failed"]), skipped=int(match["skipped"]))
        elif match := DOTNET_FAILING.match(line):
            dotnet.names.setdefault(match["name"])
    if go_seen:
        go.add(failed=go_tests["FAIL"], errors=go_errors)
        if go_verbose:
            go.add(passed=go_tests["PASS"], skipped=go_tests["SKIP"])
    found = [runner.summary() for runner in runners.values() if runner.seen]
    return next((summary for summary in found if failed(summary)), found[0] if found else None)


def summarize(text: str) -> dict | None:
    return summarize_lines(text.splitlines())
