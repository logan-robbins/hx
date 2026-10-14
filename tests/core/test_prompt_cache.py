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
    """eng-001 launched: a role persona, the one CLAUDE.md, two installed skills, a seam."""
    run = instance / "run" / "eng-001"
    run.mkdir(parents=True, exist_ok=True)
    (instance / "personas" / "engineer.md").write_text("P" * 3000)
    (instance / "config" / "CLAUDE.md").write_text("C" * 1000)
    home = run / "home"
    _skill(home, "hx-worker", "Operating manual.")
    _skill(home, "hx-memory", "Search episodes.")
    harness = instance / "config" / "eng-001" / "harness.json"
    harness.write_text(json.dumps({**json.loads(harness.read_text()), "cache_ttl": "1h"}))
    return instance


def _valid(root, **fields):
    """harness.json as `config_harness.validate_harness` accepts it, which `hx persona` needs:
    the fixture's top-level `cache_ttl`, which only the report reads, is not a harness field."""
    harness = root / "config" / "eng-001" / "harness.json"
    body = {key: value for key, value in json.loads(harness.read_text()).items() if key != "cache_ttl"}
    harness.write_text(json.dumps({**body, **fields}))


def test_report_sums_the_invariant_prefix_and_says_what_is_unmeasured(cached):
    from hx import learn, persona

    _valid(cached)

    proposal = learn.propose(cached, signal="s", check="c", evidence="e", category="build",
                             env={"HARNESS_ID": "eng-001"})
    learn.accept(cached, proposal["id"], env={"HARNESS_ID": "partner"})
    (row,) = prompt_cache.report(cached, "eng-001", now="2026-10-12T12:00:00Z")["agents"]
    invariant = row["invariant"]
    handed = persona.boundary(cached, "eng-001")
    sizes = {name: size for name, _source, size in handed["parts"]}
    assert invariant["persona_bytes"] == 3000 and invariant["persona_source"] == "personas/engineer.md"
    assert (invariant["persona_channel"], invariant["claude_md_channel"]) == ("hook", "home")
    assert invariant["claude_md_bytes"] == 1000
    assert invariant["identity_bytes"] == sizes["identity"] > 0
    assert invariant["learnings_bytes"] == sizes["rules"] > 0 and invariant["learnings_as_files"] == []
    assert (invariant["hook_bytes"], invariant["hook_bound"]) == (handed["bytes"], 10_000)
    assert invariant["skills"] == ["hx-memory", "hx-worker"] and invariant["skills_source"] == "installed"
    assert invariant["skill_descriptions_bytes"] == len("Operating manual.") + len("Search episodes.")
    assert invariant["prefix_bytes"] == (3000 + 1000 + sizes["identity"] + sizes["rules"]
                                         + invariant["skill_descriptions_bytes"])
    assert invariant["native_tool_schemas"] == "unmeasured"
    assert invariant["host_context"] == "unmeasured"
    budget = row["budget"]
    assert budget["model"] == "claude-opus-5"
    assert budget["invariant_ceiling_tokens"] == 200000
    assert budget["within"] is True
    assert budget["share"] == round(invariant["prefix_bytes"] / 200000, 4)
    assert row["seam"] is None  # no seam yet: nothing to age


@pytest.mark.parametrize("flavor,claude_md,role_persona", [
    ("claude", "home", "hook"), ("codex", "agents", "agents"), ("pi", "hook", "flag"),
    ("meta", "hook", "hook"),
])
def test_report_counts_each_source_once_whatever_its_channel(cached, flavor, claude_md, role_persona):
    _valid(cached, flavor=flavor)
    (row,) = prompt_cache.report(cached, "eng-001")["agents"]
    invariant = row["invariant"]
    assert (invariant["claude_md_channel"], invariant["persona_channel"]) == (claude_md, role_persona)
    assert invariant["prefix_bytes"] == (invariant["persona_bytes"] + invariant["claude_md_bytes"]
                                         + invariant["identity_bytes"] + invariant["learnings_bytes"]
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


def test_report_reads_the_sources_and_the_manifest_before_launch(instance):
    (row,) = prompt_cache.report(instance, "eng-001")["agents"]
    invariant = row["invariant"]
    # The conftest instance ships no `personas/engineer.md`: no persona, and none invented.
    assert invariant["persona_bytes"] is None and invariant["persona_source"] is None
    assert invariant["identity_bytes"] > 0 and invariant["hook_bytes"] > 0
    assert not (instance / "run" / "eng-001" / "persona.md").exists(), "the report never writes"
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
