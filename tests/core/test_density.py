"""The density of section 4 of the context file, and the prompts that fill it.

The reader of `render_step_state`'s output is a Claude session resuming a half-built change
after `/clear`, and it pays for every character of it at every boundary. So the rendering is
telegraphic — one tagged line per fact, no headings, no bullets, no blank lines — and the
Companion prompts that produce the strings say the same thing about the strings themselves.

What is checked here:

* every tag in `STEP_STATE_TAGS` is what the renderer actually emits, and nothing else is;
* the rendering carries no markdown furniture and no blank lines;
* a realistic eleven-key state renders smaller than the same state did as markdown;
* `companion/BASE.md` and every role file carry the style rules and the
  "pre-answer the master's next tool calls" table that make the strings dense in the first
  place.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from hx.compose import STEP_STATE_TAGS, render_step_state
from hx.stepstate import SCHEMA, validate

REPO = pathlib.Path(__file__).resolve().parents[2]
COMPANION = REPO / "src" / "hx" / "skeleton" / "companion"
ROLE_FILES = sorted((COMPANION / "roles").glob("*.md"))


#: An eleven-key state of the shape a Companion actually writes, with every field populated.
FULL_STATE = {
    "seq": 1487,
    "prompt_version": {"base": "9f2c1ab", "role": "4d81e07"},
    "goal": "ETag caching on /v1/assets; p95 < 120ms",
    "constraints": ["no new runtime deps", "response shape of /v1/assets frozen"],
    "decisions": [
        {"d": "weak ETag from id+updated_at", "why": "body hash = full read per request",
         "ev": [1402, 1409]},
    ],
    "open_steps": [
        {"id": "st9", "intent": "304 on conditional GET",
         "next": "src/api/media/routes.py:214 compare if-none-match to etag_for(asset); "
                 ".venv/bin/python -m pytest tests/api/test_media_etag.py -q",
         "ev": [1455, 1478]},
    ],
    "closed_steps": [
        {"id": "st7", "outcome": "etag_for() in src/api/media/etag.py, 6 unit tests",
         "verified": True, "commit": "a71c3f9", "ev": [1410]},
        {"id": "st10", "outcome": "scripts/bench_assets.py added", "verified": False,
         "commit": "", "ev": [1470]},
    ],
    "dead_ends": ["strong ETag over body: +40ms/req in scripts/bench_assets.py"],
    "working_set": {
        "commits": [{"sha": "a71c3f9", "msg": "media: etag_for(asset) from id+updated_at"}],
        "dirty": ["src/api/media/routes.py"],
        "files": [{"path": "src/api/deps.py",
                   "note": "get_cache at :57 -> redis.asyncio.Redis, db from settings.redis_db"}],
        "last_failure": ".venv/bin/python -m pytest tests/api/test_media_etag.py -q -> 1 failed: "
                        "test_304_on_matching_etag assert 200 == 304 "
                        "(tests/api/test_media_etag.py:41)",
        "hypothesis": "routes.py:214 reads If-None-Match cased; starlette lowercases keys",
    },
    "blockers": ["staging Redis credentials absent; bench runs against fakeredis only"],
    "subagents_open": ["s003"],
}


def test_the_sample_state_is_a_state_hx_would_accept():
    """A rendering test proves nothing about a document the validator would have rejected."""
    assert set(FULL_STATE) == set(SCHEMA), "the sample is not the eleven-key object"
    validate(json.loads(json.dumps(FULL_STATE)))


def rendered_lines() -> list[str]:
    return render_step_state(FULL_STATE).splitlines()


def test_every_line_is_tagged_with_a_tag_the_module_documents():
    known = set(STEP_STATE_TAGS)
    for line in rendered_lines():
        tag = line.split(":", 1)[0].split(" ", 1)[0]
        assert tag in known, f"untagged line {line!r}; tags are {sorted(known)}"


def test_every_documented_tag_is_one_the_renderer_emits():
    """The table in compose.py is what companion/BASE.md and hx-worker describe to a model. A
    tag that no longer appears would be teaching both of them a fiction."""
    emitted = {line.split(":", 1)[0].split(" ", 1)[0] for line in rendered_lines()}
    assert emitted == set(STEP_STATE_TAGS), (
        f"documented but not emitted: {sorted(set(STEP_STATE_TAGS) - emitted)}; "
        f"emitted but not documented: {sorted(emitted - set(STEP_STATE_TAGS))}"
    )


def test_the_rendering_has_no_markdown_furniture_and_no_blank_lines():
    text = render_step_state(FULL_STATE)
    assert "**" not in text, "bold headings are structure the reader pays for twice"
    assert "\n\n" not in text, "blank lines between groups"
    for line in text.splitlines():
        assert not line.startswith(("- ", "  ", "#", "_")), f"list furniture: {line!r}"


def test_one_line_per_entry():
    lines = rendered_lines()
    expected = (
        1  # goal
        + len(FULL_STATE["constraints"])
        + len(FULL_STATE["decisions"])
        + 2 * len(FULL_STATE["open_steps"])  # each open step plus its `next`
        + len(FULL_STATE["closed_steps"])
        + len(FULL_STATE["dead_ends"])
        + len(FULL_STATE["working_set"]["commits"])
        + len(FULL_STATE["working_set"]["dirty"])
        + len(FULL_STATE["working_set"]["files"])
        + 2  # last_failure, hypothesis
        + len(FULL_STATE["blockers"])
        + 1  # seq
    )
    assert len(lines) == expected, "\n".join(lines)


def test_the_next_action_is_its_own_line_next_to_its_step():
    lines = rendered_lines()
    index = lines.index("open st9: 304 on conditional GET [1455,1478]")
    assert lines[index + 1].startswith("next st9: src/api/media/routes.py:214")


def test_an_unverified_close_is_shouted_and_a_verified_one_carries_its_sha():
    text = render_step_state(FULL_STATE)
    assert "done st7 verified a71c3f9: etag_for()" in text
    assert "done st10 UNVERIFIED: scripts/bench_assets.py added" in text


def test_identifiers_survive_verbatim():
    """Density is never paid for with an approximate path, command or error line."""
    text = render_step_state(FULL_STATE)
    for exact in (
        "src/api/media/routes.py:214",
        ".venv/bin/python -m pytest tests/api/test_media_etag.py -q",
        "tests/api/test_media_etag.py:41",
        "a71c3f9",
        "get_cache at :57",
    ):
        assert exact in text, exact


def test_a_multi_line_string_is_flattened_to_one_line():
    state = dict(FULL_STATE, dead_ends=["line one\nline two   with   spaces"])
    assert "dead: line one line two with spaces" in render_step_state(state)


def test_unknown_keys_are_carried_on_one_line_rather_than_dropped():
    text = render_step_state(dict(FULL_STATE, invented=["x"]))
    assert 'other: {"invented":["x"]}' in text


def test_an_empty_state_renders_to_nothing_so_the_section_says_none_yet():
    assert render_step_state({}) == ""


MARKDOWN_RENDERING_CHARS = 1264


def test_the_rendering_is_smaller_than_the_markdown_it_replaced():
    """`MARKDOWN_RENDERING_CHARS` is what the previous heading-and-bullet rendering produced
    for FULL_STATE. The saving is structural — the same facts, without the furniture — and it
    is paid for once per boundary per stream, on top of the shorter strings the rewritten
    companion prompts ask for."""
    size = len(render_step_state(FULL_STATE))
    assert size < MARKDOWN_RENDERING_CHARS, (
        f"{size} chars against {MARKDOWN_RENDERING_CHARS} for the old markdown rendering"
    )


# ---------------------------------------------------------------- the prompts behind it


def test_base_md_tells_the_companion_to_pre_answer_the_next_tool_calls():
    text = (COMPANION / "BASE.md").read_text()
    assert "Pre-answer the master's next tool calls" in text
    for fragment in ("working_set.files", "last_failure", "working_set.commits", "dead_ends"):
        assert fragment in text, fragment


def test_base_md_states_the_style_and_a_length_budget_per_field():
    text = (COMPANION / "BASE.md").read_text()
    for fragment in ("telegraph", "No articles", "path:line", "Numbers, not adjectives"):
        assert fragment in text, f"companion/BASE.md no longer says {fragment!r}"
    # A cap per field, in a table, so "short" is a number rather than an adjective.
    for field in ("`goal`", "`constraints[]`", "`open_steps[].next`",
                  "`working_set.last_failure`"):
        assert field in text, f"companion/BASE.md has no length guidance for {field}"


@pytest.mark.parametrize("path", ROLE_FILES, ids=[p.stem for p in ROLE_FILES])
def test_every_role_file_lists_the_tool_calls_its_facts_pre_empt(path):
    text = path.read_text()
    assert "after a seam" in text, f"{path}: no seam-recovery framing"
    assert "| It would run |" in text, (
        f"{path}: no table of the tool calls this role's facts make unnecessary"
    )
    assert "Write instead" in text, path
