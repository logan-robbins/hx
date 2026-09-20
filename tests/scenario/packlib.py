"""Shared machinery for the scenario packs.

Both packs assert the same two kinds of thing — that their orders are ones `hx dispatch` would
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
#: (state, outcome, after, whether a `run/<id>/goal` marker exists)
State = tuple[str, "str | None", list[str], bool]


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
    # Auth is one instance token at seed/token, mode 0600 (spec 11 Auth, CONTRACTS.md), and
    # `hx board` reports a missing or world-readable one as an invariant error. A scratch
    # instance without it would differ from a real one in the board text, which is the whole
    # thing these fixtures exist to compare.
    seed = root / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    token = seed / "token"
    token.write_text("scenario-fixture-token-not-a-real-credential\n")
    token.chmod(0o600)

    worker_template = root / "templates" / "worker"
    tasks: dict[str, dict] = {}

    for item_id, (state, outcome, after, has_goal) in states.items():
        pod = pod_of(item_id, worker_pod)
        if item_id != "partner":
            config_dir = root / "config" / item_id
            config_dir.mkdir(parents=True, exist_ok=True)
            for name in ("AGENTS.md", "SUBAGENTS.md", "harness.json"):
                text = (worker_template / name).read_text()
                (config_dir / name).write_text(
                    text.replace("{{id}}", item_id).replace("{{pod}}", pod)
                )
            (root / "wt" / item_id).mkdir(parents=True, exist_ok=True)

        body = (root / "templates" / "work-item.md").read_text()
        body = (
            body.replace("{{id}}", item_id)
            .replace("{{pod}}", pod)
            .replace("{{after}}", ", ".join(after))
            .replace("{{dispatched}}", FIXED_TS)
            .replace("{{order}}", "## Order\n\nscenario fixture\n\n## Definition of done\n\n1. n/a")
        )
        # Only a `complete` item carries an outcome in its frontmatter (spec 06); hx.workitems
        # rejects an `idle` item that does. A benched item's `done` still shows on the board,
        # because it comes from tasks.json, which `hx bench` does not touch (spec 08).
        if outcome and state == "complete":
            body = body.replace("outcome:\n", f"outcome: {outcome}\n", 1)
        item_dir = root / "pods" / pod
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / f"{item_id}-{state}.md").write_text(body)

        run_dir = root / "run" / item_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if has_goal:
            (run_dir / "goal").write_text(FIXED_TS + "\n")

        if state != "idle" or outcome:
            tasks[item_id] = {
                "order": "scenario fixture", "after": after, "addenda": [],
                "outcome": outcome, "dispatched": FIXED_TS,
                "completed": FIXED_TS if outcome else None,
            }
    (root / "tasks.json").write_text(json.dumps(tasks, indent=2))


def real_board(root: pathlib.Path, states: dict[str, State]) -> list[str]:
    """`hx board`'s text for this instance, timestamps normalised.

    No tmux session is created: `hx board` matches a live session by the id itself, and this
    suite must not create sessions named `partner` or `eng-001` on a machine that may be
    running a real fleet. The resulting "no live tmux session" errors are expected here and are
    stripped; M8 asserts session liveness for real.
    """
    r = subprocess.run(
        [sys.executable, "-m", "hx", "board"],
        capture_output=True, text=True, env=hx_env(root),
    )
    suffixes = tuple(f"no live tmux session {i}" for i in states)
    return [
        line for line in normalise(r.stdout).splitlines() if not line.endswith(suffixes)
    ], r.stderr


def checks_commands(checks: str) -> list[str]:
    return [
        line for line in checks.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def assert_acyclic(graph: dict[str, list[str]], known: set[str]) -> None:
    """Depth-first, so a longer chain added later is still caught."""
    for item_id, after in graph.items():
        for dep in after:
            assert dep in known, f"{item_id}: `after` names {dep!r}, which the pack does not ship"
            assert dep != item_id, f"{item_id}: depends on itself"

    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)

    def visit(node: str, trail: list[str]) -> None:
        if colour.get(node) == GREY:
            raise AssertionError(f"cycle in `after`: {' -> '.join(trail + [node])}")
        if colour.get(node, BLACK) == BLACK:
            return
        colour[node] = GREY
        for nxt in graph.get(node, []):
            visit(nxt, trail + [node])
        colour[node] = BLACK

    for node in graph:
        visit(node, [])


def assert_mutable_header(path: pathlib.Path) -> None:
    lines = path.read_text().splitlines()
    hits = [n for n, line in enumerate(lines, 1) if line.strip() == MUTABLE_HEADER]
    assert len(hits) == 1, f"{path}: {len(hits)} `{MUTABLE_HEADER}` lines, expected exactly 1"
    above, _, below = path.read_text().partition(MUTABLE_HEADER)
    assert above.strip(), f"{path}: nothing above the header, so persona.md would be empty"
    assert below.strip(), f"{path}: nothing below the header"
    assert path.parent.name in above, f"{path}: the persona does not name its own id"


def assert_addendum_is_prose(path: pathlib.Path) -> None:
    """`hx resume` appends it verbatim beneath `## Order` (spec 06, 08), so a `##` heading
    would break the work item's structure."""
    text = path.read_text()
    assert text.strip(), f"{path} is empty"
    assert not re.search(r"^## ", text, re.MULTILINE), (
        f"{path}: must not introduce `##` headings; it is appended under `## Order`"
    )
    assert not text.startswith("---\n"), f"{path}: an addendum carries no frontmatter"


def expected_line(path_col: str, after: list[str], outcome: str | None, has_goal: bool) -> str:
    """One `hx board` text line (spec 08)."""
    return "  ".join((
        path_col,
        ",".join(after) or "-",
        outcome or "-",
        "0",
        "<ts>" if has_goal else "-",
    ))


def assert_expected_board(
    expected_dir: pathlib.Path, stem: str, states: dict[str, State], *, worker_pod: str
) -> None:
    """The checked-in file agrees with the state this step is in, column by column."""
    lines = (expected_dir / f"{stem}.txt").read_text().strip().splitlines()
    assert len(lines) == len(states), f"{stem}: {len(lines)} lines for {len(states)} ids"
    # partner first, then by id (CONTRACTS.md).
    order = ["partner", *sorted(i for i in states if i != "partner")]
    for line, item_id in zip(lines, order):
        state, outcome, after, has_goal = states[item_id]
        pod = pod_of(item_id, worker_pod)
        want = expected_line(f"pods/{pod}/{item_id}-{state}.md", after, outcome, has_goal)
        assert line == want, f"{stem}: {line!r}\nexpected {want!r}"


def assert_spec_06_invariants(steps: list[tuple[str, dict[str, State]]]) -> None:
    """A `queued` item has an unmet `after` and no goal marker; a `working` item has one."""
    for stem, states in steps:
        for item_id, (state, _, after, has_goal) in states.items():
            if state == "queued":
                assert not has_goal, f"{stem}/{item_id}: queued with a goal marker"
                assert after, f"{stem}/{item_id}: queued with an empty `after`"
            if state == "working":
                assert has_goal, f"{stem}/{item_id}: working with no goal marker"
