"""`hx learn` — the central learnings file and the reviewed way into it (docs/learnings.md).

A proposal never touches the file; a different session accepts it as a new numbered rule or
merges its evidence into the rule that already says it; the lint keeps the file one rule per
lesson. The fleet case: several instances share one file through `config/hx.json`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from hx import learn
from hx.errors import NotFound, ValidationError

from .conftest import clean_env

ENG = {"HARNESS_ID": "eng-001"}
PARTNER = {"HARNESS_ID": "partner"}


def run_learn(root: Path, *args: str, harness_id: str | None = "eng-001", **env_extra: str):
    env = clean_env(HARNESS_ROOT=str(root), **env_extra)
    if harness_id:
        env["HARNESS_ID"] = harness_id
    env["HOME"] = str(root.parent / "fake-home")
    Path(env["HOME"]).mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-m", "hx", "learn", *args], env=env,
                          capture_output=True, text=True)


# --- the file -----------------------------------------------------------------------------------


def test_the_skeleton_ships_an_empty_learnings_file(instance):
    assert (instance / "LEARNINGS.md").is_file()
    assert learn.read_rules(instance / "LEARNINGS.md") == []
    assert learn.learnings_path(instance, {}) == instance / "LEARNINGS.md"


def test_a_rule_line_parses_into_its_three_fields():
    rule = learn.parse_rule(
        "**Rule T4** · Signal: anything on a temp disk · Check: local NVMe holds caches only · Evidence: box upgrade 2026-10-10"
    )
    assert rule["key"] == "T4" and rule["prefix"] == "T" and rule["number"] == 4
    assert rule["signal"] == "anything on a temp disk"
    assert rule["check"] == "local NVMe holds caches only"
    assert rule["evidence"] == "box upgrade 2026-10-10"
    assert learn.parse_rule("- a bullet · Signal: not a rule line") is None


def test_the_file_can_live_anywhere_the_instance_points(instance, tmp_path):
    shared = tmp_path / "fleet" / "shared" / "LEARNINGS.md"
    hx_json = instance / "config" / "hx.json"
    recorded = json.loads(hx_json.read_text()) if hx_json.is_file() else {}
    recorded["learnings"] = str(shared)
    recorded["learnings_prefix"] = "T"
    hx_json.write_text(json.dumps(recorded))
    assert learn.learnings_path(instance, {}) == shared
    assert learn.default_prefix(instance, {}) == "T"
    # The environment wins over the file, so one shell can address another file.
    assert learn.learnings_path(instance, {"HX_LEARNINGS": "/elsewhere/L.md"}) == Path("/elsewhere/L.md")
    proposal = learn.propose(instance, signal="s", check="c", evidence="e", env=ENG)
    assert Path(proposal["learnings"]) == shared
    assert (shared.parent / "learnings" / "proposals" / f"{proposal['id']}.json").is_file()
    learn.accept(instance, proposal["id"], env=PARTNER)
    assert learn.read_rules(shared)[0]["key"] == "T1"


# --- propose ------------------------------------------------------------------------------------


def test_propose_writes_a_proposal_and_never_the_file(instance):
    before = (instance / "LEARNINGS.md").read_bytes()
    proposal = learn.propose(instance, signal="a count quoted without its scope",
                             check="every count carries suite, selection and omissions",
                             evidence="FLAWS_3 step 3", env={**ENG, "HX_ROLE": "qa-engineer"})
    assert (instance / "LEARNINGS.md").read_bytes() == before
    assert proposal["author"] == "eng-001" and proposal["role"] == "qa-engineer"
    assert proposal["instance"] == "instance" and proposal["prefix"] == "L"
    assert proposal["nearest"] == []
    assert learn.list_proposals(instance / "LEARNINGS.md")[0]["id"] == proposal["id"]


def test_propose_needs_all_three_fields(instance):
    with pytest.raises(ValidationError):
        learn.propose(instance, signal="s", check="c", evidence="", env=ENG)
    with pytest.raises(ValidationError):
        learn.propose(instance, signal="", check="c", evidence="e", env=ENG)


def test_propose_shows_the_nearest_rules_so_a_twin_is_seen_before_it_is_filed(instance):
    first = learn.propose(instance, signal="a count quoted without its scope",
                          check="every count carries suite, selection and omissions",
                          evidence="first", env=ENG)
    learn.accept(instance, first["id"], env=PARTNER)
    twin = learn.propose(instance, signal="a count quoted without scope",
                         check="counts carry suite, selection, omissions", evidence="again", env=ENG)
    assert twin["nearest"] and twin["nearest"][0]["rule"] == "L1"
    assert twin["nearest"][0]["score"] >= learn.DUPLICATE_SCORE
    other = learn.propose(instance, signal="a reviewer read a different worktree than the one under test",
                          check="every review names the exact revision it read", evidence="x", env=ENG)
    assert not other["nearest"] or other["nearest"][0]["score"] < learn.DUPLICATE_SCORE


def test_propose_evidence_for_an_existing_rule(instance):
    first = learn.propose(instance, signal="s", check="c", evidence="e", env=ENG)
    learn.accept(instance, first["id"], env=PARTNER)
    more = learn.propose(instance, signal="", check="", evidence="seen again in run 7",
                         evidence_for="l1", env=ENG)
    assert more["evidence_for"] == "L1" and more["signal"] == "s"
    with pytest.raises(NotFound):
        learn.propose(instance, signal="", check="", evidence="e", evidence_for="L9", env=ENG)
    record = learn.accept(instance, more["id"], env=PARTNER)
    assert record["decision"] == "merged" and record["rule"] == "L1"
    rules = learn.read_rules(instance / "LEARNINGS.md")
    assert len(rules) == 1 and "seen again in run 7" in rules[0]["evidence"]


# --- accept and reject --------------------------------------------------------------------------


def test_accept_numbers_the_rule_and_records_the_decision(instance):
    proposal = learn.propose(instance, signal="s1", check="c1", evidence="e1", env=ENG)
    record = learn.accept(instance, proposal["id"], why="general", env=PARTNER)
    assert record == {**record, "decision": "new", "rule": "L1", "reviewer": "partner",
                      "author": "eng-001", "why": "general"}
    rules = learn.read_rules(instance / "LEARNINGS.md")
    assert [rule["key"] for rule in rules] == ["L1"]
    assert rules[0]["signal"] == "s1" and rules[0]["check"] == "c1"
    assert "e1 (from eng-001@instance, " in rules[0]["evidence"]
    decisions = (instance / "learnings" / "decisions.jsonl").read_text().splitlines()
    assert json.loads(decisions[-1])["rule"] == "L1"
    assert not (instance / "learnings" / "proposals").glob("*.json").__class__ is None
    assert list((instance / "learnings" / "proposals").glob("*.json")) == []
    assert (instance / "learnings" / "accepted" / f"{proposal['id']}.json").is_file()
    second = learn.propose(instance, signal="s2", check="c2", evidence="e2", env=ENG)
    assert learn.accept(instance, second["id"], env=PARTNER)["rule"] == "L2"


def test_the_session_that_proposed_cannot_accept(instance):
    proposal = learn.propose(instance, signal="s", check="c", evidence="e", env=ENG)
    with pytest.raises(ValidationError):
        learn.accept(instance, proposal["id"], env=ENG)
    with pytest.raises(ValidationError):
        learn.reject(instance, proposal["id"], why="no", env=ENG)
    # The human (no HARNESS_ID) may always decide, including on its own proposals.
    own = learn.propose(instance, signal="s2", check="c2", evidence="e2", env={})
    assert learn.accept(instance, own["id"], env={})["rule"] == "L1"


def test_the_reviewer_may_generalise_the_wording(instance):
    proposal = learn.propose(instance, signal="PHPSOD2D threw ArgumentException on panel 1",
                             check="run Phpsod2dFieldHelpTests", evidence="PR #1885", env=ENG)
    learn.accept(instance, proposal["id"], signal="an exception type asserted differs from the one raised",
                 check="the assertion names the exact exception the source raises", env=PARTNER)
    rule = learn.read_rules(instance / "LEARNINGS.md")[0]
    assert rule["signal"] == "an exception type asserted differs from the one raised"
    assert "PR #1885" in rule["evidence"]


def test_accept_keeps_each_prefix_together(instance):
    for prefix, signal in (("S", "s-one"), ("T", "t-one"), ("S", "s-two")):
        proposal = learn.propose(instance, signal=signal, check="c", evidence="e", prefix=prefix, env=ENG)
        learn.accept(instance, proposal["id"], env=PARTNER)
    keys = [rule["key"] for rule in learn.read_rules(instance / "LEARNINGS.md")]
    assert keys == ["S1", "S2", "T1"]


def test_reject_records_why_and_files_nothing(instance):
    proposal = learn.propose(instance, signal="s", check="c", evidence="e", env=ENG)
    with pytest.raises(ValidationError):
        learn.reject(instance, proposal["id"], why="  ", env=PARTNER)
    record = learn.reject(instance, proposal["id"], why="one-off, not a category", env=PARTNER)
    assert record["decision"] == "rejected"
    assert learn.read_rules(instance / "LEARNINGS.md") == []
    assert (instance / "learnings" / "rejected" / f"{proposal['id']}.json").is_file()
    with pytest.raises(NotFound):
        learn.accept(instance, proposal["id"], env=PARTNER)


def test_concurrent_accepts_never_share_a_number(instance):
    proposals = [learn.propose(instance, signal=f"s{i}", check=f"c{i}", evidence="e", env=ENG)
                 for i in range(12)]
    errors: list[BaseException] = []

    def run(proposal):
        try:
            learn.accept(instance, proposal["id"], env=PARTNER)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(p,)) for p in proposals]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    keys = [rule["key"] for rule in learn.read_rules(instance / "LEARNINGS.md")]
    assert sorted(keys, key=lambda k: int(k[1:])) == [f"L{i}" for i in range(1, 13)]
    assert len(set(keys)) == 12


# --- check --------------------------------------------------------------------------------------


def test_check_passes_a_clean_file_and_counts_pending(instance):
    rows = learn.check(instance)
    assert all(status == "ok" for status, _ in rows), rows
    learn.propose(instance, signal="s", check="c", evidence="e", env=ENG)
    assert ("warn", "1 pending proposal(s) (hx learn pending)") in learn.check(instance)


def test_check_fails_duplicate_numbers_and_missing_fields(instance):
    path = instance / "LEARNINGS.md"
    path.write_text(path.read_text() + "\n".join([
        "**Rule L1** · Signal: a · Check: b · Evidence: c",
        "**Rule L1** · Signal: d · Check: e · Evidence: f",
        "**Rule L2** · Signal: only a signal",
        "**Rule nonsense",
    ]) + "\n")
    rows = learn.check(instance)
    fails = [detail for status, detail in rows if status == "fail"]
    assert any("L1 is also line" in detail for detail in fails)
    assert any("L2 has no Check, Evidence" in detail for detail in fails)
    assert any("starts like a rule but is not" in detail for detail in fails)


def test_check_warns_on_twins(instance):
    path = instance / "LEARNINGS.md"
    path.write_text(path.read_text() + "\n".join([
        "**Rule L1** · Signal: a count quoted without its scope · Check: every count carries suite, selection and omissions · Evidence: c",
        "**Rule L2** · Signal: a count quoted without scope · Check: counts carry suite, selection, omissions · Evidence: f",
    ]) + "\n")
    assert any("L1 and L2 look like one rule" in detail for status, detail in learn.check(instance) if status == "warn")


# --- the CLI ------------------------------------------------------------------------------------


def test_cli_round_trip(instance):
    result = run_learn(instance, "propose", "--signal", "a count without scope",
                       "--check", "counts carry their scope", "--evidence", "FLAWS_3")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("HX-LEARN-PROPOSED ")
    proposal_id = result.stdout.split()[1]

    result = run_learn(instance, "accept", proposal_id)
    assert result.returncode != 0 and "different session" in result.stderr

    result = run_learn(instance, "pending", harness_id="partner")
    assert result.returncode == 0 and proposal_id in result.stdout and "HX-LEARN-PENDING 1" in result.stdout

    result = run_learn(instance, "accept", proposal_id, "--why", "category", harness_id="partner")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"HX-LEARN new {proposal_id} rule=L1"

    result = run_learn(instance, "list")
    assert "L1     a count without scope" in result.stdout and "HX-LEARN-LIST 1 rule(s)" in result.stdout
    assert run_learn(instance, "list", "L1").stdout.startswith("**Rule L1** · Signal: a count without scope")

    result = run_learn(instance, "check")
    assert result.returncode == 0 and "HX-LEARN-CHECK rules=1 pending=0 fails=0" in result.stdout

    assert run_learn(instance, "path").stdout.strip() == str(instance / "LEARNINGS.md")

    twin = run_learn(instance, "propose", "--signal", "a count without its scope",
                     "--check", "counts carry their scope", "--evidence", "again")
    assert "reads like L1" in twin.stderr


def test_cli_propose_from_a_file(instance, tmp_path):
    body = tmp_path / "learning.md"
    body.write_text("Signal: the pane was dead\nCheck: HX_TMUX names the server\nEvidence: ui on 8801\n")
    result = run_learn(instance, "propose", "--file", str(body), "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["check"] == "HX_TMUX names the server"
