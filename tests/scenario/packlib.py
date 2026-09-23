"""Shared machinery for the scenario packs.

Both packs assert the same two kinds of thing — that their goals are ones `hx dispatch` would
accept, and that their `expected/` boards are what `hx board` really prints — so the parts that
build an instance and read a board live here rather than being written twice and drifting.

The interesting function is `build_instance`: it writes an instance in exactly the state a
given observation point describes, by writing the files hx would have written. That is not the
same as running `hx dispatch` and `hx complete` to get there — it asserts the *shape* of each
step, not the transitions that produce it. M8 asserts the transitions.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SCENARIOS = pathlib.Path(__file__).resolve().parent

MUTABLE_HEADER = "## UPDATES BELOW ONLY"

#: A fixed instant, written into every marker so `hx board` prints something deterministic.
#: Normalised back to `<ts>` before comparing, so no checked-in file carries a fake timestamp.
FIXED_TS = "2026-09-20T12:00:00Z"
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")

#: One entry per id at an observation point.
#: (state, outcome, whether a `run/<id>/goal` marker exists)
#:
#: There is no `after` and no `queued` state: spec 14 D25 cut dependency chains, and the
#: Partner has no work item at all, so it is never a row on the board.
State = tuple[str, "str | None", bool]


def hx_env(root: pathlib.Path | None = None) -> dict[str, str]:
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    env.pop("HARNESS_ID", None)
    if root is not None:
        env["HARNESS_ROOT"] = str(root)
    return env


def normalise(text: str) -> str:
    return TS_RE.sub("<ts>", text).strip()


def pod_of(item_id: str, worker_pod: str) -> str:
    return "partner" if item_id == "partner" else worker_pod


def build_instance(root: pathlib.Path, states: dict[str, State], *, worker_pod: str) -> None:
    """Write the instance exactly as hx would have left it at this observation point."""
    subprocess.run(
        [sys.executable, "-m", "hx", "install", "--skeleton-only", "--root", str(root)],
        check=True, capture_output=True, text=True, env=hx_env(),
    )
    # Auth is one instance token at seed/token, mode 0600 (spec 11 Auth, CONTRACTS.md).
    seed = root / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    token = seed / "token"
    token.write_text("scenario-fixture-token-not-a-real-credential\n")
    token.chmod(0o600)

    worker_template = root / "templates" / "worker"
    tasks: dict[str, dict] = {}

    for item_id, (state, outcome, has_goal) in states.items():
        assert item_id != "partner", "the Partner has no work item (spec 06, 12)"
        config_dir = root / "config" / item_id
        config_dir.mkdir(parents=True, exist_ok=True)
        workdir = root / "work" / item_id
        workdir.mkdir(parents=True, exist_ok=True)
        for name in ("AGENTS.md", "SUBAGENTS.md", "harness.json"):
            text = (worker_template / name).read_text()
            text = text.replace("{{id}}", item_id).replace("{{pod}}", worker_pod)
            text = text.replace("{{workdir}}", str(workdir))
            (config_dir / name).write_text(text)

        body = (root / "templates" / "work-item.md").read_text()
        body = (
            body.replace("{{id}}", item_id)
            .replace("{{pod}}", worker_pod)
            .replace("{{dispatched}}", FIXED_TS)
            .replace("{{goal}}", "## Goal\n\nscenario fixture\n\n## Definition of done\n\n1. n/a")
        )
        # Only a `complete` item carries an outcome in its frontmatter (spec 06).
        if outcome and state == "complete":
            body = body.replace("outcome:\n", f"outcome: {outcome}\n", 1)
        item_dir = root / "pods" / worker_pod
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / f"{item_id}-{state}.md").write_text(body)

        run_dir = root / "run" / item_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if has_goal:
            (run_dir / "goal").write_text(FIXED_TS + "\n")

        if state != "idle" or outcome:
            tasks[item_id] = {
                "goal": "scenario fixture", "addenda": [],
                "outcome": outcome, "dispatched": FIXED_TS,
                "completed": FIXED_TS if outcome else None,
            }
    (root / "tasks.json").write_text(json.dumps(tasks, indent=2))


def real_board(root: pathlib.Path, states: dict[str, State]) -> tuple[list[str], str]:
    """`hx board`'s text for this instance, timestamps normalised.

    No tmux session is created: `hx board` reports liveness by id, and this suite must not
    create sessions named `eng-001` on a machine that may be running a real fleet. The
    `alive`/`dead` column is therefore normalised out rather than asserted; M8 asserts
    liveness for real.
    """
    r = subprocess.run(
        [sys.executable, "-m", "hx", "board"],
        capture_output=True, text=True, env=hx_env(root),
    )
    lines = [
        re.sub(r"\b(alive|dead)\b", "<alive>", line)
        for line in normalise(r.stdout).splitlines()
    ]
    return lines, r.stderr


def checks_commands(checks: str) -> list[str]:
    return [
        line for line in checks.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def assert_mutable_header(path: pathlib.Path) -> None:
    lines = path.read_text().splitlines()
    hits = [n for n, line in enumerate(lines, 1) if line.strip() == MUTABLE_HEADER]
    assert len(hits) == 1, f"{path}: {len(hits)} `{MUTABLE_HEADER}` lines, expected exactly 1"
    above, _, _ = path.read_text().partition(MUTABLE_HEADER)
    assert above.strip(), f"{path}: nothing above the header, so persona.md would be empty"
    assert path.parent.name in above, f"{path}: the persona does not name its own id"
    # Below the header may be empty: it is the agent's own memory, written as it works.


def assert_addendum_is_prose(path: pathlib.Path) -> None:
    """`hx resume` appends it verbatim beneath `## Goal` (spec 06, 08), so a `##` heading
    would break the work item's structure."""
    text = path.read_text()
    assert text.strip(), f"{path} is empty"
    assert not re.search(r"^## ", text, re.MULTILINE), (
        f"{path}: must not introduce `##` headings; it is appended under `## Goal`"
    )
    assert not text.startswith("---\n"), f"{path}: an addendum carries no frontmatter"


