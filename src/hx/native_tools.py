"""Transactional admission of observable native tool calls.

An acknowledged tool result settles its call, not any process it started. This
registry supplies one shutdown barrier; it never certifies process quiescence.
Only call identities and input hashes live here; evidence stays in capture.
"""

from . import native_launch, unit_execution
from .continuity_store import Conflict, _id, digest
from .errors import ValidationError


class AdmissionDenied(Conflict):
    """An intentional refusal, not a lost observation or capture gap."""


def _identity(payload):
    session = payload.get("session_id") or payload.get("sessionId") or payload.get("transcript_path")
    actor = payload.get("agent_id") or payload.get("agentId") or ""
    call = payload.get("tool_use_id") or payload.get("toolUseId")
    name = payload.get("tool_name") or payload.get("toolName")
    for value in (session, call, name):
        _id(value)
    if actor:
        _id(actor)
    if "tool_input" not in payload and "toolInput" not in payload:
        raise ValidationError("native tool observation lacks its input")
    return session, actor, call, digest([name, payload.get("tool_input", payload.get("toolInput"))])


def pending(tx, launch_id):
    return tx.db.execute("SELECT count(*) FROM native_tool_calls WHERE launch_id=? AND status='admitted'",
                         (launch_id,)).fetchone()[0]


def child(tx, run_id, session_id, event):
    row = native_launch._row(tx, run_id)
    if row is None:
        return
    actor = event.data.get("agent_id")
    if not actor and event.kind == "finish":
        return  # Main turn end says nothing about outstanding children.
    _id(actor)
    session = event.data.get("child_session_id") or session_id
    _id(session)
    previous = tx.db.execute("SELECT session_id,status FROM native_children WHERE launch_id=? AND actor_id=?",
                             (row["launch_id"], actor)).fetchone()
    if previous and previous[0] != session:
        raise Conflict("native child identity changed session")
    tx._change()
    # A late/duplicate start must never reopen a child already observed stopped.
    tx.db.execute("""INSERT INTO native_children VALUES(?,?,?,?) ON CONFLICT(launch_id,actor_id)
        DO UPDATE SET status=CASE WHEN excluded.status='settled' THEN 'settled' ELSE native_children.status END""",
        (row["launch_id"], actor, session, "active" if event.kind == "spawn" else "settled"))


def _actor(tx, row, session, actor, *, admission=False):
    if actor:
        found = tx.db.execute("SELECT actor_id,session_id,status FROM native_children WHERE launch_id=? AND actor_id=?",
                              (row["launch_id"], actor)).fetchone()
    elif session != row["payload"].get("native_session"):
        matches = tx.db.execute("SELECT actor_id,session_id,status FROM native_children WHERE launch_id=? AND session_id=? LIMIT 2",
                               (row["launch_id"], session)).fetchall()
        found = matches[0] if len(matches) == 1 else None
    else:
        return ""
    if found is None or found[1] != session or (admission and found[2] != "active"):
        raise AdmissionDenied("tool call has no active child bound to this native session")
    return found[0]


def admit(store, run_id, launch_id, payload):
    """Commit ownership before allowing execution; serialize with drain/finish."""
    try:
        _admit(store, run_id, launch_id, payload)
    except AdmissionDenied:
        # Hosts may still emit an error result for a deliberately blocked call.
        # Retain that identity so it does not masquerade as missing capture.
        session, actor, call, fingerprint = _identity(payload)
        with store.transaction() as tx:
            row = native_launch._row(tx, run_id)
            if row and row["launch_id"] == launch_id:
                tx._change()
                tx.db.execute("INSERT OR IGNORE INTO native_tool_calls VALUES(?,?,?,?,?,'denied')",
                              (launch_id, session, actor, call, fingerprint))
        raise


def _admit(store, run_id, launch_id, payload):
    session, actor, call, fingerprint = _identity(payload)
    snapshot = native_launch._row(store, run_id)
    if snapshot is None or snapshot["launch_id"] != launch_id:
        raise AdmissionDenied("tool execution requires its original prepared native launch")
    if snapshot["status"] != "submitted" or not snapshot["payload"].get("startup_observed"):
        raise AdmissionDenied("native tool admission is closed; reconcile the assignment before continuing")
    # Do not hold SQLite's writer lock while querying an external process.
    # Recheck durable status and the pane receipt in the admission transaction.
    from .native_controller import _owned_pane
    pane = _owned_pane(snapshot)
    from . import runtime_policy
    with store.transaction() as tx:
        _, task, _ = unit_execution._run(tx, run_id)
    reading = runtime_policy.inspect_call(snapshot, task['payload'], payload)
    with store.transaction() as tx:
        row = native_launch._row(tx, run_id)
        if row is None:
            # A capture-only contract does not authorize native execution.
            raise AdmissionDenied("tool execution requires a prepared native launch")
        if row["launch_id"] != launch_id:
            raise AdmissionDenied("tool call belongs to another native launch")
        run, _, _ = unit_execution._run(tx, run_id)
        if run["phase"] == "paused" or row["status"] != "submitted" or not row["payload"].get("startup_observed"):
            raise AdmissionDenied("native tool admission is closed; reconcile the assignment before continuing")
        if row["payload"].get("pane") != pane:
            raise AdmissionDenied("native pane ownership changed before tool admission")
        actor = _actor(tx, row, session, actor, admission=True)
        key = (launch_id, session, actor, call)
        previous = tx.db.execute("SELECT input_hash,status FROM native_tool_calls WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?", key).fetchone()
        if previous:
            if previous[0] != fingerprint or previous[1] != "admitted":
                raise AdmissionDenied("native tool identity was reused after settlement or with different input")
            return
        from .native_completion import admission_closed
        if admission_closed(tx, run_id):
            raise AdmissionDenied('completion is being verified; end the native turn and wait for the runtime')
        if pending(tx, launch_id) >= 256:
            raise AdmissionDenied("native tool admission limit reached; finish outstanding calls first")
        tx._change()
        tx.db.execute("INSERT INTO native_tool_calls VALUES(?,?,?,?,?,'admitted')", (*key, fingerprint))
        runtime_policy.remember_read(tx, row, session, actor, call, reading)


def settle(tx, run_id, session_id, payload):
    """Settle inside the capture transaction, including durable queue retries."""
    row = native_launch._row(tx, run_id)
    if row is None:
        return
    session, actor, call, fingerprint = _identity({**payload, "session_id": session_id})
    denied = tx.db.execute("SELECT input_hash FROM native_tool_calls WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=? AND status='denied'",
                           (row["launch_id"], session, actor, call)).fetchone()
    if denied and denied[0] == fingerprint:
        return
    actor = _actor(tx, row, session, actor)
    key = (row["launch_id"], session, actor, call)
    previous = tx.db.execute("SELECT input_hash,status FROM native_tool_calls WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?", key).fetchone()
    if previous is None or previous[0] != fingerprint:
        raise Conflict("native tool result has no matching admitted call; reconcile capture")
    if previous[1] == "settled":
        return
    tx._change()
    tx.db.execute("UPDATE native_tool_calls SET status='settled' WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?", key)
    from .runtime_policy import settled
    settled(tx, row['launch_id'], session, actor, call, payload)
