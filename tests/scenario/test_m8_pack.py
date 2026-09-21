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
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import packlib  # noqa: E402

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
FIXED_TS = packlib.FIXED_TS

#: (expected filename stem, {id: (state, outcome, after, goal marker?)}) for each observation
#: point in README.md. `goal` is the `run/<id>/goal` marker, which `hx complete` removes.
STEPS: list[tuple[str, dict[str, packlib.State]]] = [
    ("01-eng-001-working", {
        "eng-001": ("working", None, True),
        "eng-002": ("idle", None, False),
    }),
    ("02-eng-001-done", {
        "eng-001": ("complete", "done", False),
        "eng-002": ("idle", None, False),
    }),
    ("03-eng-002-working", {
        "eng-001": ("complete", "done", False),
        "eng-002": ("working", None, True),
    }),
    ("04-eng-002-decision", {
        "eng-001": ("complete", "done", False),
        "eng-002": ("complete", "decision", False),
    }),
    ("05-eng-002-resumed", {
        "eng-001": ("complete", "done", False),
        "eng-002": ("working", None, True),
    }),
    ("06-all-done", {
        "eng-001": ("complete", "done", False),
        "eng-002": ("complete", "done", False),
    }),
    ("07-benched", {
        "eng-001": ("idle", "done", False),
        "eng-002": ("idle", "done", False),
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
        "eng-001.md", "eng-002.addendum.md", "eng-002.md",
    ], "the Partner has no order file of its own (spec 12, D25)"
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
    """Every board file is named in the README and every name is a real file.

    Not "named exactly once": the README has two tables that both walk the sequence — the
    transitions each step causes, and the commands that drive it — so each file is named in
    both. What must hold is that the *sets* agree, in both directions, and that this module's
    STEPS has not drifted from either.
    """
    named = readme_steps()
    shipped = sorted(p.stem for p in EXPECTED.glob("*.txt"))
    assert named, "the README names no expected/ file; the sequence table is the index"
    missing = sorted(set(shipped) - set(named))
    assert not missing, f"the README never names {missing}"
    unknown = sorted(set(named) - set(shipped))
    assert not unknown, f"the README names {unknown}, which expected/ does not hold"
    assert [s for s, _ in STEPS] == shipped, (
        "this module's STEPS table has drifted from expected/"
    )


def test_every_step_names_its_hx_command_and_its_spec_06_transition():
    text = (PACK / "README.md").read_text()
    for stem, _ in STEPS:
        assert f"`expected/{stem}.txt`" in text, stem
    for transition in ("idle → working", "working → complete",
                       "complete → working", "complete → idle"):
        assert transition in text, f"the README does not show the {transition} transition"


# ------------------------------------------------- the live check against `hx board`


@pytest.mark.parametrize("stem,states", STEPS, ids=[s for s, _ in STEPS])
def test_expected_board_is_what_hx_board_actually_prints(stem, states, tmp_path):
    """Build the instance in this step's state and diff the real `hx board` against the
    checked-in file. This is what stops `expected/` from being plausible-looking fiction.

    The instance building lives in `packlib` because m8b does exactly the same thing; two
    copies of it would drift the moment one pack gained a state the other did not.
    """
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not on PATH; `hx board` cannot tell which sessions are live")

    root = tmp_path / "hx"
    packlib.build_instance(root, states, worker_pod=POD)
    lines, stderr = packlib.real_board(root, states)
    expected = (EXPECTED / f"{stem}.txt").read_text().strip().splitlines()
    assert lines == expected, (
        f"{stem}: `hx board` printed\n  " + "\n  ".join(lines)
        + "\nexpected/ holds\n  " + "\n  ".join(expected)
        + f"\n\nstderr:\n{stderr}"
    )


@pytest.mark.parametrize("stem,states", STEPS, ids=[s for s, _ in STEPS])
def test_expected_board_agrees_with_the_step(stem, states):
    packlib.assert_expected_board(EXPECTED, stem, states, worker_pod=POD)


def test_no_cut_feature_survives_in_the_steps():
    packlib.assert_no_cut_features(STEPS)


def test_the_orders_declare_no_dependency():
    """Spec 14 D25 cut `after`. `eng-002` still depends on `eng-001`'s work — that is what the
    scenario is about — but the *Partner* sequences it by waiting, not a field hx reads."""
    for path in order_files():
        text = path.read_text()
        assert not text.startswith("---"), f"{path.name} still opens with frontmatter"
        assert "after:" not in text, f"{path.name} still declares `after`"
