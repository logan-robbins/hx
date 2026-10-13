"""`hx compile` — the template compiler (spec 11 Identity).

Base rules live in two Partner-managed sources (`config/CLAUDE.md` and the
role persona); `compile` distributes them into each worker's final
`config/<id>/AGENTS.md` as fenced regions, preserving the per-id text and the
agent's own memory below `## UPDATES BELOW ONLY`. It runs on every `hx launch`
and `hx restart`.
"""

from __future__ import annotations

import pytest

from hx.compile import GLOBAL_BEGIN, LEARNINGS_BEGIN, LEARNINGS_END, compile_agent
from hx.errors import HxError


def agents_md(instance, item_id="eng-001"):
    return (instance / "config" / item_id / "AGENTS.md").read_text()


def _set_flavor(instance, flavor, item_id="eng-001"):
    import json

    path = instance / "config" / item_id / "harness.json"
    body = json.loads(path.read_text())
    body["flavor"] = flavor
    path.write_text(json.dumps(body))


def test_compile_distributes_the_role_region_and_no_invariants_on_claude(instance):
    """The invariants reach a Claude agent once, as its home's native CLAUDE.md
    (`config_harness.INVARIANTS_CHANNEL`); the persona does not carry a second copy."""
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
    assert GLOBAL_BEGIN not in text
    assert "hx:role begin" in text
    assert "You are an engineer." in text
    # The role's below-header starter is not distributed: memory is per-agent.
    assert "Role memory starter." not in text
    # The per-id text and the agent's own memory survive the compile.
    assert "You are eng-001, an engineer." in text
    assert "Things I learned: nothing yet." in text


def test_compile_without_a_role_persona_distributes_no_role_region(instance):
    report = compile_agent(instance, "eng-001")
    assert report["compiled"] is True and report["role_region"] is False
    text = agents_md(instance)
    assert GLOBAL_BEGIN not in text
    assert "hx:role begin" not in text
    assert "Things I learned: nothing yet." in text


@pytest.mark.parametrize("flavor", ["pi", "grok"])
def test_the_invariants_ride_the_persona_only_where_no_other_channel_exists(instance, flavor):
    """Pi and Grok take a system prompt but read no CLAUDE.md: the persona is their channel.
    Moving the agent back to Claude removes the region instead of keeping it stale."""
    _set_flavor(instance, flavor)
    compile_agent(instance, "eng-001")
    text = agents_md(instance)
    assert text.count(GLOBAL_BEGIN) == 1
    assert (instance / "config" / "CLAUDE.md").read_text().strip() in text
    _set_flavor(instance, "claude")
    assert compile_agent(instance, "eng-001")["compiled"] is True
    assert GLOBAL_BEGIN not in agents_md(instance)
    assert "Things I learned: nothing yet." in agents_md(instance)


@pytest.mark.parametrize("flavor", ["meta", "codex"])
def test_no_invariants_region_where_the_context_file_carries_them(instance, flavor):
    _set_flavor(instance, flavor)
    compile_agent(instance, "eng-001")
    assert GLOBAL_BEGIN not in agents_md(instance)


def test_compile_is_idempotent(instance):
    compile_agent(instance, "eng-001")
    report = compile_agent(instance, "eng-001")
    assert report == {
        "id": "eng-001", "compiled": False, "reason": "up-to-date",
        "role": "engineer", "role_region": False,
    }


def test_recompile_picks_up_base_edits_and_keeps_per_id_text(instance):
    _set_flavor(instance, "pi")  # the flavor whose persona carries the invariants
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
    _set_flavor(instance, "grok")
    compile_agent(instance, "eng-001")
    compile_agent(instance, "eng-001")
    assert agents_md(instance).count(GLOBAL_BEGIN) == 1


