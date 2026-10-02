"""Replayable termination of the process instances observed under a launch.

Each call performs one bounded step. Durable identities precede every signal;
retries never select a replacement by PID or by a reused tmux session name.
Termination retains leases until background/source coverage is reconciled.
"""

import signal
import time

from . import native_controller, native_launch, native_processes
from .caller import require_partner_caller
from .continuity_store import Conflict, canonical, digest

SIGNAL_QUANTUM = 16


def _save(ledger, row, state, status):
    payload = {**row["payload"], "shutdown": state}
    with ledger.transaction() as tx:
        tx._change()
        changed = tx.db.execute("""UPDATE native_launches SET payload=?,status=?,error=NULL
            WHERE run_id=? AND payload=? AND status=?""",
            (canonical(payload), status, row["run_id"], canonical(row["payload"]), row["status"])).rowcount
        if changed:
            tx.enqueue("native_shutdown", "shutdown:" + row["launch_id"] + ":" + digest(state),
                       {"run_id": row["run_id"], "phase": state["phase"], "processes": len(state["processes"])})


def advance(ledger, worker, run_id, request_id, *, env=None):
    """Freeze, discover, then kill observed descendants; keep uncertain ownership."""
    require_partner_caller("launch shutdown", env)
    row = native_launch._row(ledger, run_id)
    if not row or row["request_id"] != request_id or row["payload"]["worker_id"] != worker:
        raise Conflict("native shutdown must name its original worker and launch request")
    if "shutdown" not in row["payload"]:
        identity = row["payload"].get("pane", {}).get("process")
        if not identity:
            raise Conflict("native shutdown lacks a process-instance receipt; do not adopt a PID or replacement pane")
        if row["payload"].get("supervisor") != "shell-wait-v1":
            raise Conflict("native shutdown requires its original controlled waiting supervisor")
        native_controller.drain(ledger, worker, run_id, request_id, env=env)
        row = native_launch._row(ledger, run_id)
        if "shutdown" in row["payload"]:
            return native_controller._result(ledger, run_id)
        _save(ledger, row, {"phase": "freezing", "processes": [identity],
            "supervisor": identity, "scope": "observed_descendants", "coverage_verified": False}, "stopping")
        return native_controller._result(ledger, run_id)
    state = row["payload"]["shutdown"]
    if state["phase"] == "stopped":
        return native_controller._result(ledger, run_id)
    processes = state["processes"]
    if len(processes) > native_processes.MAX_PROCESSES:
        raise Conflict("native shutdown process bound exceeded; retain ownership for reconciliation")
    live = [(identity, native_processes.current(identity)) for identity in processes]
    live = [(identity, actual) for identity, actual in live if actual is not None]
    if state["phase"] == "freezing":
        moving = [identity for identity, actual in live if not actual["stopped"] and identity != state["supervisor"]]
        if moving:
            # A suspended hook must not hold the authority's writer lock. The
            # identities/stop intent were committed in an earlier step; hold
            # writer exclusion only around this bounded kernel-signal batch.
            with ledger.transaction():
                frozen = moving[:SIGNAL_QUANTUM]
                for identity in frozen:
                    native_processes.send(identity, signal.SIGSTOP)
                deadline = time.monotonic() + 0.25
                while frozen:
                    frozen = [identity for identity in frozen if (actual := native_processes.current(identity)) and not actual["stopped"]]
                    if not frozen:
                        break
                    if time.monotonic() >= deadline:
                        raise Conflict("native stop signal remains pending; retain ownership and retry")
                    time.sleep(0.005)
        else:
            known = {digest(identity) for identity in processes}
            discovered = {digest(identity): identity for identity in native_processes.descendants(
                [identity for identity, _ in live], supervisor=state["supervisor"])}
            additions = [identity for key, identity in discovered.items() if key not in known]
            if len(processes) + len(additions) > native_processes.MAX_PROCESSES:
                raise Conflict("native shutdown process bound exceeded; retain ownership for reconciliation")
            if additions:
                _save(ledger, row, {**state, "processes": processes + additions}, "stopping")
            else:
                _save(ledger, row, {**state, "phase": "killing"}, "stopping")
    elif state["phase"] == "killing":
        if live:
            # Leave the waiting shell until last; it normally exits when its
            # native child does, and never launches a replacement.
            live.sort(key=lambda item: item[0] == state["supervisor"])
            for identity, _ in live[:SIGNAL_QUANTUM]:
                native_processes.send(identity, signal.SIGKILL)
        else:
            # This receipt proves only that the observed instances cannot run.
            # Detached/remote work and capture gaps remain separate obligations.
            _save(ledger, row, {**state, "phase": "stopped"}, "stopped_unreconciled")
    else:
        raise Conflict("unknown native shutdown phase; retain assignment ownership")
    return native_controller._result(ledger, run_id)
