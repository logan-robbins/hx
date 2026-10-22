"""Durable, single-submission control of fresh planned native sessions."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from . import context_packets, goal, native_launch, prompt_compiler, tmux, unit_execution
from .caller import require_partner_caller
from .continuity_store import Conflict, canonical, digest
from .errors import HxError, ValidationError
from .store import atomic_write_text


def _prompt(launch_id):
    return (f"Assignment {launch_id}: the complete current task context follows in this input. "
            "Complete the declared unit using every supplied constraint and acceptance check. "
            "Do not reread its backing files to reconstruct the same context.")


def _input_end(launch_id):
    # Keep every packet byte inside a non-whitespace envelope. Native editors
    # may trim outer whitespace, including the checkpoint's final newline.
    return f"\n\nEnd of complete input for assignment {launch_id}."


def _submission_prompt(row):
    """The exact frozen native input, delivered without a required model read."""
    return _prompt(row["launch_id"]) + "\n\n" + native_launch._bytes(
        Path(row["payload"]["packet_path"]), 262144).decode("utf-8") + _input_end(row["launch_id"])


def startup_packet_path(capsule, run_id, launch_id):
    checkpoint = str(uuid.uuid5(uuid.NAMESPACE_URL, "hx-context:" + run_id + ":startup-" + launch_id))
    return Path(capsule).parent / (checkpoint + ".md")


def startup_overhead(capsule, run_id, launch_id, system):
    """Charge the exact system prefix and submitted complete-input marker."""
    return len((system + _prompt(launch_id) + "\n\n" + _input_end(launch_id)).encode())


def required_files(payload, packet):
    """Retain the invariant instruction bundle and replace assignment references."""
    return [item for item in payload['required_files'] if item.get('kind') != 'assignment'] + packet['required_files']


def _transition(tx, run_id, expected, status, *, payload=None, error=None):
    row = native_launch._row(tx, run_id)
    if row is None or row["status"] not in expected:
        return False
    tx._change()
    tx.db.execute("UPDATE native_launches SET status=?,payload=?,error=? WHERE run_id=? AND status<>'archived'",
                  (status, canonical(payload or row["payload"]), error, run_id))
    return True


def _result(ledger, run_id):
    from .native_tools import pending
    row = native_launch._row(ledger, run_id)
    body = row["payload"]
    shutdown = body.get("shutdown", {})
    return {"run_id": run_id, "launch_id": row["launch_id"], "status": row["status"],
            "session": body.get("session"), "checkpoint_id": body["checkpoint_id"],
            "instruction_delivery": body["instruction_delivery"], "error": row["error"],
            "pending_tool_calls": pending(ledger, row["launch_id"]),
            "active_children": ledger.db.execute("SELECT count(*) FROM native_children WHERE launch_id=? AND status='active'",
                                                 (row["launch_id"],)).fetchone()[0],
            "shutdown_phase": shutdown.get("phase"), "observed_processes": len(shutdown.get("processes", []))}


def _native_env(ledger, row, env):
    result = native_launch.environment(ledger.root, row, env=env)
    result["HX_CONTINUITY_SESSION"] = row["payload"]["session"]
    return result


def _tmux(row, *args, timeout=10):
    return subprocess.run([*row["payload"]["tmux"], *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


def _owned_pane(row):
    """Require the exact launch environment and stable native pane identity."""
    session = row["payload"]["session"]
    identity = _tmux(row, "show-environment", "-t", "=" + session, "HX_CONTINUITY_LAUNCH")
    if identity.returncode or identity.stdout.strip() != "HX_CONTINUITY_LAUNCH=" + row["launch_id"]:
        raise Conflict("native session is missing or does not belong to this launch")
    pane = _tmux(row, "display-message", "-p", "-t", "=" + session + ":main",
                 "#{pane_id} #{pane_pid} #{pane_dead}")
    fields = pane.stdout.strip().split()
    if pane.returncode or len(fields) != 3 or not fields[0].startswith("%") or not fields[1].isdigit() or fields[2] != "0":
        raise Conflict("native main pane is unavailable")
    actual = {"id": fields[0], "pid": int(fields[1])}
    from .native_processes import probe
    process = probe(actual["pid"])
    if process is None:
        raise Conflict("native main process exited before ownership was recorded")
    actual["process"] = process["identity"]
    if row['payload'].get('supervisor_process') not in (None, actual['process']):
        raise Conflict('native pane is not the recorded supervisor instance')
    if row["payload"].get("pane") not in (None, actual):
        raise Conflict("native main pane was replaced")
    return actual


def advance(ledger, worker, run_id, request_id, *, env=None, submit=True, ensure_service=True):
    """Advance once; retries inspect durable state and never repaste blindly.

    The call returns while the native UI is starting or busy. Repeating the same
    request advances a ready session; an ambiguous operation retains its leases.
    """
    require_partner_caller("launch", env)
    with ledger.transaction() as tx:
        run, _, _ = unit_execution._run(tx, run_id)
        if run['worker_id'] != worker:
            raise Conflict('native launch belongs to another worker')
    row = native_launch._row(ledger, run_id)
    if row is None:
        row = native_launch.prepare(ledger, worker, run_id, request_id, env=env)
    if row["request_id"] != request_id or row["payload"]["worker_id"] != worker:
        raise Conflict("native launch retry must name its original worker and request")
    if row["status"] == "prepared":
        verified = native_launch.verify(ledger, run_id)
        with ledger.transaction() as tx:
            row = native_launch.confirm(tx, verified)
            payload = {**row["payload"], "session": "hx-" + row["launch_id"], "tmux": tmux.tmux_command(env),
                       "supervisor": "receipt-wait"}
            _transition(tx, run_id, {"prepared"}, "installing", payload=payload)
        row = native_launch._row(ledger, run_id)
        capsule = Path(payload["capsule"])
        child = _native_env(ledger, row, env)
        adapter = child["HX_CONTINUITY_ADAPTER"]
        command = ["bash", str(capsule / "adapters" / adapter / "install.sh")]
        try:
            # Installation output may include authentication diagnostics. Keep it
            # out of the ledger and bound the installer, which starts no worker.
            installed = subprocess.run([*command, worker], env=child, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=30, check=False)
            if installed.returncode:
                raise HxError("native installer failed")
            verified = native_launch.verify(ledger, run_id, states={"installing"})
            with ledger.transaction() as tx:
                native_launch.confirm(tx, verified)
                _transition(tx, run_id, {"installing"}, "installed")
        except Exception as exc:
            with ledger.transaction() as tx:
                _transition(tx, run_id, {"installing"}, "installation_failed", error=type(exc).__name__)
            raise
    row = native_launch._row(ledger, run_id)
    if row["status"] == "installed":
        verified = native_launch.verify(ledger, run_id, states={"installed"})
        with ledger.transaction() as tx:
            row = native_launch.confirm(tx, verified)
            # Claim before touching tmux. A crash after this point is uncertain;
            # another controller never creates a replacement session implicitly.
            _transition(tx, run_id, {"installed"}, "starting")
        payload = row["payload"]
        capsule = Path(payload["capsule"])
        child = _native_env(ledger, row, env)
        environment = []
        for name, value in child.items():
            if name.startswith("HX_") or name in {"HARNESS_ROOT", "HOME", "PATH", "PYTHONPATH"}:
                environment += ["-e", f"{name}={value}"]
        try:
            # new-session fails on a collision. The supervisor records its own
            # process instance before it starts the native executable.
            launched = _tmux(row, "new-session", "-d", "-s", payload["session"], "-n", "main",
                "-c", json.loads(native_launch._bytes(capsule / "config" / worker / "harness.json"))["workdir"],
                # tmux resumes its immediate child after SIGSTOP. Keep the
                # waiting supervisor as that child so its native descendants
                # can be stopped without suspending a shared tmux server.
                *environment, str(capsule / 'bin/hx-supervise'), str(ledger.root), run_id, row['launch_id'],
                "bash", str(capsule / "adapters" / child["HX_CONTINUITY_ADAPTER"] / "start.sh"),
                "--exec", worker)
            if launched.returncode:
                raise HxError("native session creation failed; inspect its original launch before recovery")
            with ledger.transaction() as tx:
                current = native_launch._row(tx, run_id)
                tx._change()
                tx.db.execute("UPDATE native_launches SET payload=? WHERE run_id=? AND status<>'archived'",
                              (canonical({**current["payload"], "spawn_returned": True}), run_id))
        except Exception as exc:
            with ledger.transaction() as tx:
                _transition(tx, run_id, {"starting"}, "start_uncertain", error=type(exc).__name__)
            raise
    row = native_launch._row(ledger, run_id)
    if row['status'] == 'start_uncertain' and row['payload'].get('supervisor_process'):
        _owned_pane(row)
        with ledger.transaction() as tx:
            _transition(tx, run_id, {'start_uncertain'}, 'starting',
                        payload={**row['payload'], 'spawn_returned': True})
        row = native_launch._row(ledger, run_id)
    if row['status'] == 'starting' and not row['payload'].get('spawn_returned') and row['payload'].get('supervisor_process'):
        _owned_pane(row)
        with ledger.transaction() as tx:
            _transition(tx, run_id, {'starting'}, 'starting', payload={**row['payload'], 'spawn_returned': True})
        row = native_launch._row(ledger, run_id)
    if submit and ensure_service and row['status'] in {'starting', 'ready', 'submitted', 'submission_unconfirmed'}:
        from .application_loop import ensure
        ensure(ledger.root, env=env)
    if submit and row["status"] in {"starting", "ready"}:
        # Some TUIs defer SessionStart until the first prompt. Submit only the
        # launch marker; the request hook refuses execution until startup has
        # supplied and verified the current task context.
        if row["status"] == "starting" and not row["payload"].get("spawn_returned"):
            return _result(ledger, run_id)
        try:
            pane = _owned_pane(row)
        except Conflict:
            with ledger.transaction() as tx:
                _transition(tx, run_id, {row["status"]}, "session_uncertain", error="original native pane is unavailable")
            return _result(ledger, run_id)
        child = {**(os.environ if env is None else env), "HX_TMUX": " ".join(row["payload"]["tmux"])}
        target = row["payload"]["session"]
        visible = goal.capture_pane(target, child)
        if visible is None or not goal.pane_is_idle(visible):
            return _result(ledger, run_id)
        try:
            native_launch.verify(ledger, run_id, states={"starting", "ready"})
        except Conflict as exc:
            if 'newer execution evidence' not in str(exc):
                raise
            current = native_launch._row(ledger, run_id)
            if current['status'] == 'ready' and current['payload'].get('startup_observed'):
                refresh_before_submission(ledger, current)
            else:
                return _result(ledger, run_id)
        verified = native_launch.verify(ledger, run_id, states={"starting", "ready"})
        with ledger.transaction() as tx:
            row = native_launch.confirm(tx, verified)
            payload = {**row["payload"], "pane": pane}
            prompt = _submission_prompt(row)
            payload["submission_hash"] = digest(prompt)
            _transition(tx, run_id, {"starting", "ready"}, "submitting", payload=payload)
        try:
            _owned_pane(native_launch._row(ledger, run_id))
            # A native request hook, not a disappearing input box, confirms the
            # exact prompt. Transport success alone remains unconfirmed.
            goal.paste(target, prompt, child)
            with ledger.transaction() as tx:
                _transition(tx, run_id, {"submitting"}, "submission_unconfirmed")
        except Exception as exc:
            with ledger.transaction() as tx:
                _transition(tx, run_id, {"submitting"}, "submission_uncertain", error=type(exc).__name__)
            raise
    return _result(ledger, run_id)


def reconcile_launches(ledger, *, env=None, limit=8):
    """Advance existing launch intents fairly, retaining uncertain execution ownership.

    A reserved run without a native launch is deliberately absent. Only the
    explicit launch command authorizes native installation and execution.
    """
    from .application_loop import _save, _state
    after = _state(ledger, 'launch-round').get('after', '')
    rows = ledger.db.execute("""SELECT l.run_id FROM native_launches l JOIN runs r USING(run_id)
        WHERE r.ended_at IS NULL AND l.status IN
        ('starting','ready','submitted','submission_unconfirmed','submitting','start_uncertain')
        ORDER BY l.run_id<=?,l.run_id LIMIT ?""", (after, limit)).fetchall()
    results = []
    for found in rows:
        run_id = found['run_id']
        _save(ledger, 'launch-round', {'after': run_id})
        row = native_launch._row(ledger, run_id)
        try:
            if row['status'] in {'submitted', 'submission_unconfirmed', 'submitting'}:
                try:
                    _owned_pane(row)
                except Conflict:
                    with ledger.transaction() as tx:
                        if _transition(tx, run_id, {row['status']}, 'session_uncertain',
                                       error='original native pane is unavailable; execution ownership retained'):
                            tx.enqueue('native_launch_unresolved', 'native-session:' + row['launch_id'],
                                       {'task_id': tx.db.execute('SELECT task_id FROM runs WHERE run_id=?', (run_id,)).fetchone()[0],
                                        'identity': row['launch_id'], 'run_id': run_id})
                else:
                    if row['status'] == 'submitting':
                        with ledger.transaction() as tx:
                            _transition(tx, run_id, {'submitting'}, 'submission_uncertain',
                                        error='submission interrupted before acknowledgement')
                result = _result(ledger, run_id)
            else:
                result = advance(ledger, row['payload']['worker_id'], run_id,
                                 row['request_id'], env=env, ensure_service=False)
            if _state(ledger, 'launch-error:' + run_id):
                _save(ledger, 'launch-error:' + run_id, {})
        except (HxError, OSError, subprocess.SubprocessError) as exc:
            result = {'run_id': run_id, 'status': 'unresolved', 'error': str(exc)}
            if _state(ledger, 'launch-error:' + run_id) != result:
                _save(ledger, 'launch-error:' + run_id, result)
        results.append(result)
    return results


def drain(ledger, worker, run_id, request_id, *, env=None):
    """Close admissions durably while retaining every execution lease.

    A drain is one prerequisite for shutdown. Zero observable calls does not
    prove the native process or its detached/background work has stopped.
    """
    require_partner_caller("launch drain", env)
    with ledger.transaction() as tx:
        row = native_launch._row(tx, run_id)
        if not row or row["request_id"] != request_id or row["payload"]["worker_id"] != worker:
            raise Conflict("native drain must name its original worker and launch request")
        if row["payload"].get("shutdown"):
            return _result(tx, run_id)
        if row["status"] != "draining":
            if row["status"] not in {"ready", "submitted", "submission_unconfirmed", "submission_uncertain",
                                      "submission_rejected", "continuation_required", "session_uncertain", "starting", "start_uncertain", "startup_failed"}:
                raise Conflict("native launch is changing state; reconcile startup before draining")
            # Closing admission must work even when task inputs were invalidated.
            # The immutable launch tuple, not a fresh assignment, owns shutdown.
            payload = {**row["payload"], "drain_from": row["status"]}
            _transition(tx, run_id, {row["status"]}, "draining", payload=payload)
    return _result(ledger, run_id)


def observe(ledger, run_id, launch_id, event, observation):
    """Handle lifecycle evidence after its public payload is durably captured."""
    row = native_launch._row(ledger, run_id)
    if row is None:  # Standalone capture contracts need no launch controller.
        return None
    if row["launch_id"] != launch_id:
        raise Conflict("native lifecycle event belongs to another launch")
    if event == "request" and (row["status"] in {"draining", "quiesced"} or row["payload"].get("shutdown")):
        from .native_tools import AdmissionDenied
        raise AdmissionDenied("native request admission is closed for shutdown")
    if row["payload"].get("shutdown"):
        return None  # Late context hooks cannot reopen a terminating launch.
    if observation.get("agent_id") or observation.get("agentId"):
        return None
    native_session = observation.get("session_id") or observation.get("sessionId") or observation.get("transcript_path")
    if event == "context":
        if observation.get("source", "startup") != "startup":
            from .compaction_policy import continue_context
            try:
                return continue_context(ledger, row, observation)
            except HxError as exc:
                with ledger.transaction() as tx:
                    current = native_launch._row(tx, run_id)
                    if current['status'] in {'starting', 'ready', 'submitted', 'continuation_required'}:
                        payload = {**current['payload'], 'startup_observed': False,
                                   'instruction_delivery': 'continuation_boundary_required'}
                        _transition(tx, run_id, {current['status']}, 'continuation_required', payload=payload)
                raise Conflict('native continuation requires a controlled checkpoint boundary: ' + str(exc)) from None
        if row["payload"].get("startup_observed"):
            # A duplicate startup cannot reinstall context into a running turn.
            return None
        startup_states = {"starting", "submitting", "submission_unconfirmed", "submission_uncertain"}
        if row["status"] not in startup_states:
            return None
        pane = _owned_pane(row)
        # This runs in the startup hook: verify in a read snapshot, then take
        # the writer only to confirm the verified row and record the step.
        with ledger.snapshot() as tx:
            row = native_launch._row(tx, run_id)
            if row["payload"].get("startup_observed") or row["status"] not in startup_states:
                return None
            row = native_launch.verify_inputs(tx, run_id, states=startup_states, check_evidence=False)
            _, task, _ = unit_execution._run(tx, run_id)
        payload = row["payload"]
        previous_status = row["status"]
        _, rendered = prompt_compiler.verified_bundle(ledger.root, payload["worker_id"],
            Path(payload["manifest"]), workdir=task["payload"]["workdir"])
        persona = Path(payload["capsule"]) / "run" / payload["worker_id"] / "persona.md"
        if native_launch._bytes(persona, 262144) != rendered["system"].encode():
            raise Conflict("native launcher did not install its expected system prefix")
        with ledger.transaction() as tx:
            current = native_launch._row(tx, run_id)
            if current is not None and (current["payload"].get("startup_observed") or current["status"] not in startup_states):
                return None
            native_launch.confirm(tx, row, check_evidence=False)
            _transition(tx, run_id, startup_states, "composing_startup")
        # Startup acknowledges ownership and the installed prefix only. The
        # full frozen checkpoint travels in the exact submitted native input;
        # no hook envelope, pointer or model read carries required context.
        payload = {**payload, "pane": pane, "native_session": native_session,
                   "startup_observed": True,
                   "instruction_delivery": "prefix_verified_waiting_for_complete_input"}
        with ledger.transaction() as tx:
            unit_execution._run(tx, run_id)
            _transition(tx, run_id, {"composing_startup"},
                        "ready" if previous_status == "starting" else previous_status, payload=payload)
        return None
    if event == "request" and row["status"] == "continuation_required" and row["payload"].get("instruction_restart"):
        raise Conflict("current reviewed instructions require their queued controlled restart before another model request")
    if event == "request" and not row["payload"].get("startup_observed"):
        with ledger.transaction() as tx:
            _transition(tx, run_id, {row["status"]}, "submission_rejected", error="startup context is not verified")
        raise Conflict("native startup context is not verified; do not execute this assignment")
    if event == "request":
        from .runtime_policy import check_files
        check_files(row)
        if row["payload"].get("native_session") and native_session != row["payload"]["native_session"]:
            raise Conflict("native submission acknowledgement belongs to another session")
        _owned_pane(row)
    if event == "request" and row["status"] == "continuation_required" and row["payload"].get("continuation_input"):
        prompt = observation.get("prompt", observation.get("text"))
        continuation = row["payload"]["continuation_input"]
        if (continuation["phase"] not in {"submitting", "unconfirmed", "uncertain"}
                or not isinstance(prompt, str) or digest(prompt) != continuation["submission_hash"]):
            raise Conflict("continuation requires acknowledgment of its complete frozen native input")
        with ledger.transaction() as tx:
            unit_execution._run(tx, run_id)
            payload = {**row["payload"], "submission_hash": digest(prompt),
                       "continuation_input": {**continuation, "phase": "acknowledged"},
                       "instruction_delivery": "complete_continuation_input_acknowledged"}
            _transition(tx, run_id, {"continuation_required"}, "submitted", payload=payload)
        return None
    if event == "request" and row["status"] in {"submitting", "submission_unconfirmed", "submission_uncertain", "submitted"}:
        prompt = observation.get("prompt", observation.get("text"))
        if not isinstance(prompt, str) or digest(prompt) != row["payload"].get("submission_hash"):
            if row["status"] != "submitted":
                raise Conflict("native input does not match the complete frozen checkpoint submission")
            return None
        with ledger.transaction() as tx:
            unit_execution._run(tx, run_id)
            _transition(tx, run_id, {"submitting", "submission_unconfirmed", "submission_uncertain"}, "submitted",
                        payload={**row["payload"], "instruction_delivery": "complete_native_input_acknowledged"})
    return None


def guard_release(tx, run_id):
    row = native_launch._row(tx, run_id)
    if row and row["status"] not in {"prepared", "preparation_failed", "installation_failed", "quiesced"}:
        raise Conflict("native session ownership must be reconciled before releasing assignment leases")
    if row:
        from .native_tools import pending
        if pending(tx, row["launch_id"]):
            raise Conflict("native tools remain in flight; reconcile calls before releasing assignment leases")
        if tx.db.execute("SELECT 1 FROM native_children WHERE launch_id=? AND status='active' LIMIT 1", (row["launch_id"],)).fetchone():
            raise Conflict("native children remain active; reconcile ownership before releasing assignment leases")
        if row['status'] == 'quiesced':
            from .native_producer import require_drained
            require_drained(tx, run_id)
            if tx.db.execute('SELECT 1 FROM cursors WHERE run_id=? AND head_seq<>classified_seq LIMIT 1', (run_id,)).fetchone() or tx.db.execute("SELECT 1 FROM events WHERE run_id=? AND disposition='pending' LIMIT 1", (run_id,)).fetchone():
                raise Conflict('late native evidence must be classified before releasing assignment leases')


def refresh_before_submission(ledger, row):
    """Freeze the full current native input before its first submission."""
    payload = row['payload']
    _, rendered = prompt_compiler.verified_bundle(ledger.root, payload['worker_id'],
        Path(payload['manifest']), workdir=payload['workdir'])
    with ledger.snapshot() as tx:
        native_launch.verify_inputs(tx, row['run_id'], states={'ready'}, check_evidence=False)
        cursors, pending = context_packets._pending(tx, row['run_id'])
        sources = context_packets._sources(tx, row['run_id'])
    overhead = startup_overhead(payload['capsule'], row['run_id'], row['launch_id'], rendered['system'])
    budget = payload['initial_input_limit'] - overhead
    packet = context_packets.issue(ledger, row['run_id'], request_id='startup-refresh-' + digest([row['launch_id'], cursors, pending, sources]),
        mode='forced', instructions=rendered['context'], max_tokens=budget, optional_tokens=min(1000, budget))
    with ledger.transaction() as tx:
        current = native_launch._row(tx, row['run_id'])
        if current['status'] != 'ready' or current['payload'] != payload:
            return
        atomic_write_text(Path(payload['packet_path']), packet['text'])
        updated = {**payload, 'checkpoint_id': packet['checkpoint_id'], 'packet_hash': packet['packet_hash'],
                   'required_files': required_files(payload, packet),
                   'charged_initial_tokens': packet['charged_tokens'] + overhead}
        _transition(tx, row['run_id'], {'ready'}, 'ready', payload=updated)


def submit_continuation(ledger, row, *, env=None):
    """Submit a frozen complete checkpoint once after an observed native reset."""
    continuation = row["payload"].get("continuation_input")
    if row["status"] != "continuation_required" or not continuation or continuation["phase"] != "prepared":
        return None
    from . import native_restart
    if native_restart.request_instruction_refresh(ledger, row):
        return {"status": "instruction_restart_queued", "run_id": row["run_id"]}
    from .native_tools import pending
    if pending(ledger, row["launch_id"]):
        return None
    pane = _owned_pane(row)
    child = {**(os.environ if env is None else env), "HX_TMUX": " ".join(row["payload"]["tmux"])}
    visible = goal.capture_pane(row["payload"]["session"], child)
    if not visible or not goal.pane_is_idle(visible):
        return None
    # Compile before the writer; the transaction confirms the row it was compiled for.
    _, rendered = prompt_compiler.verified_bundle(ledger.root, row["payload"]["worker_id"],
        Path(row["payload"]["manifest"]), workdir=row["payload"]["workdir"])
    with ledger.transaction() as tx:
        current = native_launch._row(tx, row["run_id"])
        if current["payload"] != row["payload"] or current["status"] != "continuation_required":
            return None
        packet = context_packets._read(tx, row["payload"]["checkpoint_id"], row["run_id"])
        if native_launch._bytes(Path(row["payload"]["packet_path"]), 262144) != packet["text"].encode():
            raise Conflict("continuation native input changed before submission")
        system_path = Path(row["payload"]["capsule"]) / "run" / row["payload"]["worker_id"] / "persona.md"
        if native_launch._bytes(system_path, 262144) != rendered["system"].encode():
            raise Conflict("continuation native system instructions changed")
        prompt = _submission_prompt(row)
        if len(rendered["system"].encode()) + len(prompt.encode()) > row["payload"]["initial_input_limit"]:
            raise ValidationError("complete continuation input exceeds its configured model context budget")
        payload = {**row["payload"], "continuation_input": {**continuation, "phase": "submitting", "submission_hash": digest(prompt)}}
        _transition(tx, row["run_id"], {"continuation_required"}, "continuation_required", payload=payload)
    try:
        _owned_pane(native_launch._row(ledger, row["run_id"]))
        goal.paste(row["payload"]["session"], prompt, child)
        phase = "unconfirmed"
    except Exception:
        phase = "uncertain"
        raise
    finally:
        with ledger.transaction() as tx:
            current = native_launch._row(tx, row["run_id"])
            if current["status"] == "continuation_required" and current["payload"]["continuation_input"]["phase"] == "submitting":
                payload = {**current["payload"], "continuation_input": {**current["payload"]["continuation_input"], "phase": phase}}
                _transition(tx, row["run_id"], {"continuation_required"}, "continuation_required", payload=payload)
    return {"status": "continuation_" + phase, "run_id": row["run_id"]}