def test_the_partner_keeps_its_persona_and_gains_only_the_learnings_region(instance):
    """The shipped Partner persona is not compiled from CLAUDE.md or a role file, but the
    Partner carries the learnings like everyone else: one region, last above the header."""
    before = agents_md(instance, "partner")
    report = compile_agent(instance, "partner")
    assert report["compiled"] is True and report["reason"] == "partner-learnings-only"
    text = agents_md(instance, "partner")
    assert GLOBAL_BEGIN not in text and "<!-- hx:role begin" not in text
    assert text.count(LEARNINGS_BEGIN) == 1
    upper, _, lower = text.partition("## UPDATES BELOW ONLY")
    assert upper.rstrip().endswith(LEARNINGS_END), "the region is the last thing above the header"
    from hx.compile import REGION_RE
    assert REGION_RE.sub("", upper).strip() == before.partition("## UPDATES BELOW ONLY")[0].strip()
    assert lower == before.partition("## UPDATES BELOW ONLY")[2]
    assert compile_agent(instance, "partner") == {
        "id": "partner", "compiled": False, "reason": "up-to-date", "role": "partner", "role_region": False,
    }
    assert agents_md(instance, "partner").count(LEARNINGS_BEGIN) == 1


def test_compile_refuses_an_agents_md_with_no_header(instance):
    (instance / "config" / "eng-001" / "AGENTS.md").write_text("no header here\n")
    with pytest.raises(HxError):
        compile_agent(instance, "eng-001")


def test_launch_delivers_an_invariants_edit_through_the_home_claude_md(instance, hx, launched):
    """On Claude the invariants' one channel is the home's native CLAUDE.md, refreshed at
    launch; the compiled persona does not carry a second copy."""
    clauses = instance / "config" / "CLAUDE.md"
    clauses.write_text(clauses.read_text() + "\nLaunched invariant.\n")
    launched("eng-001")
    assert "Launched invariant." in (instance / "run" / "eng-001" / "home" / "CLAUDE.md").read_text()
    assert "Launched invariant." not in agents_md(instance)
    assert "Launched invariant." not in (instance / "run" / "eng-001" / "persona.md").read_text()


def test_compile_distributes_the_learnings_region_to_every_agent(instance):
    """docs/learnings.md: the agent is told where the central file is and how to propose."""
    compile_agent(instance, "eng-001")
    text = agents_md(instance)
    assert LEARNINGS_BEGIN in text
    assert str(instance / "LEARNINGS") in text
    assert "hx learn propose --category" in text and "never read the files" in text
    assert text.index(LEARNINGS_BEGIN) < text.index("## UPDATES BELOW ONLY")


def test_an_instance_compiled_before_learnings_existed_gains_the_region(instance):
    """A file with global and role regions but no learnings region gets one, after them."""
    compile_agent(instance, "eng-001")
    path = instance / "config" / "eng-001" / "AGENTS.md"
    import re
    from hx.compile import REGION_RE
    without = REGION_RE.sub(lambda m: "" if m.group("kind") == "learnings" else m.group(0),
                            path.read_text())
    without = re.sub(r"\n{3,}", "\n\n", without)
    path.write_text(without)
    assert LEARNINGS_BEGIN not in path.read_text()
    report = compile_agent(instance, "eng-001")
    assert report["compiled"] is True
    text = path.read_text()
    assert text.count(LEARNINGS_BEGIN) == 1
    assert text.index(LEARNINGS_BEGIN) < text.index("## UPDATES BELOW ONLY")
    assert "Things I learned: nothing yet." in text


def test_the_learnings_region_carries_every_rule_whole(instance):
    """docs/learnings.md: the rules are the system prompt, not a pointer to a file."""
    from hx import learn
    compile_agent(instance, "eng-001")
    assert "0 rule(s)" in agents_md(instance)
    proposal = learn.propose(instance, signal="a count quoted without its scope", check="every count carries its scope",
                             evidence="FLAWS_3", category="evidence", env={"HARNESS_ID": "eng-001"})
    learn.accept(instance, proposal["id"], env={"HARNESS_ID": "partner"})
    assert compile_agent(instance, "eng-001")["compiled"] is True
    text = agents_md(instance)
    assert "1 rule(s), last L1" in text
    assert "### evidence — " in text
    assert learn.read_rules(instance / "LEARNINGS")[0]["line"] in text
