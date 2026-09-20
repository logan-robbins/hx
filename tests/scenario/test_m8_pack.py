"""The M8 scenario pack, checked against the spec and against the real `hx board`.

`tests/scenario/m8/` is the concrete data the build lane runs spec 13 M8 with. Its value is
entirely in being *true* — an order that `hx dispatch` would refuse, or an expected board that
`hx board` would never print, is worse than no pack at all, because it gets believed.

So this module does two kinds of check:

* **Structural**, always: every order parses with `hx.orders.parse_order` (the same function
  `hx dispatch` uses), the `after` graph is acyclic and names real ids, every `AGENTS.md` has
  its mutable header, the fixture repo's tripwires are intact, and `expected/` covers exactly
  the steps the README's sequence table names — no more, no fewer.
* **Live**, when tmux is available: for each of the eight steps, build a scratch instance in
  exactly the state the README says that step reaches, run the real `hx board`, and compare its
  text to the checked-in `expected/` file. Timestamps are normalised to `<ts>`.

The live half is what keeps the pack honest as the build lane's CLI lands. It constructs each
state by writing the files hx would have written, rather than by running `hx dispatch` and
`hx complete` — those are build-2 and do not exist yet — so it asserts the *shape* of each
step, not the transitions that produce it. M8 itself asserts the transitions.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PACK = pathlib.Path(__file__).resolve().parent / "m8"
ORDERS = PACK / "orders"
EXPECTED = PACK / "expected"
CONFIG = PACK / "config"
FIXTURE_REPO = PACK / "repo"

sys.path.insert(0, str(REPO / "src"))

MUTABLE_HEADER = "## UPDATES BELOW ONLY"
WORKERS = ("eng-001", "eng-002")
POD = "engineers"

#: A fixed instant, written into every marker so `hx board` prints something deterministic.
#: Normalised back to `<ts>` before comparing, so the checked-in files carry no fake data.
FIXED_TS = "2026-09-20T12:00:00Z"
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")

#: (expected filename stem, {id: (state, outcome, after, goal marker?)}) for each observation
#: point in README.md. `goal` is the `run/<id>/goal` marker, which `hx complete` removes.
STEPS: list[tuple[str, dict[str, tuple[str, str | None, list[str], bool]]]] = [
    ("01-partner-working", {
        "partner": ("working", None, [], True),
        "eng-001": ("idle", None, [], False),
        "eng-002": ("idle", None, [], False),
    }),
    ("02-plan-dispatched", {
        "partner": ("working", None, [], True),
        "eng-001": ("working", None, [], True),
        "eng-002": ("queued", None, ["eng-001"], False),
    }),
    ("03-eng-001-done", {
        "partner": ("working", None, [], True),
        "eng-001": ("complete", "done", [], False),
        "eng-002": ("working", None, ["eng-001"], True),
    }),
    ("04-eng-002-decision", {
        "partner": ("working", None, [], True),
        "eng-001": ("complete", "done", [], False),
        "eng-002": ("complete", "decision", ["eng-001"], False),
    }),
    ("05-eng-002-resumed", {
        "partner": ("working", None, [], True),
        "eng-001": ("complete", "done", [], False),
        "eng-002": ("working", None, ["eng-001"], True),
    }),
    ("06-all-done", {
        "partner": ("working", None, [], True),
        "eng-001": ("complete", "done", [], False),
        "eng-002": ("complete", "done", ["eng-001"], False),
    }),
    ("07-partner-done", {
        "partner": ("complete", "done", [], False),
        "eng-001": ("complete", "done", [], False),
        "eng-002": ("complete", "done", ["eng-001"], False),
    }),
    ("08-benched", {
        "partner": ("complete", "done", [], False),
        "eng-001": ("idle", "done", [], False),
        "eng-002": ("idle", "done", ["eng-001"], False),
    }),
]


def order_files() -> list[pathlib.Path]:
    """Order-shaped files only: the addendum is prose appended under `## Order` (spec 06)."""
    return sorted(p for p in ORDERS.glob("*.md") if not p.name.endswith(".addendum.md"))


def agents_files() -> list[pathlib.Path]:
    return sorted(CONFIG.rglob("AGENTS.md"))


def _ids(paths):
    return [str(p.relative_to(PACK)) for p in paths]


# ------------------------------------------------------------------ the pack exists


def test_the_pack_has_every_file_gtm_3_names():
    assert (PACK / "README.md").is_file()
    assert (PACK / "chat.md").is_file()
    assert sorted(p.name for p in ORDERS.iterdir()) == [
        "eng-001.md", "eng-002.addendum.md", "eng-002.md", "partner.md",
    ]
    assert sorted(p.name for p in CONFIG.iterdir()) == list(WORKERS)
    assert FIXTURE_REPO.is_dir(), "the orders' checks run against tests/scenario/m8/repo/"


# ----------------------------------------------------------------------- the orders


@pytest.mark.parametrize("path", order_files(), ids=_ids(order_files()))
def test_order_parses_with_the_function_dispatch_uses(path):
    """`hx.orders.parse_order` is what `hx dispatch` validates with. If it refuses one of
    these, M8 stops at its first command."""
    from hx import orders

    order = orders.parse_order(path)
    assert order.checks.strip(), f"{path}: empty `### Checks` block; dispatch refuses it"


@pytest.mark.parametrize("path", order_files(), ids=_ids(order_files()))
def test_order_checks_are_runnable_commands(path):
    from hx import orders

    commands = [
        line for line in orders.parse_order(path).checks.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert commands, f"{path}: `### Checks` has no commands"
    for line in commands:
        assert not line.startswith(" "), f"{path}: indented check line {line!r}"


def test_the_after_graph_is_acyclic_and_names_real_ids():
    from hx import orders

    graph = {p.stem: list(orders.parse_order(p).after) for p in order_files()}
    known = set(graph) | {"partner"}
    for item_id, after in graph.items():
        for dep in after:
            assert dep in known, f"{item_id}: `after` names {dep!r}, which the pack does not ship"
            assert dep != item_id, f"{item_id}: depends on itself"

    # Depth-first cycle detection, so a longer chain added later is still caught.
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


def test_eng_002_waits_on_eng_001_which_is_what_makes_the_queued_state_real():
    from hx import orders

    assert orders.parse_order(ORDERS / "eng-002.md").after == ["eng-001"]
    assert orders.parse_order(ORDERS / "eng-001.md").after == []


def test_the_partner_checks_are_the_require_done_form_spec_12_names():
    from hx import orders

    checks = orders.parse_order(ORDERS / "partner.md").checks
    assert "hx board --require-done eng-001 eng-002" in checks, checks


def test_the_addendum_is_prose_that_lands_under_the_order_heading():
    """`hx resume` appends it verbatim beneath `## Order` (spec 06, 08), so a `##` heading
    would break the work item's structure."""
    text = (ORDERS / "eng-002.addendum.md").read_text()
    assert text.strip()
    assert not re.search(r"^## ", text, re.MULTILINE)
    assert not text.startswith("---\n"), "an addendum carries no frontmatter"


