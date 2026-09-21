"""build-8 item 10, live: a paste submits itself, on a fresh pane and on a warm one.

The bug: an Enter sent immediately after `paste-buffer` is swallowed while the TUI is still
ingesting the paste, and the text sits in the input box forever. It bit the first paste of a
session twice on 2026-09-20 — build-7's Companion check and the orchestrator's rehearsal.
"""

from __future__ import annotations

from .conftest import pane_text, transcript_of, wait_for, work_item


def stranded(item_id, env, text):
    """Is this text still sitting in the input box, unsubmitted?"""
    from hx import goal

    return goal._squash(text)[:40] in goal._squash(goal.input_box(pane_text(item_id, env)))


def test_the_first_paste_into_a_brand_new_pane_submits(live_root, live_env, worker):
    from hx import goal
    from hx.lifecycle import launch

    item_id, workdir = worker("be-001")
    work_item(live_root, item_id)
    launch(live_root, item_id, companion=False, env=live_env)
    goal.wait_for_prompt(item_id, live_env)

    first = "Use the Bash tool to run exactly: echo one > ONE.txt"
    goal.paste(item_id, first, live_env)
    assert not stranded(item_id, live_env, first), "the first paste was left in the input box"
    wait_for((workdir / "ONE.txt").is_file, what="the first paste to be acted on")


def test_a_second_paste_into_the_warm_pane_submits(live_root, live_env, worker):
    from hx import goal
    from hx.lifecycle import launch

    item_id, workdir = worker("be-001")
    work_item(live_root, item_id)
    launch(live_root, item_id, companion=False, env=live_env)
    goal.wait_for_prompt(item_id, live_env)

    goal.paste(item_id, "Use the Bash tool to run exactly: echo one > ONE.txt", live_env)
    wait_for((workdir / "ONE.txt").is_file, what="the first paste")

    goal.wait_for_prompt(item_id, live_env)
    second = "Use the Bash tool to run exactly: echo two > TWO.txt"
    goal.paste(item_id, second, live_env)
    assert not stranded(item_id, live_env, second)
    wait_for((workdir / "TWO.txt").is_file, what="the second paste to be acted on")
    assert "one" in (workdir / "ONE.txt").read_text()


def test_a_slash_command_pasted_into_a_fresh_pane_runs(live_root, live_env, worker):
    """`/clear` is the one `hx seam` pastes, and it is always the first of a pair."""
    from hx import goal
    from hx.lifecycle import launch

    item_id, _ = worker("be-001")
    work_item(live_root, item_id)
    launch(live_root, item_id, companion=False, env=live_env)
    goal.wait_for_prompt(item_id, live_env)

    goal.paste(item_id, "Say exactly: HELLO-FROM-THE-PANE", live_env)
    wait_for(lambda: "HELLO-FROM-THE-PANE" in transcript_of(item_id, live_env),
             what="the first message to be answered")

    goal.paste(item_id, "/clear", live_env)
    wait_for(lambda: "HELLO-FROM-THE-PANE" not in transcript_of(item_id, live_env),
             what="the `/clear` to take effect")
    assert not stranded(item_id, live_env, "/clear")
