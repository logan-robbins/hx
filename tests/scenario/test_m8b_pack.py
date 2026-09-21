"""The m8b scenario pack: a `decision` nobody scripted.

m8 tells its worker to stop and ask, because a test pack needs one deterministic path through
that outcome. m8b does not mention a decision anywhere. Its one goal contradicts itself in a
single documented way, and its `### Checks` are deliberately neutral between the two readings,
so there is no path that satisfies the checks while dodging the question.

What this module can assert is that the pack stays *set up* that way: that the contradiction is
still present in both halves of the goal, that the checks still resolve neither side, that
nothing in the pack names the `decision` outcome at the worker, and that the six expected
boards are what `hx board` really prints. Whether the ambiguity is actually noticed is M8's to
find out with a live agent — that is the experiment, and it cannot be unit-tested.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import packlib  # noqa: E402

PACK = pathlib.Path(__file__).resolve().parent / "m8b"
GOALS = PACK / "goals"
EXPECTED = PACK / "expected"
CONFIG = PACK / "config"
FIXTURE_REPO = pathlib.Path(__file__).resolve().parent / "m8" / "repo"

WORKER = "eng-001"
POD = "engineers"

#: The six observation points of README.md's table.
STEPS: list[tuple[str, dict[str, packlib.State]]] = [
    ("01-eng-001-working", {WORKER: ("working", None, True)}),
    ("02-eng-001-decision", {WORKER: ("complete", "decision", False)}),
    ("03-eng-001-resumed", {WORKER: ("working", None, True)}),
    ("04-eng-001-done", {WORKER: ("complete", "done", False)}),
]

ORDER_FILES = [GOALS / f"{WORKER}.md"]


def _ids(paths):
    return [str(p.relative_to(PACK)) for p in paths]


# ------------------------------------------------------------------ the pack exists


def test_the_pack_ships_what_the_readme_names():
    assert (PACK / "README.md").is_file()
    assert (PACK / "chat.md").is_file()
    assert sorted(p.name for p in GOALS.iterdir()) == [
        "eng-001.addendum.md", "eng-001.md",
    ], "the Partner has no goal file of its own (spec 12, D25)"
    assert sorted(p.name for p in CONFIG.iterdir()) == [WORKER]


def test_it_reuses_m8s_fixture_repo_rather_than_shipping_a_second_one():
    assert not (PACK / "repo").exists(), "m8b uses ../m8/repo; a second copy would drift"
    assert FIXTURE_REPO.is_dir()
    assert "--json" not in (FIXTURE_REPO / "greet.py").read_text(), (
        "the fixture already has --json, so this goal would pass on arrival"
    )


def test_it_is_smaller_than_m8():
    """The point of a second pack is that it is cheap enough to run on every prompt change."""
    m8_steps = len(list((pathlib.Path(__file__).resolve().parent / "m8" / "expected").glob("*.txt")))
    assert len(STEPS) < m8_steps, (len(STEPS), m8_steps)
    assert len(list(CONFIG.iterdir())) == 1, "one worker, no `after` chain"


# ---------------------------------------------------------------------- the goals


@pytest.mark.parametrize("path", ORDER_FILES, ids=_ids(ORDER_FILES))
def test_order_parses_with_the_function_dispatch_uses(path):
    from hx import goals

    goal = goals.parse_goal(path)
    assert packlib.checks_commands(goal.checks), f"{path}: no commands under `### Checks`"


def test_addendum_is_prose():
    packlib.assert_addendum_is_prose(GOALS / f"{WORKER}.addendum.md")


# ------------------------------------------------------------------ the ambiguity


def test_both_halves_of_the_contradiction_are_still_there():
    """The whole scenario is this one pair of sentences. If a tidy-up removes either, m8b
    silently becomes an ordinary goal that anyone can finish."""
    text = (GOALS / f"{WORKER}.md").read_text()
    order_half, _, done_half = text.partition("## Definition of done")
    assert "and nothing else on stdout" in order_half, (
        "`## Goal` no longer demands JSON and nothing else"
    )
    assert "still prints the human-readable greeting line first" in done_half, (
        "`## Definition of done` no longer demands the greeting line first"
    )


def test_the_checks_do_not_resolve_the_ambiguity():
    """Neutral checks are what make the question unavoidable. A check asserting either shape
    would let a worker satisfy the block and finish without deciding anything."""
    from hx import goals

    checks = goals.parse_goal(GOALS / f"{WORKER}.md").checks
    # Would force JSON-only:
    assert "json.load(sys.stdin)" not in checks, checks
    assert "-qx" not in checks.split("--json")[-1], (
        "an exact-line check on --json output would pick a reading"
    )
    # Would force the greeting-first reading:
    for forcing in ("head -1", "head -n 1", "sed -n '1p'", "grep -c"):
        assert forcing not in checks, f"{forcing!r} in the checks picks a reading"


def test_nothing_in_the_pack_tells_the_worker_to_reach_decision():
    """m8's goal says `hx complete decision` in as many words. m8b must not, anywhere the
    worker can see — not in the goal, and not in its persona."""
    worker_visible = [
        GOALS / f"{WORKER}.md",
        CONFIG / WORKER / "AGENTS.md",
    ]
    for path in worker_visible:
        text = path.read_text()
        assert "hx complete decision" not in text, f"{path} instructs the decision outcome"
        assert "complete decision" not in text, f"{path} instructs the decision outcome"


def test_the_persona_teaches_the_behaviour_without_naming_the_answer():
    """The behaviour under test lives in the persona and the hx-worker skill, so the persona
    has to say what to do with a self-contradicting goal — in general terms, not about this
    goal."""
    text = (CONFIG / WORKER / "AGENTS.md").read_text()
    assert "## Open decision" in text
    assert "--json" not in text, "the persona must not know about this particular goal"
    assert "greeting" not in text


def test_the_addendum_withdraws_the_losing_criterion():
    """The `## Definition of done` is what the goal evaluator judges. An addendum that answers
    the question without retracting the other half leaves the worker unable to satisfy its own
    item."""
    text = (GOALS / f"{WORKER}.addendum.md").read_text()
    assert "withdraw" in text.lower(), text
    assert "criterion 3" in text.lower()
    assert "JSON only" in text


# --------------------------------------------------------------- README and expected


def readme_steps() -> list[str]:
    text = (PACK / "README.md").read_text()
    return re.findall(r"`expected/([0-9]{2}-[a-z0-9-]+)\.txt`", text)


def test_the_readme_and_expected_cover_the_same_steps():
    named = readme_steps()
    shipped = sorted(p.stem for p in EXPECTED.glob("*.txt"))
    assert named, "the README names no expected/ file"
    assert sorted(set(named)) == shipped, (sorted(set(named)), shipped)
    assert [s for s, _ in STEPS] == shipped, "this module's STEPS has drifted from expected/"


def test_the_readme_documents_the_two_ways_to_pass_and_the_one_way_to_fail():
    text = (PACK / "README.md").read_text()
    assert "before dispatching" in text, "the early-catch route is not documented"
    assert "silent" in text.lower(), "the failure mode is not named"
    for transition in ("idle → working", "working → complete", "complete → working"):
        assert transition in text, f"the README does not show the {transition} transition"


@pytest.mark.parametrize("stem,states", STEPS, ids=[s for s, _ in STEPS])
def test_expected_board_is_what_hx_board_actually_prints(stem, states, tmp_path):
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