def test_the_addendum_answers_the_question_eng_002_is_told_to_ask():
    """The point of the `decision` step: the worker must not be able to infer the answer from
    its own order, and the addendum must actually settle it."""
    order = (ORDERS / "eng-002.md").read_text()
    addendum = (ORDERS / "eng-002.addendum.md").read_text()
    assert "hx complete decision" in order, "eng-002's order must route it to `decision`"
    assert "## Open decision" in order
    # Both options are stated in the order; exactly one is chosen in the addendum.
    assert "fall back to English" in order and "non-zero" in order
    assert "fall back to English" in addendum
    assert "stderr" in addendum and "exits 0" in addendum


def test_the_orders_checks_dont_pre_decide_the_open_question():
    """If `### Checks` already encoded the fallback behaviour, the worker could read the answer
    off its own definition of done instead of asking."""
    from hx import orders

    checks = orders.parse_order(ORDERS / "eng-002.md").checks
    assert "--lang xx" not in checks, checks
    assert "unknown language" not in checks, checks


# ------------------------------------------------------------------- the identities


def test_the_pack_ships_a_persona_for_each_worker():
    assert [p.parent.name for p in agents_files()] == list(WORKERS)


@pytest.mark.parametrize("path", agents_files(), ids=_ids(agents_files()))
def test_agents_file_has_exactly_one_mutable_header(path):
    lines = path.read_text().splitlines()
    hits = [n for n, line in enumerate(lines, 1) if line.strip() == MUTABLE_HEADER]
    assert len(hits) == 1, f"{path}: {len(hits)} `{MUTABLE_HEADER}` lines, expected exactly 1"