def assert_expected_board(
    expected_dir: pathlib.Path, stem: str, states: dict[str, State], *, worker_pod: str
) -> None:
    """The checked-in file agrees with the state this step is in, column by column.

    `hx board` is a listing, not a verdict (spec 08): no invariants, no error lines, and one
    row per *worker* id — the Partner has no work item and is never on it — followed by
    one machine-derived `scope <id>:` line per id that has a goal.
    """
    lines = (expected_dir / f"{stem}.txt").read_text().strip().splitlines()
    rows = [line for line in lines if not line.startswith("scope ")]
    scopes = [line for line in lines if line.startswith("scope ")]
    assert len(rows) == len(states), f"{stem}: {len(rows)} rows for {len(states)} ids"
    for line, item_id in zip(rows, sorted(states)):
        state, outcome, _ = states[item_id]
        columns = line.split()
        assert columns[0] == item_id, f"{stem}: {line!r} does not start with {item_id}"
        assert columns[1] == worker_pod, f"{stem}: {line!r} is not in pod {worker_pod}"
        assert columns[2] == state, f"{stem}: {line!r} is not {state}"
        assert columns[3] == (outcome or "-"), f"{stem}: {line!r} outcome is not {outcome}"
        assert "partner" not in line, f"{stem}: the Partner is not a row on the board"
    assert scopes == [
        f"scope {item_id}: scenario fixture" for item_id in sorted(states)
    ], f"{stem}: scope lines are one per id, derived from the pack goal"


def assert_no_cut_features(steps: list[tuple[str, dict[str, State]]]) -> None:
    """Spec 14 D25: no `queued` state, and the Partner is never an item."""
    for stem, states in steps:
        for item_id, (state, _, has_goal) in states.items():
            assert state in ("idle", "working", "complete"), f"{stem}/{item_id}: {state}"
            assert item_id != "partner", f"{stem}: the Partner has no work item"
            if state == "working":
                assert has_goal, f"{stem}/{item_id}: working with no goal marker"
