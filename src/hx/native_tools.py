"""Transactional admission of observable native tool calls.

An acknowledged tool result settles its call, not any process it started. This
registry supplies one shutdown barrier; it never certifies process quiescence.
Only call identities and input hashes live here; evidence stays in capture.
"""

from . import native_launch, unit_execution
from .continuity_store import Conflict, LedgerBusy, _id, digest
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
    active = tx.db.execute("SELECT count(*) FROM native_tool_calls WHERE launch_id=? AND status='admitted'",
                          (launch_id,)).fetchone()[0]
    # A settled native preview is not a completed file delivery. Preserve the
    # completion barrier until a later complete retry resolves the same file.
    incomplete = tx.db.execute('''SELECT count(*) FROM native_reads r WHERE r.launch_id=? AND r.success=-1
        AND NOT EXISTS (SELECT 1 FROM native_reads ok WHERE ok.launch_id=r.launch_id
            AND ok.session_id=r.session_id AND ok.actor_id=r.actor_id AND ok.checkpoint_id=r.checkpoint_id
            AND ok.read_key=r.read_key AND ok.success=1 AND ok.rowid>r.rowid)''', (launch_id,)).fetchone()[0]
    # A material chain counts as delivered only when every part has its own receipt.
    from .file_reads import incomplete_material
    return active + incomplete + len(incomplete_material(tx, launch_id))


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


def admit(store, run_id, launch_id, payload, *, adapter):
    """Commit ownership before allowing execution; serialize with drain/finish."""
    try:
        _admit(store, run_id, launch_id, payload, adapter)
    except AdmissionDenied as exc:
        # Hosts may still emit an error result for a deliberately blocked call.
        # Retain that identity so it does not masquerade as missing capture.
        session, actor, call, fingerprint = _identity(payload)
        try:
            with store.transaction() as tx:
                row = native_launch._row(tx, run_id)
                if row and row["launch_id"] == launch_id:
                    tx._change()
                    tx.db.execute("INSERT OR IGNORE INTO native_tool_calls VALUES(?,?,?,?,?,'denied')",
                                  (launch_id, session, actor, call, fingerprint))
                    served = getattr(exc, "receipt", None)
                    if served:  # A search the cache answered is a search the worker made.
                        from .search_receipts import record
                        record(tx, run_id, **served)
        except LedgerBusy:
            pass  # The refusal stands; its unmatched error result becomes a capture gap.
        raise


def _admit(store, run_id, launch_id, payload, adapter):
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
    with store.snapshot() as tx:
        _, task, _ = unit_execution._run(tx, run_id)
    from .file_read_delivery import require_native_read
    try:
        require_native_read(adapter, payload)
    except ValidationError as exc:
        raise AdmissionDenied(str(exc)) from exc
    reading = runtime_policy.inspect_call(snapshot, task['payload'], payload, root=store.root)
    if reading is not None:
        import json
        from .file_reads import ensure_capacity
        try:
            ensure_capacity(store, snapshot, json.loads(reading[1]))
        except ValidationError as exc:
            raise AdmissionDenied(str(exc)) from exc
    from . import search_coverage
    search = search_coverage.prepare(task['payload'], payload)
    with store.transaction() as tx:
        row = native_launch._row(tx, run_id)
        if row is None:
            # A capture-only contract does not authorize native execution.
            raise AdmissionDenied("tool execution requires a prepared native launch")
        if row["launch_id"] != launch_id:
            raise AdmissionDenied("tool call belongs to another native launch")
        run, current_task, _ = unit_execution._run(tx, run_id)
        if run["phase"] == "paused" or row["status"] != "submitted" or not row["payload"].get("startup_observed"):
            raise AdmissionDenied("native tool admission is closed; reconcile the assignment before continuing")
        if tx.db.execute('SELECT 1 FROM native_capture_gaps WHERE run_id=? LIMIT 1', (run_id,)).fetchone():
            raise AdmissionDenied('native capture has an unresolved gap; reconcile it before another tool operation')
        if row["payload"].get("pane") != pane:
            raise AdmissionDenied("native pane ownership changed before tool admission")
        if any(row['payload'][field] != snapshot['payload'][field] for field in ('checkpoint_id', 'required_files')):
            raise AdmissionDenied('native context checkpoint changed before tool admission; inspect its current required material')
        runtime_policy.verify_material_read(tx, row, current_task['payload'], payload, reading)
        actor = _actor(tx, row, session, actor, admission=True)
        runtime_policy.require_complete_reads(tx, row, session, actor, reading)
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
        search_coverage.remember(tx, row, session, actor, call, search)


def record_executed(store, run_id, launch_id, payload):
    """A deferred tool call ran without admission; record it as admitted so the
    shutdown barrier waits for its result and settlement matches it."""
    session, actor, call, fingerprint = _identity(payload)
    with store.transaction() as tx:
        row = native_launch._row(tx, run_id)
        if row is None or row["launch_id"] != launch_id:
            raise Conflict("deferred tool call lost its native launch")
        actor = _actor(tx, row, session, actor)
        previous = tx.db.execute("SELECT input_hash FROM native_tool_calls WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?",
                                 (launch_id, session, actor, call)).fetchone()
        if previous:
            if previous[0] != fingerprint:
                raise Conflict("deferred tool call reused an identity with different input")
            return
        tx._change()
        tx.db.execute("INSERT INTO native_tool_calls VALUES(?,?,?,?,?,'admitted')", (launch_id, session, actor, call, fingerprint))


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
    from .search_coverage import settle as settle_search
    receipt = settle_search(tx, row['launch_id'], session, actor, call, payload)
    if receipt:
        from .search_receipts import record
        record(tx, run_id, **receipt)