@pytest.mark.parametrize("path", agents_files(), ids=_ids(agents_files()))
def test_agents_file_has_a_persona_above_and_room_below(path):
    above, _, below = path.read_text().partition(MUTABLE_HEADER)
    assert above.strip(), f"{path}: nothing above the header, so persona.md would be empty"
    assert below.strip(), f"{path}: nothing below the header"
    assert path.parent.name in above, f"{path}: the persona does not name its own id"


# ------------------------------------------------------------------ the fixture repo


def test_the_fixture_repo_runs_and_starts_where_the_orders_assume():
    r = subprocess.run(
        ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=FIXTURE_REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    out = subprocess.run(
        ["python3", "greet.py", "World"], cwd=FIXTURE_REPO, capture_output=True, text=True,
    )
    assert out.stdout == "Hello, World!\n", out.stdout


def test_the_fixture_repo_does_not_already_do_the_work():
    """If `--upper` or `--lang` already worked, both orders would pass their checks on arrival
    and M8 would prove nothing."""
    for flag in ("--upper", "--lang"):
        r = subprocess.run(
            ["python3", "greet.py", flag, "World"],
            cwd=FIXTURE_REPO, capture_output=True, text=True,
        )
        assert r.returncode != 0, f"{flag} already works in the fixture repo"


def test_the_tripwires_are_present_and_loud():
    claude_md = (FIXTURE_REPO / "CLAUDE.md").read_text()
    assert "TRIPWIRE-CLAUDE-MD-LOADED" in claude_md, (
        "the CLAUDE.md tripwire must make an agent that loaded it identifiable"
    )
    settings = json.loads((FIXTURE_REPO / ".claude" / "settings.json").read_text())
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "TRIPWIRE-REPO-CLAUDE-DIR-LOADED" in hook


# --------------------------------------------------------- README and expected agree


def readme_steps() -> list[str]:
    """The `expected/` filenames the README's sequence table names, in order."""
    text = (PACK / "README.md").read_text()
    return re.findall(r"`expected/([0-9]{2}-[a-z0-9-]+)\.txt`", text)


def test_the_readme_sequence_and_expected_cover_exactly_the_same_steps():
    named = readme_steps()
    shipped = sorted(p.stem for p in EXPECTED.glob("*.txt"))
    assert named, "the README names no expected/ file; the sequence table is the index"
    assert named == sorted(set(named)), f"the README names a step twice: {named}"
    assert sorted(named) == shipped, (
        f"README names {sorted(named)};\nexpected/ holds {shipped}"
    )
    assert [s for s, _ in STEPS] == shipped, (
        "this module's STEPS table has drifted from expected/"
    )


def test_every_step_names_its_hx_command_and_its_spec_06_transition():
    text = (PACK / "README.md").read_text()
    for stem, _ in STEPS:
        assert f"`expected/{stem}.txt`" in text, stem
    for transition in ("idle → working", "idle → queued", "queued → working",
                       "working → complete", "complete → working", "complete → idle"):
        assert transition in text, f"the README does not show the {transition} transition"


@pytest.mark.parametrize("stem,states", STEPS, ids=[s for s, _ in STEPS])
def test_expected_board_is_well_formed_and_matches_the_step(stem, states):
    """Each line is `<file>  <after>  <outcome>  <open subagents>  <goal ts>` (spec 08), and
    the columns agree with the state this step is in."""
    lines = (EXPECTED / f"{stem}.txt").read_text().strip().splitlines()
    assert len(lines) == len(states), f"{stem}: {len(lines)} lines for {len(states)} ids"
    # partner first, then by id (CONTRACTS.md).
    order = ["partner", *sorted(WORKERS)]
    for line, item_id in zip(lines, order):
        file_col, after_col, outcome_col, subagents_col, goal_col = line.split("  ")
        state, outcome, after, has_goal = states[item_id]
        pod = "partner" if item_id == "partner" else POD
        assert file_col == f"pods/{pod}/{item_id}-{state}.md", f"{stem}: {line}"
        assert after_col == (",".join(after) or "-"), f"{stem}: {line}"
        assert outcome_col == (outcome or "-"), f"{stem}: {line}"
        assert subagents_col == "0", f"{stem}: {line}"
        assert goal_col == ("<ts>" if has_goal else "-"), f"{stem}: {line}"


def test_a_queued_item_never_carries_a_goal_marker():
    """Spec 06 invariant: a `queued` item has no goal marker, and a `working` item has one."""
    for stem, states in STEPS:
        for item_id, (state, _, after, has_goal) in states.items():
            if state == "queued":
                assert not has_goal, f"{stem}/{item_id}: queued with a goal marker"
                assert after, f"{stem}/{item_id}: queued with an empty `after`"
            if state == "working":
                assert has_goal, f"{stem}/{item_id}: working with no goal marker"


# ------------------------------------------------- the live check against `hx board`


def build_instance(root: pathlib.Path, states: dict) -> None:
    """Write the instance exactly as hx would have left it at this observation point."""
    subprocess.run(
        [sys.executable, "-m", "hx", "install", "--skeleton-only", "--root", str(root)],
        check=True, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )
    worker_template = root / "templates" / "worker"
    tasks: dict[str, dict] = {}
    for item_id, (state, outcome, after, has_goal) in states.items():
        pod = "partner" if item_id == "partner" else POD
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


def normalise(text: str) -> str:
    return TS_RE.sub("<ts>", text).strip()


@pytest.mark.parametrize("stem,states", STEPS, ids=[s for s, _ in STEPS])
def test_expected_board_is_what_hx_board_actually_prints(stem, states, tmp_path):
    """Build the instance in this step's state and diff the real `hx board` against the
    checked-in file. This is what stops `expected/` from being plausible-looking fiction."""
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not on PATH; `hx board` cannot tell which sessions are live")

    root = tmp_path / "hx"
    build_instance(root, states)

    # No tmux session is created. `hx board` matches a live session by the id itself, and
    # this suite must not create sessions named `partner` or `eng-001` on a machine that may
    # be running a real fleet. So the "no live tmux session" errors are expected here and are
    # stripped before comparing; M8 asserts session liveness for real.
    r = subprocess.run(
        [sys.executable, "-m", "hx", "board"],
        capture_output=True, text=True,
        env={**os.environ, "HARNESS_ROOT": str(root), "PYTHONPATH": str(REPO / "src")},
    )

    board_lines = [
        line for line in normalise(r.stdout).splitlines()
        if not line.endswith(tuple(f"no live tmux session {i}" for i in states))
    ]
    expected = (EXPECTED / f"{stem}.txt").read_text().strip().splitlines()
    assert board_lines == expected, (
        f"{stem}: `hx board` printed\n  " + "\n  ".join(board_lines)
        + "\nexpected/ holds\n  " + "\n  ".join(expected)
        + f"\n\nstderr:\n{r.stderr}"
    )
