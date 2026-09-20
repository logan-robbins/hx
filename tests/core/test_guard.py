"""The `guard` hook — milestone M3 (spec 09.2, 13 M3 table, 04 ownership).

One test per row of the spec 13 M3 table, plus the path-resolution rules the table depends on.
All of it under bypass permissions, where these rules are the only enforcement.

Payload shape verified against https://code.claude.com/docs/en/hooks on 2026-09-20:
`PreToolUse` carries `session_id`, `transcript_path`, `cwd`, `hook_event_name`,
`permission_mode`, `tool_name`, `tool_input`, `tool_use_id`, plus `agent_id`/`agent_type`
inside a subagent. A deny is exit 2 with the reason on stderr.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from .conftest import clean_env

BYPASS = "bypassPermissions"


def payload(tool_name, tool_input, cwd, *, agent_id=None):
    """A PreToolUse payload in the documented shape."""
    body = {
        "session_id": "abc123",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": str(cwd),
        "permission_mode": BYPASS,
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "toolu_01ABC",
    }
    if agent_id:
        body["agent_id"] = agent_id
        body["agent_type"] = "general-purpose"
    return body


def guard(instance, item_id, tool_name, tool_input, *, cwd=None, agent_id=None):
    """Run the guard as `item_id` would, with HARNESS_ID set the way start.sh sets it."""
    cwd = cwd if cwd is not None else (instance if item_id == "partner" else instance / "wt" / item_id)
    return subprocess.run(
        [sys.executable, "-m", "hx.hooks", "--id", item_id, "guard"],
        input=json.dumps(payload(tool_name, tool_input, cwd, agent_id=agent_id)),
        env=clean_env(HARNESS_ROOT=str(instance), HARNESS_ID=item_id),
        capture_output=True,
        text=True,
    )


def assert_denied(result, *, because=None):
    assert result.returncode == 2, f"expected deny, got {result.returncode}: {result.stdout}{result.stderr}"
    assert result.stdout == "", "a deny writes nothing to stdout (spec 09.1)"
    assert result.stderr.strip(), "a deny states the reason on stderr"
    assert result.returncode != 1, "never exit 1 (spec 09.1)"
    if because:
        assert because in result.stderr, result.stderr


def assert_allowed(result):
    assert result.returncode == 0, f"expected allow, got {result.returncode}: {result.stdout}{result.stderr}"
    assert result.stdout == "" and result.stderr == "", "an allow says nothing"


@pytest.fixture
def ready(instance, agent):
    agent("eng-002")
    (instance / "pods" / "partner").mkdir(parents=True, exist_ok=True)
    (instance / "pods" / "engineers").mkdir(parents=True, exist_ok=True)
    for item_id, pod in (("partner", "partner"), ("eng-001", "engineers"), ("eng-002", "engineers")):
        (instance / "pods" / pod / f"{item_id}-working.md").write_text(
            f"---\nid: {item_id}\npod: {pod}\nafter: []\noutcome:\ndispatched:\n---\n\n## Order\n\nx\n"
        )
    (instance / "PARTNER.md").write_text("# Partner state\n")
    (instance / "orders").mkdir(exist_ok=True)
    (instance / "tasks.json").write_text("{}")
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "eng-001-main.jsonl").write_text("")
    (instance / "config" / "eng-001" / "SUBAGENTS.md").write_text("subagent identity\n")
    return instance


# --- the spec 13 M3 table, row by row ---------------------------------------------------------


def test_row_01_worker_reads_another_agents_persona(ready):
    result = guard(ready, "eng-001", "Read", {"file_path": str(ready / "config/eng-002/AGENTS.md")})
    assert_denied(result, because="identity is delivered by hook")


def test_row_02_worker_reads_its_own_subagents_md(ready):
    result = guard(ready, "eng-001", "Read", {"file_path": str(ready / "config/eng-001/SUBAGENTS.md")})
    assert_denied(result, because="identity is delivered by hook")


def test_row_03_worker_edits_its_own_agents_md_below_the_header(ready):
    path = ready / "config" / "eng-001" / "AGENTS.md"
    result = guard(ready, "eng-001", "Edit", {
        "file_path": str(path),
        "old_string": "Things I learned: nothing yet.",
        "new_string": "Things I learned: the importer buffers.",
    })
    assert_allowed(result)


def test_row_04_worker_edits_its_own_agents_md_above_the_header(ready):
    path = ready / "config" / "eng-001" / "AGENTS.md"
    result = guard(ready, "eng-001", "Edit", {
        "file_path": str(path),
        "old_string": "You are eng-001, an engineer.",
        "new_string": "You are eng-001, a reviewer.",
    })
    assert_denied(result, because="only the section below the header is yours")


def test_row_05_worker_edits_another_agents_md_below_the_header(ready):
    result = guard(ready, "eng-001", "Edit", {
        "file_path": str(ready / "config/eng-002/AGENTS.md"),
        "old_string": "nothing yet.",
        "new_string": "something.",
    })
    assert_denied(result, because="identity is delivered by hook")


def test_row_06_worker_cats_every_persona_with_a_glob(ready):
    result = guard(ready, "eng-001", "Bash", {"command": "cat ../../config/*/AGENTS.md"})
    assert_denied(result, because="identity is delivered by hook")


def test_row_07_worker_reads_through_a_symlink_into_config(ready):
    link = ready / "wt" / "eng-001" / "peek"
    link.symlink_to(ready / "config")
    result = guard(ready, "eng-001", "Read", {"file_path": "peek/eng-002/AGENTS.md"})
    assert_denied(result, because="identity is delivered by hook")


def test_row_08_worker_reads_a_companion_prompt(ready):
    result = guard(ready, "eng-001", "Read", {"file_path": str(ready / "companion/BASE.md")})
    assert_denied(result, because="identity is delivered by hook")


def test_row_09_worker_runs_hx_hook_as_another_id(ready):
    result = guard(ready, "eng-001", "Bash", {"command": "hx-hook --id eng-002 context"})
    assert_denied(result, because="identity is delivered by hook")


def test_row_10_partner_reads_a_workers_persona(ready):
    result = guard(ready, "partner", "Read", {"file_path": str(ready / "config/eng-001/AGENTS.md")})
    assert_denied(result, because="identity is delivered by hook")


def test_row_11_partner_edits_a_workers_persona_above_the_header(ready):
    """spec 04: the Partner writes the persona, rarely and on direct human instruction."""
    result = guard(ready, "partner", "Edit", {
        "file_path": str(ready / "config/eng-001/AGENTS.md"),
        "old_string": "You are eng-001, an engineer.",
        "new_string": "You are eng-001, who owns the importer.",
    })
    assert_allowed(result)


def test_row_12_worker_edits_its_own_working_item(ready):
    result = guard(ready, "eng-001", "Edit", {
        "file_path": str(ready / "pods/engineers/eng-001-working.md"),
        "old_string": "## Order",
        "new_string": "## Order",
    })
    assert_allowed(result)


def test_row_13_worker_edits_another_workers_item(ready):
    result = guard(ready, "eng-001", "Edit", {
        "file_path": str(ready / "pods/engineers/eng-002-working.md"),
        "old_string": "x",
        "new_string": "y",
    })
    assert_denied(result, because="managed by hx")


def test_row_14_partner_edits_its_own_working_item(ready):
    result = guard(ready, "partner", "Edit", {
        "file_path": str(ready / "pods/partner/partner-working.md"),
        "old_string": "x",
        "new_string": "y",
    })
    assert_allowed(result)


def test_row_15_partner_writes_an_order(ready):
    result = guard(ready, "partner", "Write", {
        "file_path": str(ready / "orders/eng-001.md"), "content": "## Order\n"
    })
    assert_allowed(result)


def test_row_16_worker_writes_an_order(ready):
    result = guard(ready, "eng-001", "Write", {
        "file_path": str(ready / "orders/eng-001.md"), "content": "## Order\n"
    })
    assert_denied(result, because="orders are the Partner's")


def test_row_17_worker_edits_its_own_stream(ready):
    result = guard(ready, "eng-001", "Edit", {
        "file_path": str(ready / "logs/eng-001/eng-001-main.jsonl"),
        "old_string": "", "new_string": "x",
    })
    assert_denied(result, because="managed by hx")


def test_row_18_worker_edits_tasks_json(ready):
    result = guard(ready, "eng-001", "Edit", {
        "file_path": str(ready / "tasks.json"), "old_string": "{}", "new_string": "{}",
    })
    assert_denied(result, because="managed by hx")


def test_row_19_worker_runs_hx_complete(ready):
    assert_allowed(guard(ready, "eng-001", "Bash", {"command": "hx complete done"}))


def test_row_20_worker_runs_hx_task(ready):
    assert_allowed(guard(ready, "eng-001", "Bash", {"command": "hx task"}))


def test_row_21_worker_runs_hx_dispatch(ready, hx):
    """Allowed by the guard, refused by hx itself: HARNESS_ID is not `partner` (spec 08)."""
    assert_allowed(guard(ready, "eng-001", "Bash", {"command": "hx dispatch eng-002 orders/eng-002.md"}))
    refused = hx("dispatch", "eng-002", "orders/eng-002.md", harness_id="eng-001", cwd=ready)
    assert refused.returncode == 1 and "refuse" in refused.stderr


def test_row_22_partner_edits_partner_md(ready):
    assert_allowed(guard(ready, "partner", "Edit", {
        "file_path": str(ready / "PARTNER.md"), "old_string": "# Partner state", "new_string": "# State",
    }))


# --- the rules the table leans on ---------------------------------------------------------------


def test_a_worker_may_not_write_partner_md(ready):
    result = guard(ready, "eng-001", "Edit", {
        "file_path": str(ready / "PARTNER.md"), "old_string": "# Partner state", "new_string": "x",
    })
    assert_denied(result, because="PARTNER.md is the Partner's")


def test_an_edit_the_guard_cannot_simulate_is_denied(ready):
    """A non-unique `old_string` could land anywhere, so hx does not guess (rule 1)."""
    path = ready / "config" / "eng-001" / "AGENTS.md"
    path.write_text("You are eng-001.\nrepeat\n\n## UPDATES BELOW ONLY\n\nrepeat\n")
    result = guard(ready, "eng-001", "Edit", {
        "file_path": str(path), "old_string": "repeat", "new_string": "changed",
    })
    assert_denied(result, because="only the section below the header is yours")


def test_a_write_that_keeps_the_header_section_intact_is_allowed(ready):
    path = ready / "config" / "eng-001" / "AGENTS.md"
    above = path.read_text().split("## UPDATES BELOW ONLY")[0]
    result = guard(ready, "eng-001", "Write", {
        "file_path": str(path), "content": above + "## UPDATES BELOW ONLY\n\nbrand new memory.\n",
    })
    assert_allowed(result)


def test_a_write_that_rewrites_the_persona_is_denied(ready):
    path = ready / "config" / "eng-001" / "AGENTS.md"
    result = guard(ready, "eng-001", "Write", {
        "file_path": str(path), "content": "You are someone else.\n\n## UPDATES BELOW ONLY\n\nx\n",
    })
    assert_denied(result, because="only the section below the header is yours")


def test_the_worktrees_own_config_directory_is_the_agents_business(ready):
    """Rule 2 compares against HARNESS_ROOT subtrees, not a bare relative name."""
    workdir = ready / "wt" / "eng-001"
    (workdir / "config").mkdir(parents=True, exist_ok=True)
    (workdir / "config" / "settings.py").write_text("PORT = 1\n")
    assert_allowed(guard(ready, "eng-001", "Edit", {
        "file_path": "config/settings.py", "old_string": "1", "new_string": "2",
    }))
    assert_allowed(guard(ready, "eng-001", "Read", {"file_path": "config/settings.py"}))


@pytest.mark.parametrize("command", [
    "echo $HARNESS_ROOT",
    "ls $HARNESS_ROOT/pods",
    "cat ${HARNESS_ROOT}/tasks.json",
])
def test_rule_7_denies_a_bash_command_that_reaches_into_the_instance(ready, command):
    assert_denied(guard(ready, "eng-001", "Bash", {"command": command}), because="use an `hx` command")


@pytest.mark.parametrize("command", [
    "hx complete done",
    "HX_DEBUG=1 hx board",
    "cd $HARNESS_ROOT && hx board",
])
def test_rule_7_allows_an_hx_command_that_mentions_the_root(ready, command):
    assert_allowed(guard(ready, "eng-001", "Bash", {"command": command}))


def test_rule_7_sees_the_root_by_its_absolute_path_too(ready):
    assert_denied(guard(ready, "eng-001", "Bash", {"command": f"ls {ready}/pods"}))


def test_ordinary_work_in_the_worktree_is_allowed(ready):
    workdir = ready / "wt" / "eng-001"
    (workdir / "src").mkdir(parents=True, exist_ok=True)
    (workdir / "src" / "importer.py").write_text("x = 1\n")
    assert_allowed(guard(ready, "eng-001", "Edit", {
        "file_path": "src/importer.py", "old_string": "x = 1", "new_string": "x = 2",
    }))
    assert_allowed(guard(ready, "eng-001", "Bash", {"command": "pytest -q tests/"}))
    assert_allowed(guard(ready, "eng-001", "Read", {"file_path": "src/importer.py"}))


def test_the_rules_apply_inside_a_subagent(ready):
    """spec 09.1: the tool hooks apply to the main thread and to every subagent."""
    result = guard(ready, "eng-001", "Read",
                   {"file_path": str(ready / "config/eng-002/AGENTS.md")}, agent_id="agent-xyz")
    assert_denied(result, because="identity is delivered by hook")
    assert_allowed(guard(ready, "eng-001", "Bash", {"command": "ls"}, agent_id="agent-xyz"))


def test_a_tool_with_no_paths_at_all_is_allowed(ready):
    assert_allowed(guard(ready, "eng-001", "WebFetch", {"url": "https://example.invalid", "prompt": "x"}))


def test_the_guard_never_exits_1(ready):
    """spec 09.1: "Deny = exit 2 with the reason on stderr. Never exit 1"."""
    for tool_name, tool_input in (
        ("Read", {"file_path": str(ready / "config/eng-002/AGENTS.md")}),
        ("Bash", {"command": "ls"}),
        ("Edit", {"file_path": str(ready / "tasks.json"), "old_string": "{}", "new_string": "[]"}),
        ("Grep", {"pattern": "x"}),
    ):
        assert guard(ready, "eng-001", tool_name, tool_input).returncode in (0, 2)
