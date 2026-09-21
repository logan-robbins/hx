"""build-8 items 1-4, live: the seam handshake against the real binary (spec 09.2, 13 M6).

The order spec 02 and 09.2 fix, and which this suite is here to prove:

    Stop → hx seam (flush, compose, `/clear`) → SessionStart(clear) → `/goal` → one Read

Everything before the Read is hx and the hooks; the Read is the agent's first action in the
new conversation, and there must be exactly one of it — that is the M7 metric's denominator.
"""

from __future__ import annotations

import json
import os

from .conftest import LIVE_MODEL, transcript_of, wait_for, work_item

#: The M6 criterion is ten seams; a smaller number is for iterating on the test itself.
SEAM_RUNS = int(os.environ.get("HX_LIVE_SEAMS", "10"))


def records(live_root, item_id, stream=None):
    from hx.streams import iter_records, stream_path

    return list(iter_records(stream_path(live_root, item_id, stream or f"{item_id}-main")))


def events(live_root, item_id):
    return [r.get("event") for r in records(live_root, item_id)]


def reads_of_the_context_file(live_root, item_id, since_seq=0):
    """Every `Read` tool call whose target is the composed context file."""
    context = str(live_root / "run" / item_id / f"{item_id}-main.context.md")
    found = []
    for record in records(live_root, item_id):
        if record.get("seq", 0) <= since_seq or record.get("event") != "post_tool":
            continue
        if record.get("tool") == "Read" and context in json.dumps(record, default=str):
            found.append(record)
    return found


#: A real order, so the `/goal` evaluator keeps the agent working instead of deciding the
#: order is malformed and completing `blocked` — which is what a bare "Say READY" produced on
#: the first live run. The checks can never pass, so the agent cannot finish and wander off:
#: the seam handshake is what this file is about, not completion.
STANDING_ORDER = """## Order
Wait for instructions. Answer each message with exactly what it asks, in as few words as
possible, and then stop. Do not run `hx complete`: this task ends when the human says so.

## Definition of done
- [ ] the human has said the session is over

### Checks

```bash
test -f NEVER-WRITTEN.txt
```
"""


def dispatched_worker(live_root, live_env, worker, order=STANDING_ORDER):
    """A live worker with a `working` item and its goal delivered."""
    from hx import goal
    from hx.lifecycle import launch

    item_id, workdir = worker("be-001")
    path = work_item(live_root, item_id, state="working")
    path.write_text(
        f"---\nid: {item_id}\npod: engineers\noutcome:\ndispatched: 2026-09-20T12:00:00Z\n---\n\n"
        f"{order}\n## Tasks\n- [ ] wait for instructions\n"
    )
    (live_root / "tasks.json").write_text(json.dumps({item_id: {
        "order": order, "addenda": [], "outcome": None,
        "dispatched": "2026-09-20T12:00:00Z", "completed": None,
    }}))
    launch(live_root, item_id, companion=False, env=live_env)
    goal.wait_for_prompt(item_id, live_env)
    return item_id, workdir


def take_one_seam(live_root, live_env, item_id, nudge):
    """Mark a seam, make the agent take a turn, and wait for the `/clear` to land."""
    from hx import goal
    from hx.hook_log import seam_marker

    before = len(reads_of_the_context_file(live_root, item_id))
    marker = seam_marker(live_root, item_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()

    goal.wait_for_prompt(item_id, live_env)
    goal.paste(item_id, nudge, live_env)
    # The turn ends → `Stop` → `hx seam` → the queued `/clear` runs → `SessionStart(clear)`.
    wait_for(lambda: not marker.exists(), what="the stop hook to consume the seam marker")
    wait_for(lambda: nudge[:30] not in transcript_of(item_id, live_env),
             what="the `/clear` to cut the conversation")
    return before


def test_the_seam_handshake_runs_in_the_order_spec_09_2_fixes(live_root, live_env, worker):
    item_id, _ = dispatched_worker(live_root, live_env, worker)
    before = take_one_seam(live_root, live_env, item_id, "Say SEAM-ONE and nothing else.")

    # 1. hx seam wrote its record (spec 07.4) ...
    assert "seam" in events(live_root, item_id)
    seam_record = [r for r in records(live_root, item_id) if r.get("event") == "seam"][-1]
    assert seam_record["source"] == "clear"
    assert seam_record["context_file_bytes"] > 0

    # 2. ... the `context` hook sent the goal again ...
    assert (live_root / "run" / item_id / "goal").is_file()

    # 3. ... and the agent's first action in the new conversation is one Read of the file.
    reads = wait_for(lambda: reads_of_the_context_file(live_root, item_id)[before:] or None,
                     what="the agent's Read of its context file after the seam")
    assert len(reads) == 1, f"the context file was read {len(reads)} times, not once (M7)"


def test_ten_seams_never_reach_native_compaction(live_root, live_env, worker):
    """spec 02: the seam threshold is far below the native window, so hx gets there first."""
    item_id, _ = dispatched_worker(live_root, live_env, worker)
    for index in range(SEAM_RUNS):
        take_one_seam(live_root, live_env, item_id, f"Say SEAM-{index} and nothing else.")

    seams = [r for r in records(live_root, item_id) if r.get("event") == "seam"]
    assert len(seams) == SEAM_RUNS, f"{len(seams)} seam records for {SEAM_RUNS} seams"
    assert "compact_pending" not in events(live_root, item_id), "native compaction was reached"
    assert "compact" not in events(live_root, item_id)
    assert "compact_boundary" not in transcript_of(item_id, live_env, lines=2000)


def test_restart_delivers_the_goal_once_the_pane_is_ready(live_root, live_env, worker):
    """spec 08 `hx restart`: the fallback seam. The pointer lands after the idle prompt."""
    from hx.lifecycle import restart

    item_id, _ = dispatched_worker(live_root, live_env, worker)
    marker = live_root / "run" / item_id / "goal"
    first = marker.read_text()

    result = restart(live_root, item_id, env=live_env)
    assert result["goal"] == "pasted"
    assert marker.read_text() != first, "the goal marker is rewritten at the new launch"
    assert "/goal The order for" in transcript_of(item_id, live_env)


def test_launch_of_a_working_item_delivers_the_goal(live_root, live_env, worker):
    from hx.lifecycle import launch

    item_id, _ = dispatched_worker(live_root, live_env, worker)
    assert launch(live_root, item_id, companion=False, env=live_env)["goal"] == "pasted"
    assert LIVE_MODEL  # the cheap model this suite runs on
