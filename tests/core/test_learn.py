"""`hx learn` — the central learnings as one file per category, and the reviewed way into them
(docs/learnings.md).

A proposal names the category of the mistake it prevents and never touches a rule file; a
different session accepts it as a new numbered rule in that category (or the reviewer's) or
merges its evidence into the rule that already says it; the lint keeps the directory one rule
per lesson across every file. The fleet case: several instances share one directory through
`config/hx.json`. Every agent carries every rule in its system prompt.
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


def propose(root, signal="s", check="c", evidence="e", category="evidence", **kw):
    return learn.propose(root, signal=signal, check=check, evidence=evidence, category=category, **kw)


# --- the directory ------------------------------------------------------------------------------


def test_the_skeleton_ships_one_empty_file_per_category(instance):
    directory = instance / "LEARNINGS"
    assert learn.learnings_path(instance, {}) == directory
    assert sorted(p.name for p in directory.iterdir()) == sorted(f"{c}.md" for c in learn.CATEGORY_NAMES)
    for category in learn.CATEGORY_NAMES:
        assert (directory / f"{category}.md").read_text() == learn.category_header(category)
    assert learn.read_rules(directory) == []
    assert not (instance / "LEARNINGS.md").exists()


def test_the_taxonomy_is_closed_and_defined():
    assert len(learn.CATEGORIES) == 11
    assert all(name.islower() and name.isalpha() for name in learn.CATEGORY_NAMES)
    assert all(definition for _, definition in learn.CATEGORIES)
    assert learn.validate_category(" Delivery ") == "delivery"
    with pytest.raises(ValidationError):
        learn.validate_category("footguns")
    assert learn.category_of(Path("x/LEARNINGS/evidence.md")) == "evidence"
    assert learn.category_of(Path("x/LEARNINGS/README.md")) is None


def test_a_rule_line_parses_into_its_three_fields():
    rule = learn.parse_rule(
        "**Rule T4** · Signal: anything on a temp disk · Check: local NVMe holds caches only · Evidence: box upgrade 2026-10-10"
    )
    assert rule["key"] == "T4" and rule["prefix"] == "T" and rule["number"] == 4
    assert rule["signal"] == "anything on a temp disk"
    assert rule["check"] == "local NVMe holds caches only"
    assert rule["evidence"] == "box upgrade 2026-10-10"
    assert learn.parse_rule("- a bullet · Signal: not a rule line") is None


def test_the_directory_can_live_anywhere_the_instance_points(instance, tmp_path):
    shared = tmp_path / "fleet" / "shared" / "LEARNINGS"
    hx_json = instance / "config" / "hx.json"
    recorded = json.loads(hx_json.read_text()) if hx_json.is_file() else {}
    recorded["learnings"] = str(shared)
    recorded["learnings_prefix"] = "T"
    hx_json.write_text(json.dumps(recorded))
    assert learn.learnings_path(instance, {}) == shared
    assert learn.default_prefix(instance, {}) == "T"
    # The environment wins over the file, so one shell can address another directory.
    assert learn.learnings_path(instance, {"HX_LEARNINGS": "/elsewhere/L"}) == Path("/elsewhere/L")
    proposal = propose(instance, category="harness", env=ENG)
    assert Path(proposal["learnings"]) == shared
    assert (shared / "proposals" / f"{proposal['id']}.json").is_file()
    learn.accept(instance, proposal["id"], env=PARTNER)
    rules = learn.read_rules(shared)
    assert rules[0]["key"] == "T1" and rules[0]["category"] == "harness"
    assert (shared / "harness.md").read_text().startswith(learn.category_header("harness"))


def test_the_single_file_layout_is_refused_with_the_migration_pointer(instance):
    with pytest.raises(ValidationError, match="directory"):
        learn.learnings_path(instance, {"HX_LEARNINGS": "/fleet/shared/LEARNINGS.md"})


# --- propose ------------------------------------------------------------------------------------


def test_propose_writes_a_proposal_and_never_a_rule_file(instance):
    before = {p.name: p.read_bytes() for p in (instance / "LEARNINGS").glob("*.md")}
    proposal = learn.propose(instance, signal="a count quoted without its scope",
                             check="every count carries suite, selection and omissions",
                             evidence="FLAWS_3 step 3", category="evidence",
                             env={**ENG, "HX_ROLE": "qa-engineer"})
    assert {p.name: p.read_bytes() for p in (instance / "LEARNINGS").glob("*.md")} == before
    assert proposal["author"] == "eng-001" and proposal["role"] == "qa-engineer"
    assert proposal["instance"] == "instance" and proposal["prefix"] == "L"
    assert proposal["category"] == "evidence"
    assert proposal["nearest"] == []
    assert learn.list_proposals(instance / "LEARNINGS")[0]["id"] == proposal["id"]


def test_propose_needs_all_three_fields_and_a_known_category(instance):
    with pytest.raises(ValidationError):
        propose(instance, evidence="", env=ENG)
    with pytest.raises(ValidationError):
        propose(instance, signal="", env=ENG)
    with pytest.raises(ValidationError, match="category"):
        learn.propose(instance, signal="s", check="c", evidence="e", env=ENG)
    with pytest.raises(ValidationError, match="not one of"):
        propose(instance, category="vibes", env=ENG)


def test_propose_shows_the_nearest_rules_across_every_category(instance):
    first = learn.propose(instance, signal="a count quoted without its scope",
                          check="every count carries suite, selection and omissions",
                          evidence="first", category="evidence", env=ENG)
    learn.accept(instance, first["id"], env=PARTNER)
    # Filed under another category on purpose: a twin is a twin wherever it is proposed.
    twin = learn.propose(instance, signal="a count quoted without scope",
                         check="counts carry suite, selection, omissions", evidence="again",
                         category="delivery", env=ENG)
    assert twin["nearest"] and twin["nearest"][0]["rule"] == "L1"
    assert twin["nearest"][0]["score"] >= learn.DUPLICATE_SCORE
    assert twin["nearest"][0]["category"] == "evidence"
    other = learn.propose(instance, signal="a reviewer read a different worktree than the one under test",
                          check="every review names the exact revision it read", evidence="x",
                          category="evidence", env=ENG)
    assert not other["nearest"] or other["nearest"][0]["score"] < learn.DUPLICATE_SCORE


def test_propose_evidence_for_an_existing_rule_inherits_its_category(instance):
    first = propose(instance, category="runtime", env=ENG)
    learn.accept(instance, first["id"], env=PARTNER)
    more = learn.propose(instance, signal="", check="", evidence="seen again in run 7",
                         evidence_for="l1", env=ENG)
    assert more["evidence_for"] == "L1" and more["signal"] == "s" and more["category"] == "runtime"
    with pytest.raises(NotFound):
        learn.propose(instance, signal="", check="", evidence="e", evidence_for="L9", env=ENG)
    record = learn.accept(instance, more["id"], env=PARTNER)
    assert record["decision"] == "merged" and record["rule"] == "L1" and record["category"] == "runtime"
    rules = learn.read_rules(instance / "LEARNINGS")
    assert len(rules) == 1 and "seen again in run 7" in rules[0]["evidence"]
    assert rules[0]["file"].endswith("runtime.md")


# --- accept and reject --------------------------------------------------------------------------


def test_accept_numbers_the_rule_and_files_it_under_its_category(instance):
    proposal = propose(instance, signal="s1", check="c1", evidence="e1", category="testing", env=ENG)
    record = learn.accept(instance, proposal["id"], why="general", env=PARTNER)
    assert record == {**record, "decision": "new", "rule": "L1", "category": "testing",
                      "reviewer": "partner", "author": "eng-001", "why": "general"}
    rules = learn.read_rules(instance / "LEARNINGS")
    assert [(rule["key"], rule["category"]) for rule in rules] == [("L1", "testing")]
    assert rules[0]["signal"] == "s1" and rules[0]["check"] == "c1"
    assert "e1 (from eng-001@instance, " in rules[0]["evidence"]
    text = (instance / "LEARNINGS" / "testing.md").read_text()
    assert text.startswith(learn.category_header("testing"))
    assert text.index("## Rules") < text.index("\n**Rule L1**"), "the header's indented example is not a rule"
    decisions = (instance / "LEARNINGS" / "decisions.jsonl").read_text().splitlines()
    assert json.loads(decisions[-1])["rule"] == "L1" and json.loads(decisions[-1])["category"] == "testing"
    assert list((instance / "LEARNINGS" / "proposals").glob("*.json")) == []
    assert (instance / "LEARNINGS" / "accepted" / f"{proposal['id']}.json").is_file()
    # Numbers are unique across the whole directory, not per file.
    second = propose(instance, signal="s2", check="c2", evidence="e2", category="build", env=ENG)
    assert learn.accept(instance, second["id"], env=PARTNER)["rule"] == "L2"
    assert (instance / "LEARNINGS" / "build.md").read_text().count("**Rule L2**") == 1
    assert [rule["key"] for rule in learn.read_rules(instance / "LEARNINGS")] == ["L2", "L1"], \
        "read in taxonomy order (build before testing)"


def test_the_reviewer_may_refile_under_another_category(instance):
    proposal = propose(instance, category="domain", env=ENG)
    record = learn.accept(instance, proposal["id"], category="evidence", env=PARTNER)
    assert record["category"] == "evidence"
    assert learn.read_rules(instance / "LEARNINGS")[0]["category"] == "evidence"
    assert "\n**Rule L1**" not in (instance / "LEARNINGS" / "domain.md").read_text()
    bad = propose(instance, env=ENG)
    with pytest.raises(ValidationError):
        learn.accept(instance, bad["id"], category="nope", env=PARTNER)


def test_a_missing_category_file_is_created_on_accept(instance):
    (instance / "LEARNINGS" / "harness.md").unlink()
    proposal = propose(instance, category="harness", env=ENG)
    learn.accept(instance, proposal["id"], env=PARTNER)
    text = (instance / "LEARNINGS" / "harness.md").read_text()
    assert text.startswith(learn.category_header("harness")) and "**Rule L1**" in text


def test_the_session_that_proposed_cannot_accept(instance):
    proposal = propose(instance, env=ENG)
    with pytest.raises(ValidationError):
        learn.accept(instance, proposal["id"], env=ENG)
    with pytest.raises(ValidationError):
        learn.reject(instance, proposal["id"], why="no", env=ENG)
    # The human (no HARNESS_ID) may always decide, including on its own proposals.
    own = propose(instance, signal="s2", check="c2", evidence="e2", env={})
    assert learn.accept(instance, own["id"], env={})["rule"] == "L1"


def test_the_reviewer_may_generalise_the_wording(instance):
    proposal = learn.propose(instance, signal="PHPSOD2D threw ArgumentException on panel 1",
                             check="run Phpsod2dFieldHelpTests", evidence="PR #1885",
                             category="testing", env=ENG)
    learn.accept(instance, proposal["id"], signal="an exception type asserted differs from the one raised",
                 check="the assertion names the exact exception the source raises", env=PARTNER)
    rule = learn.read_rules(instance / "LEARNINGS")[0]
    assert rule["signal"] == "an exception type asserted differs from the one raised"
    assert "PR #1885" in rule["evidence"]


def test_each_prefix_numbers_independently_across_files(instance):
    for prefix, category, signal in (("S", "build", "s-one"), ("T", "evidence", "t-one"), ("S", "evidence", "s-two")):
        proposal = propose(instance, signal=signal, category=category, prefix=prefix, env=ENG)
        learn.accept(instance, proposal["id"], env=PARTNER)
    rules = learn.read_rules(instance / "LEARNINGS")
    assert [(rule["key"], rule["category"]) for rule in rules] == [("S1", "build"), ("T1", "evidence"), ("S2", "evidence")]


def test_reject_records_why_and_files_nothing(instance):
    proposal = propose(instance, env=ENG)
    with pytest.raises(ValidationError):
        learn.reject(instance, proposal["id"], why="  ", env=PARTNER)
    record = learn.reject(instance, proposal["id"], why="one-off, not a category", env=PARTNER)
    assert record["decision"] == "rejected" and record["category"] == "evidence"
    assert learn.read_rules(instance / "LEARNINGS") == []
    assert (instance / "LEARNINGS" / "rejected" / f"{proposal['id']}.json").is_file()
    with pytest.raises(NotFound):
        learn.accept(instance, proposal["id"], env=PARTNER)


def test_concurrent_accepts_never_share_a_number_even_across_files(instance):
    categories = list(learn.CATEGORY_NAMES)
    proposals = [propose(instance, signal=f"s{i}", check=f"c{i}", category=categories[i % len(categories)], env=ENG)
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
    keys = [rule["key"] for rule in learn.read_rules(instance / "LEARNINGS")]
    assert sorted(keys, key=lambda k: int(k[1:])) == [f"L{i}" for i in range(1, 13)]
    assert len(set(keys)) == 12


# --- check --------------------------------------------------------------------------------------


def test_check_passes_a_clean_directory_and_counts_pending(instance):
    rows = learn.check(instance)
    assert all(status == "ok" for status, _ in rows), rows
    assert rows[0][1].endswith("0 rule(s) in 11 category file(s)")
    propose(instance, env=ENG)
    assert ("warn", "1 pending proposal(s) (hx learn pending)") in learn.check(instance)


def test_check_fails_duplicate_numbers_across_files_and_missing_fields(instance):
    directory = instance / "LEARNINGS"
    (directory / "build.md").write_text((directory / "build.md").read_text() + "\n".join([
        "**Rule L1** · Signal: a · Check: b · Evidence: c",
        "**Rule L2** · Signal: only a signal",
        "**Rule nonsense",
    ]) + "\n")
    (directory / "testing.md").write_text((directory / "testing.md").read_text()
                                           + "**Rule L1** · Signal: d · Check: e · Evidence: f\n")
    fails = [detail for status, detail in learn.check(instance) if status == "fail"]
    assert any("testing.md:" in detail and "L1 is also build.md:" in detail for detail in fails), fails
    assert any("L2 has no Check, Evidence" in detail for detail in fails)
    assert any("starts like a rule but is not" in detail for detail in fails)


def test_check_refuses_rules_outside_the_taxonomy_and_warns_on_missing_files(instance):
    directory = instance / "LEARNINGS"
    (directory / "misc.md").write_text("**Rule L1** · Signal: a · Check: b · Evidence: c\n")
    (directory / "runtime.md").unlink()
    rows = learn.check(instance)
    assert any(status == "fail" and "misc.md holds 1 rule(s) but is not a category" in detail for status, detail in rows)
    assert any(status == "warn" and detail.startswith("runtime.md missing") for status, detail in rows)
    assert learn.read_rules(directory) == [], "a stray file is never read as rules"


def test_check_warns_on_twins_wherever_they_are_filed(instance):
    directory = instance / "LEARNINGS"
    (directory / "evidence.md").write_text((directory / "evidence.md").read_text()
        + "**Rule L1** · Signal: a count quoted without its scope · Check: every count carries suite, selection and omissions · Evidence: c\n")
    (directory / "delivery.md").write_text((directory / "delivery.md").read_text()
        + "**Rule L2** · Signal: a count quoted without scope · Check: counts carry suite, selection, omissions · Evidence: f\n")
    assert any("L1 (evidence) and L2 (delivery) look like one rule" in detail
               for status, detail in learn.check(instance) if status == "warn")


# --- the persona region -------------------------------------------------------------------------


def test_the_persona_region_carries_every_rule_whole(instance):
    empty = learn.persona_region(instance)
    assert str(instance / "LEARNINGS") in empty and "0 rule(s)" in empty
    assert "never read the files" in empty and "hx learn propose --category" in empty
    assert "###" not in empty, "no empty category blocks"
    for category, signal in (("evidence", "a count quoted without its scope"), ("delivery", "a draft PR"),
                             ("evidence", "a reconstructed summary cited as raw")):
        learn.accept(instance, propose(instance, signal=signal, category=category, env=ENG)["id"], env=PARTNER)
    region = learn.persona_region(instance)
    rules = learn.read_rules(instance / "LEARNINGS")
    assert "3 rule(s), last L3" in region
    for rule in rules:
        assert rule["line"] in region, rule["key"]
    assert region.index("### evidence — ") < region.index("### delivery — ")
    assert region.count("### ") == 2
    assert region.index("**Rule L1**") < region.index("**Rule L3**") < region.index("**Rule L2**"), \
        "taxonomy order, then file order"
    summary = learn.summary(instance)
    assert summary["categories"] == {"evidence": 2, "delivery": 1} and summary["last"] == "L3"


# --- the CLI ------------------------------------------------------------------------------------


def test_cli_round_trip(instance):
    result = run_learn(instance, "propose", "--category", "evidence", "--signal", "a count without scope",
                       "--check", "counts carry their scope", "--evidence", "FLAWS_3")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("HX-LEARN-PROPOSED ") and "category=evidence" in result.stdout
    proposal_id = result.stdout.split()[1]

    result = run_learn(instance, "accept", proposal_id)
    assert result.returncode != 0 and "different session" in result.stderr

    result = run_learn(instance, "pending", harness_id="partner")
    assert result.returncode == 0 and proposal_id in result.stdout and "HX-LEARN-PENDING 1" in result.stdout
    assert "category evidence" in result.stdout

    result = run_learn(instance, "accept", proposal_id, "--why", "category", harness_id="partner")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"HX-LEARN new {proposal_id} rule=L1 category=evidence"

    result = run_learn(instance, "list")
    assert "L1     evidence     a count without scope" in result.stdout and "HX-LEARN-LIST 1 rule(s)" in result.stdout
    assert run_learn(instance, "list", "L1").stdout.startswith("**Rule L1** · Signal: a count without scope")
    assert "HX-LEARN-LIST 0 rule(s)" in run_learn(instance, "list", "--category", "build").stdout

    result = run_learn(instance, "check")
    assert result.returncode == 0 and "HX-LEARN-CHECK rules=1 pending=0 fails=0" in result.stdout

    assert run_learn(instance, "path").stdout.strip() == str(instance / "LEARNINGS")

    result = run_learn(instance, "categories")
    assert result.returncode == 0
    assert [line.split()[0] for line in result.stdout.splitlines()] == list(learn.CATEGORY_NAMES)

    twin = run_learn(instance, "propose", "--category", "delivery", "--signal", "a count without its scope",
                     "--check", "counts carry their scope", "--evidence", "again")
    assert "reads like L1" in twin.stderr

    missing = run_learn(instance, "propose", "--signal", "x", "--check", "y", "--evidence", "z")
    assert missing.returncode != 0 and "--category" in missing.stderr


def test_cli_propose_from_a_file(instance, tmp_path):
    body = tmp_path / "learning.md"
    body.write_text("Category: environment\nSignal: the pane was dead\nCheck: HX_TMUX names the server\nEvidence: ui on 8801\n")
    result = run_learn(instance, "propose", "--file", str(body), "--json")
    assert result.returncode == 0, result.stderr
    proposal = json.loads(result.stdout)
    assert proposal["check"] == "HX_TMUX names the server" and proposal["category"] == "environment"
