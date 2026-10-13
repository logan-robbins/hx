"""Prompt caching: know the limit, keep invariants cached, restructure when cold."""
from __future__ import annotations

import json

import pytest

from hx import prompt_cache


def test_ttl_parsing():
    assert prompt_cache.ttl_seconds("1h") == 3600
    assert prompt_cache.ttl_seconds("5m") == 300
    assert prompt_cache.ttl_seconds(None) == 300
    assert prompt_cache.ttl_seconds(600) == 600


def test_warm_cache_keeps_prefix():
    result = prompt_cache.assess(prefix_bytes=50_000, age_s=60, ttl="5m")
    assert result["state"] == "warm"
    assert result["recommendation"] == "keep-prefix"


def test_cold_cache_restructures_freely():
    result = prompt_cache.assess(prefix_bytes=50_000, age_s=4000, ttl="1h")
    assert result["state"] == "cold"
    assert result["recommendation"] == "restructure-free"


def test_tiny_prefix_has_no_cache_benefit():
    result = prompt_cache.assess(prefix_bytes=500, age_s=10, ttl="5m")
    assert result["cacheable"] is False
    assert result["recommendation"] == "restructure-free"


def test_expiring_cache_defers_small_changes():
    result = prompt_cache.assess(prefix_bytes=50_000, age_s=1700, ttl="30m")
    assert result["state"] == "expiring"
    assert result["recommendation"] == "defer-to-expiry"


def test_invariant_budget_leaves_working_room():
    budget = prompt_cache.invariant_budget(window=1_000_000, threshold=200_000)
    assert budget["invariant_ceiling_tokens"] <= 200_000
    assert budget["working_room_tokens"] >= 800_000


# --- hx cache report (TOOL_SKILL_PLAN step 8) ----------------------------------------------------


def _skill(home, name, description):
    (home / "skills" / name).mkdir(parents=True, exist_ok=True)
    (home / "skills" / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nThe body is not invariant.\n" + "x" * 4000)


def _seam(root, item_id, ts):
    from hx import streams

    path = streams.main_stream(root, item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"seq": 1, "ts": ts, "event": "seam", "source": "clear",
                                 "context_file_bytes": 10}) + "\n")


@pytest.fixture
def cached(instance):
    """eng-001 launched: a derived persona, the one CLAUDE.md, two installed skills, a seam."""
    run = instance / "run" / "eng-001"
    run.mkdir(parents=True, exist_ok=True)
    (run / "persona.md").write_text("P" * 3000)
    (instance / "config" / "CLAUDE.md").write_text("C" * 1000)
    home = run / "home"
    _skill(home, "hx-worker", "Operating manual.")
    _skill(home, "hx-memory", "Search episodes.")
    harness = instance / "config" / "eng-001" / "harness.json"
    harness.write_text(json.dumps({**json.loads(harness.read_text()), "cache_ttl": "1h"}))
    return instance


def test_report_sums_the_invariant_prefix_and_says_what_is_unmeasured(cached):
    from hx.learn import persona_region

    (row,) = prompt_cache.report(cached, "eng-001", now="2026-10-12T12:00:00Z")["agents"]
    invariant = row["invariant"]
    learnings = len(persona_region(cached).encode())
    assert invariant["persona_bytes"] == 3000 and invariant["persona_source"] == "run/eng-001/persona.md"
    assert invariant["claude_md_bytes"] == 1000
    assert invariant["learnings_bytes"] == learnings and invariant["learnings_in_persona"] is False
    assert invariant["skills"] == ["hx-memory", "hx-worker"] and invariant["skills_source"] == "installed"
    assert invariant["skill_descriptions_bytes"] == len("Operating manual.") + len("Search episodes.")
    assert invariant["prefix_bytes"] == 3000 + 1000 + learnings + invariant["skill_descriptions_bytes"]
    assert invariant["native_tool_schemas"] == "unmeasured"
    assert invariant["host_context"] == "unmeasured"
    budget = row["budget"]
    assert budget["model"] == "claude-opus-5"
    assert budget["invariant_ceiling_tokens"] == 200000
    assert budget["within"] is True
    assert budget["share"] == round(invariant["prefix_bytes"] / 200000, 4)
    assert row["seam"] is None  # no seam yet: nothing to age


def test_report_does_not_count_learnings_twice_when_the_persona_carries_them(cached):
    from hx.compile import LEARNINGS_BEGIN

    (cached / "run" / "eng-001" / "persona.md").write_text(f"P\n{LEARNINGS_BEGIN}\nrules\n")
    (row,) = prompt_cache.report(cached, "eng-001")["agents"]
    invariant = row["invariant"]
    assert invariant["learnings_in_persona"] is True
    assert invariant["prefix_bytes"] == (invariant["persona_bytes"] + invariant["claude_md_bytes"]
                                         + invariant["skill_descriptions_bytes"])


def test_report_ages_the_last_seam_against_the_harness_cache_ttl(cached):
    _seam(cached, "eng-001", "2026-10-12T11:50:00Z")
    (row,) = prompt_cache.report(cached, "eng-001", now="2026-10-12T12:00:00Z")["agents"]
    seam = row["seam"]
    assert seam["last_seam_ts"] == "2026-10-12T11:50:00Z"
    assert seam["age_s"] == 600
    assert (seam["cache_ttl"], seam["ttl_source"]) == ("1h", "harness.json cache_ttl")
    assert seam["ttl_s"] == 3600 and seam["state"] == "warm"
    assert seam["prefix_tokens"] == row["invariant"]["prefix_bytes"]


def test_report_falls_back_to_agents_md_and_the_manifest_before_launch(instance):
    (row,) = prompt_cache.report(instance, "eng-001")["agents"]
    invariant = row["invariant"]
    assert invariant["persona_source"] == "config/eng-001/AGENTS.md above the header"
    assert invariant["persona_bytes"] == len("You are eng-001, an engineer.\nYou commit as you go.\n\n".encode())
    assert invariant["skills_source"] == "manifest"
    assert "hx-worker" in invariant["skills"]
    assert invariant["claude_md_bytes"] is None or isinstance(invariant["claude_md_bytes"], int)


def test_report_covers_every_configured_agent_and_the_cli_forms(cached, capsys):
    doc = prompt_cache.report(cached)
    assert [row["id"] for row in doc["agents"]] == ["eng-001", "partner"]
    assert prompt_cache.main(["report", "--json"], cached) == 0
    assert json.loads(capsys.readouterr().out)["agents"][0]["id"] == "eng-001"
    assert prompt_cache.main(["report", "eng-001"], cached) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[-1].startswith("HX-CACHE-REPORT agents=1 ")
    assert "unmeasured" in text


def test_report_of_an_unknown_id_is_not_found(cached):
    from hx.errors import NotFound

    with pytest.raises(NotFound):
        prompt_cache.report(cached, "eng-404")


def test_cache_report_help_carries_an_example(capsys):
    with pytest.raises(SystemExit):
        prompt_cache.main(["report", "--help"], None)
    assert "example" in capsys.readouterr().out
