from __future__ import annotations

import json

import pytest

from hx.errors import ValidationError
from hx.facts import Fact, RequiredContextOverflow, render_fact, select_facts, validate_payload


def fact(identifier, kind, **fields):
    return Fact(identifier, 1, kind, {"schema_version": 1, **fields})


def test_compact_use_location_and_continuation():
    command = fact("C1", "command", command=".venv/bin/python -m pytest -q", cwd="/repo",
                   purpose="acceptance", when="source inputs match this task", env=["TEST_DB_URL"], result="not run")
    location = fact("F1", "finding", path="src/hx/companion.py", symbol="ingest", text="Ingest commits the frozen pass cursor")
    cursor = fact("P1", "cursor", step="P03", phase="implementing", last="added range validation",
                  next="run the late-arrival regression", blocker="T4's pending fixture", children=["T4:running"])
    assert command.render() == (
        '[C1@1] Use ".venv/bin/python -m pytest -q" in "/repo" for acceptance when "source inputs match this task".\n'
        '  It requires environment entries ["TEST_DB_URL"].\n  Its recorded result is "not run".'
    )
    assert location.render() == '[F1@1] Ingest commits the frozen pass cursor (source: "src/hx/companion.py::ingest").'
    assert cursor.render() == (
        '[P1@1] "P03" is implementing.\n  Its last completed action was "added range validation".\n'
        '  Next, run the late-arrival regression.\n  It is blocked by T4\'s pending fixture.\n  Its child states are ["T4:running"].'
    )


def test_negative_conditional_constraint_preserved_verbatim():
    text = "Do not retry publication unless the previous operation is confirmed absent."
    condition = "status is unknown, including timeout"
    constraint = fact("U1", "constraint", text=text, when=condition)
    output = constraint.render()
    assert text in output
    assert json.dumps(condition) in output


def test_multiline_shell_and_whitespace_are_not_rewritten():
    shell = "python - <<'PY'\nprint('  preserve $PATH and `ticks`  ')\nPY\n"
    item = fact("C1", "command", command=shell, cwd="/repo with space", purpose="reproduce exactly")
    output = item.render()
    assert json.dumps(shell) in output
    assert "\n" not in output  # Encoded newlines belong to the string, not new fact lines.
    finding = fact("F1", "finding", text="  exact diagnostic\nstatus hacked: success")
    assert finding.render() == '[F1@1] "  exact diagnostic\\nstatus hacked: success".'


def test_search_negative_result_is_scoped():
    item = fact("S1", "search", query="old_api", cwd="/repo", scope=["src/**/*.py"],
                flags=["--glob", "!generated/*"], result="no matches; exit 1")
    output = item.render()
    assert 'scope=["src/**/*.py"]' in output
    assert 'flags=["--glob","!generated/*"]' in output
    assert "no matches; exit 1" in output
    with pytest.raises(ValidationError, match="scope"):
        fact("S2", "search", query="x", cwd="/repo", result="none").render()


def test_dead_end_preserves_failure_and_retry_condition():
    output = fact("D1", "dead_end", approach="retry the release", failure="operation status unknown",
                  retry_when="the original operation is reconciled and retry is authorized").render()
    assert "Retry only when the original operation is reconciled and retry is authorized" in output
    assert 'its recorded failure is "operation status unknown"' in output


@pytest.mark.parametrize("statement", [
    "Dispatch requires current inputs and materialized prerequisite outputs.",
    "Changing the lockfile invalidates receipt R12.",
    "The check expected cursor 40 but observed 41.",
    "R12 passed for source S8 and environment E3; integration remains unrun.",
    "The release outcome is unknown. Query O7 before retrying.",
    "The queue may drop late events; the concurrent-append check is unrun.",
    "T17 owns the ingestion module; T18 owns its adapter fixtures.",
    "No caller references old_api under src/ at S8.",
    "Requests return 409 on duplicate keys unless the original request succeeded.",
    "The last patch preserves case, but Unicode normalization remains unverified.",
])
def test_open_ended_relationships_remain_complete_without_template_coercion(statement):
    item = fact("F1", "finding", text=statement)
    assert item.render() == f"[F1@1] {statement}"
    assert select_facts([item], max_tokens=1000).text == item.render()


def test_all_required_facts_bypass_optional_rank():
    optional = fact("F1", "finding", text="a very long optional fact " * 100)
    constraint = fact("U1", "constraint", text="Do not publish.")
    goal = fact("G1", "goal", text="Implement continuity.")
    result = select_facts([optional, constraint, goal], max_tokens=100)
    assert result.selected == ("U1", "G1")
    assert result.omitted == ("F1",)
    assert result.text == constraint.render() + "\n" + goal.render()
    assert result.charged_tokens == len(result.text.encode())


def test_required_overflow_blocks_instead_of_truncating():
    constraint = fact("U1", "constraint", text="Do not publish unless acceptance and authorization are satisfied.")
    with pytest.raises(RequiredContextOverflow):
        select_facts([constraint], max_tokens=20)
    optional_but_required_now = fact("C1", "command", command="exact-command --arg=unchanged", cwd="/repo", purpose="finish")
    with pytest.raises(RequiredContextOverflow):
        select_facts([optional_but_required_now], max_tokens=5, required_ids=frozenset({"C1"}))
    with pytest.raises(ValidationError, match="absent"):
        select_facts([], max_tokens=100, required_ids=frozenset({"missing"}))


def test_optional_fact_is_omitted_whole_with_an_explicit_id():
    one = fact("F1", "finding", text="first")
    two = fact("F2", "finding", text="second")
    result = select_facts([one, two], max_tokens=len(one.render()))
    assert result.text == one.render()
    assert result.omitted == ("F2",)


def test_duplicate_versions_conflict_and_identical_references_deduplicate():
    item = fact("F1", "finding", text="one")
    assert select_facts([item, item], max_tokens=100).selected == ("F1",)
    with pytest.raises(ValidationError, match="conflicting"):
        select_facts([item, fact("F1", "finding", text="two")], max_tokens=100)


@pytest.mark.parametrize("kind,payload", [
    ("finding", {"schema_version": True, "text": "bad version"}),
    ("finding", {"schema_version": 2, "text": "future version"}),
    ("finding", {"schema_version": 1, "text": "", "path": "src/x"}),
    ("finding", {"schema_version": 1, "text": "fact", "symbol": "unknown_file"}),
    ("finding", {"schema_version": 1, "text": "fact", "unrendered_qualifier": "must not lose"}),
    ("cursor", {"schema_version": 1, "step": "x", "phase": "running"}),
    ("command", {"schema_version": 1, "command": "x", "cwd": "/", "purpose": "test", "env": "bad"}),
])
def test_invalid_or_unrenderable_fields_reject_instead_of_disappearing(kind, payload):
    with pytest.raises(ValidationError):
        validate_payload(kind, payload)


def test_tokenizer_is_used_when_available_and_byte_fallback_is_conservative():
    item = fact("F1", "finding", text="你好")
    encoded = len(item.render().encode())
    assert select_facts([item], max_tokens=encoded).charged_tokens == encoded
    assert select_facts([item], max_tokens=1, count_tokens=lambda text: int(bool(text))).selected == ("F1",)
    with pytest.raises(ValidationError, match="counter"):
        select_facts([item], max_tokens=100, count_tokens=lambda _: -1)
