"""build-8 items 1-4, live: the seam handshake against the real binary (spec 09.2, 13 M6).

The order spec 02 and 09.2 fix, and which this suite is here to prove:

    Stop → hx seam (flush, compose, `/clear`) → SessionStart(clear) → `/goal` → one Read

Everything before the Read is hx and the hooks; the Read is the agent's first action in the
new conversation, and there must be exactly one of it — that is the M7 metric's denominator.
"""

from __future__ import annotations

import json
import os

from .conftest import transcript_of, wait_for, work_item

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
    # `launch` waits for the idle prompt itself before pasting the pointer, and the pointer
    # starts a turn — so the pane is busy from here on, and nothing should wait for it.
    assert launch(live_root, item_id, companion=False, env=live_env)["goal"] == "pasted"
    return item_id, workdir


def take_one_seam(live_root, live_env, item_id, label=""):
    """Mark a seam and drive one turn, so the `Stop` hook at the end of it takes the seam.

    A turn has to be forced rather than waited for. Live, 2026-09-20: a `/goal` stops
    continuing by itself — "A hook blocked the turn from ending 9 consecutive times —
    overriding and ending turn", then "Goal paused · goal checks kept finding it unmet this
    turn · send a message to continue" — and the pane goes idle until something arrives.
    That pause is exactly the state `hx heartbeat` re-pastes the pointer into (build-8 item
    12); here it means the test sends its own nudge instead of waiting for a turn that is
    not coming.
    """
    from hx import goal
    from hx.hook_log import seam_marker

    before = len(reads_of_the_context_file(live_root, item_id))
    seams_before = len([r for r in records(live_root, item_id) if r.get("event") == "seam"])
    marker = seam_marker(live_root, item_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()

    # Straight into the pane, busy or not: a message to a busy session is folded into the
    # in-flight turn (01.1 E6), and waiting for idle would mean waiting out the nine turns
    # it takes a `/goal` to pause itself.
    goal.paste(item_id, f"Say SEAM{label or ''} and nothing else.", live_env)

    # The turn ends → `Stop` → `hx seam` → the queued `/clear` runs → `SessionStart(clear)`.
    wait_for(lambda: not marker.exists(),
             what=f"the stop hook to consume the seam marker {label}".strip())
    wait_for(
        lambda: len([r for r in records(live_root, item_id) if r.get("event") == "seam"])
        > seams_before,
        what=f"the seam record {label}".strip(),
    )
    return before


def test_the_seam_handshake_runs_in_the_order_spec_09_2_fixes(live_root, live_env, worker):
    item_id, _ = dispatched_worker(live_root, live_env, worker)
    before = take_one_seam(live_root, live_env, item_id)

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
        take_one_seam(live_root, live_env, item_id, label=f"-{index + 1}")

    seams = [r for r in records(live_root, item_id) if r.get("event") == "seam"]
    assert len(seams) == SEAM_RUNS, f"{len(seams)} seam records for {SEAM_RUNS} seams"
    assert "compact_pending" not in events(live_root, item_id), "native compaction was reached"
    assert "compact" not in events(live_root, item_id)
    assert "compact_boundary" not in transcript_of(item_id, live_env, lines=2000)


def test_restart_delivers_the_goal_once_the_pane_is_ready(live_root, live_env, worker):
    """spec 08 `hx restart`: the fallback seam. The pointer lands after the idle prompt.

    A relaunched pane *is* idle until the pointer reaches it, so this one can wait for a
    prompt — that is the readiness detector's whole job (build-3 item 8).
    """
    from hx.lifecycle import restart

    item_id, _ = dispatched_worker(live_root, live_env, worker)
    marker = live_root / "run" / item_id / "goal"

    result = restart(live_root, item_id, env=live_env)
    assert result["goal"] == "pasted"
    assert marker.is_file()
    # The marker's timestamp is second-resolution, so a restart inside the same second
    # leaves it byte-identical: what proves delivery is the pointer in the new pane.
    wait_for(lambda: "/goal The order for" in transcript_of(item_id, live_env),
             what="the pointer in the relaunched pane")


def test_launch_of_a_working_item_delivers_the_goal(live_root, live_env, worker):
    """spec 08: a relaunch after a reboot finds the item `working` and re-sends the pointer.

    The session is killed first, because that is the case spec 08 names — "relaunch after a
    reboot" — and because `hx launch` waits for the idle prompt before pasting. Against a
    *live* pane that is mid-turn under a `/goal`, that wait is correct and unbounded: with
    D26 raising the block cap the agent may not idle for a very long time, and `hx launch`
    is right to keep waiting rather than paste into a working conversation.
    """
    import subprocess

    from hx.lifecycle import launch

    item_id, _ = dispatched_worker(live_root, live_env, worker)
    subprocess.run([*live_env["HX_TMUX"].split(), "kill-session", "-t", f"={item_id}"], check=True)

    result = launch(live_root, item_id, companion=False, env=live_env)
    assert result["session"] == "started" and result["goal"] == "pasted"
    assert (live_root / "run" / item_id / "goal").is_file()
    wait_for(lambda: "/goal The order for" in transcript_of(item_id, live_env),
             what="the pointer in the relaunched pane")
