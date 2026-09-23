"""`hx compile` — the template compiler (spec 11 Identity).

Base rules live in two Partner-managed sources (`config/CLAUDE.md` and the
role persona); `compile` distributes them into each worker's final
`config/<id>/AGENTS.md` as fenced regions, preserving the per-id text and the
agent's own memory below `## UPDATES BELOW ONLY`. It runs on every `hx launch`
and `hx restart`.
"""

from __future__ import annotations

import pytest

from hx.compile import GLOBAL_BEGIN, compile_agent
from hx.errors import HxError


def agents_md(instance, item_id="eng-001"):
    return (instance / "config" / item_id / "AGENTS.md").read_text()


def test_compile_distributes_global_and_role_regions(instance):
    (instance / "personas").mkdir(exist_ok=True)
    role_dir = instance / "personas" / "engineer"
    role_dir.mkdir(exist_ok=True)
    (role_dir / "AGENTS.md").write_text(
        "You are an engineer.\n\n## UPDATES BELOW ONLY\n\nRole memory starter.\n"
    )
    report = compile_agent(instance, "eng-001")
    assert report == {
        "id": "eng-001", "compiled": True, "role": "engineer", "role_region": True,
    }
    text = agents_md(instance)
    assert GLOBAL_BEGIN in text
    assert "hx:role begin" in text
    assert "You are an engineer." in text
    # The role's below-header starter is not distributed: memory is per-agent.
    assert "Role memory starter." not in text
    # The per-id text and the agent's own memory survive the compile.
    assert "You are eng-001, an engineer." in text
    assert "Things I learned: nothing yet." in text


def test_compile_without_a_role_persona_distributes_global_only(instance):
    report = compile_agent(instance, "eng-001")
    assert report["compiled"] is True and report["role_region"] is False
    text = agents_md(instance)
    assert GLOBAL_BEGIN in text
    assert "hx:role begin" not in text
    assert "Things I learned: nothing yet." in text


def test_compile_is_idempotent(instance):
    compile_agent(instance, "eng-001")
    report = compile_agent(instance, "eng-001")
    assert report == {
        "id": "eng-001", "compiled": False, "reason": "up-to-date",
        "role": "engineer", "role_region": False,
    }


def test_recompile_picks_up_base_edits_and_keeps_per_id_text(instance):
    compile_agent(instance, "eng-001")
    clauses = instance / "config" / "CLAUDE.md"
    clauses.write_text(clauses.read_text() + "\nNew invariant truth.\n")
    path = instance / "config" / "eng-001" / "AGENTS.md"
    path.write_text(
        path.read_text().replace(
            "You are eng-001, an engineer.",
            "You are eng-001, an engineer for the importer.",
        )
    )
    report = compile_agent(instance, "eng-001")
    assert report["compiled"] is True
    text = agents_md(instance)
    assert "New invariant truth." in text
    assert "an engineer for the importer." in text


def test_stale_regions_are_replaced_not_duplicated(instance):
    compile_agent(instance, "eng-001")
    compile_agent(instance, "eng-001")
    assert agents_md(instance).count(GLOBAL_BEGIN) == 1


def test_the_partner_is_exempt(instance):
    before = agents_md(instance, "partner")
    assert compile_agent(instance, "partner") == {
        "id": "partner", "compiled": False, "reason": "partner-exempt",
    }
    assert agents_md(instance, "partner") == before


def test_compile_refuses_an_agents_md_with_no_header(instance):
    (instance / "config" / "eng-001" / "AGENTS.md").write_text("no header here\n")
    with pytest.raises(HxError):
        compile_agent(instance, "eng-001")


def test_launch_compiles_before_start(instance, hx, launched):
    """A base edit lands in the worker's file through the launch path."""
    clauses = instance / "config" / "CLAUDE.md"
    clauses.write_text(clauses.read_text() + "\nLaunched invariant.\n")
    launched("eng-001")
    assert "Launched invariant." in agents_md(instance)
