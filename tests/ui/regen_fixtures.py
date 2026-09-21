"""Regenerate the stream fixtures from records the real hooks wrote (ui-6 item 4).

    .venv/bin/python -m tests.ui.regen_fixtures

Builds an instance, drives `python -m hx.hooks` through a whole M4-shaped run
(`tests/ui/m4.py`), reads `hx show <id> --json`, and writes the `streams` and
`subagents` blocks into `tests/ui/fixtures/show-eng-001.json`. Nothing about a
record is invented here: the only edits are to make the result machine- and
run-independent, so the fixture is stable in git —

* the scratch root becomes `/srv/hx`, the root every other fixture uses;
* timestamps become the fixed instants the rest of the fixtures carry.

`step_state` and `metrics` stay hand-written, because nothing writes them yet:
the Companion is build-6 and `hx metrics` is M7. `test_fixtures_contract.py`
says so, and will start failing the day that stops being true.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.scenario import packlib  # noqa: E402
from tests.ui import m4  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
ITEM = "eng-001"

#: The instants the hand-written fixtures already use, so a regenerated file
#: does not churn every run. One per record, in stream order.
BASE_TS = "2026-09-20T13:09:{:02d}Z"
FIXED_ROOT = "/srv/hx"

TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


def stabilise(document: dict, root: Path) -> dict:
    """Make the real document reproducible: fixed root, fixed timestamps."""
    text = json.dumps(document)
    text = text.replace(str(root), FIXED_ROOT)
    document = json.loads(text)

    second = 0
    for stream in document.get("streams", []):
        for record in stream.get("tail", []):
            record["ts"] = BASE_TS.format(min(second, 59))
            second += 1
    return document


def build(root: Path) -> dict:
    packlib.build_instance(
        root,
        {"partner": ("working", None, [], True), ITEM: ("working", None, [], True)},
        worker_pod="engineers",
    )
    m4.drive(root, ITEM, subagents=3, leave_open=1)

    from hx.show import collect

    return collect(root, ITEM, env={"HX_TMUX": "tmux -L hx-ui-regen-none"})


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="hx-ui-regen-"))
    try:
        real = stabilise(build(scratch / "instance"), scratch / "instance")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    target = FIXTURES / f"show-{ITEM}.json"
    fixture = json.loads(target.read_text(encoding="utf-8"))
    fixture["streams"] = real["streams"]
    fixture["subagents"] = real["subagents"]
    target.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")

    handles = [stream["handle"] for stream in real["streams"]]
    closed = [s["handle"] for s in real["streams"] if not s["open"]]
    print(f"wrote {target.relative_to(REPO)}")
    print(f"  streams   : {handles}")
    print(f"  closed    : {closed}")
    print(f"  subagents : {real['subagents']}")
    events = [r["event"] for r in real["streams"][0]["tail"]]
    print(f"  main tail : {events}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
