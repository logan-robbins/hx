"""Immutable check execution evidence, independent of run-bound receipts.

The claim table serializes equal in-flight identities. An interrupted claim is
unresolved until explicit reconciliation proves its owner and group have exited.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from .continuity_store import Conflict, canonical, digest


def initialize(tx):
    # Separate initialization keeps this feature compatible with older ledgers.
    db = getattr(tx, "db", tx)
    db.execute("""CREATE TABLE IF NOT EXISTS check_artifacts (
        execution_id TEXT PRIMARY KEY, identity_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('running','finished')),
        artifact_hash TEXT REFERENCES artifacts(hash), payload TEXT NOT NULL,
        payload_hash TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS check_artifact_claims (
        identity_hash TEXT PRIMARY KEY,
        execution_id TEXT NOT NULL REFERENCES check_artifacts(execution_id))""")


def identity(version, source, environment):
    return digest({"schema_version": 1, "recipe": version,
                   "source": source["hash"], "environment": environment["hash"]})


def lookup(store, identity_hash, *, allow_reuse=True):
    row = store.db.execute("""SELECT a.* FROM check_artifact_claims c
        JOIN check_artifacts a ON a.execution_id=c.execution_id WHERE c.identity_hash=?""",
        (identity_hash,)).fetchone()
    if row is None:
        return None
    if row["status"] == "running":
        raise Conflict("identical check has an unresolved execution; reconcile its process ownership before retrying")
    if not allow_reuse:
        return None
    try:
        payload = json.loads(row["payload"])
    except ValueError:
        return None
    if not isinstance(payload, dict) or digest(payload) != row["payload_hash"] or not payload.get("reusable"):
        return None
    if row["identity_hash"] != identity_hash or payload.get("result", {}).get("artifact_hash") != row["artifact_hash"]:
        return None
    receipt = store.db.execute("SELECT valid FROM receipts WHERE receipt_id=?",
                               (row["execution_id"],)).fetchone()
    if receipt is None or not receipt[0]:
        return None
    return {"execution_id": row["execution_id"], "identity_hash": row["identity_hash"],
            "artifact_hash": row["artifact_hash"], "payload_hash": row["payload_hash"],
            "payload": payload}


def reserve(tx, execution_id, identity_hash, *, claim=True):
    from . import native_processes
    owner = native_processes.probe(os.getpid())
    if owner is None:
        raise Conflict("check execution owner identity is unavailable")
    metadata = {"schema_version": 1, "execution_id": execution_id, "identity_hash": identity_hash,
                "owner": owner["identity"], "child": None}
    tx.db.execute("INSERT INTO check_artifacts VALUES(?,?,'running',NULL,?,?)",
                  (execution_id, identity_hash, canonical(metadata), digest(metadata)))
    if claim:
        tx.db.execute("""INSERT INTO check_artifact_claims VALUES(?,?)
            ON CONFLICT(identity_hash) DO UPDATE SET execution_id=excluded.execution_id""",
            (identity_hash, execution_id))


def group_present(group):
    """Probe one process group; inaccessible signals require kernel census proof."""
    try:
        os.killpg(group,0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Darwin can report EPERM during a group teardown. Do not translate
        # permission refusal to absence: inspect bounded kernel group metadata.
        with tempfile.TemporaryFile() as output:
            result=subprocess.run(['/bin/ps','-axo','pgid='],stdout=output,
                stderr=subprocess.DEVNULL,timeout=10,check=False)
            output.seek(0)
            raw=output.read(262145)
        if result.returncode or len(raw)>262144:
            raise Conflict('process group census failed or exceeded its bound')
        groups=raw.split()
        if any(not value.isdigit() for value in groups):
            raise Conflict('process group census metadata is malformed')
        if not groups or str(os.getpgrp()).encode() not in groups:
            raise Conflict('process group census is incomplete; its live controller group is absent')
        if str(group).encode() in groups:
            raise Conflict('process group ownership is inaccessible; kernel census still contains the group')
        return False
    return True


def owned_child(process):
    """Capture identity or prove this Popen child has exited and its group is gone.

    A native audit-token query can race with exit. Popen wait/poll uses our
    actual child relationship, so a known exit does not require a stale PID
    lookup. Inaccessible live identities remain unresolved.
    """
    from . import native_processes
    try:
        child = native_processes.probe(process.pid)
    except Conflict:
        child = None
    if child is None:
        try:
            code=process.wait(timeout=0.05)
        except subprocess.TimeoutExpired as exc:
            raise Conflict('native child identity is unavailable while its owned process is still live') from exc
        if not group_present(process.pid):
            return {'identity':None,'group':process.pid,'boot':native_processes.boot(),
                    'owned_exit_code':code,'group_absent':True}
        raise Conflict('native child identity is unavailable and its process group is still present')
    return {'identity':child['identity'],'group':process.pid,'boot':native_processes.boot()}


def spawned(store, execution_id, process):
    observed=owned_child(process)
    with store.transaction() as tx:
        row = tx.db.execute("SELECT payload FROM check_artifacts WHERE execution_id=? AND status='running'",
                             (execution_id,)).fetchone()
        if row is None:
            raise Conflict("check execution claim disappeared before spawn capture")
        payload = json.loads(row[0])
        payload["child"] = observed
        tx._change()
        tx.db.execute("UPDATE check_artifacts SET payload=?,payload_hash=? WHERE execution_id=?",
                      (canonical(payload), digest(payload), execution_id))
    return observed


def reconcile(store, execution_id):
    """Release a dead execution claim only after proving local process quiescence."""
    from . import native_processes
    with store.transaction() as tx:
        row = tx.db.execute("SELECT * FROM check_artifacts WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None or row["status"] != "running":
            raise Conflict("check execution is absent or already reconciled")
        payload = json.loads(row["payload"])
        if digest(payload) != row["payload_hash"] or not payload.get("owner"):
            raise Conflict("check execution ownership metadata is missing or corrupt")
        if native_processes.current(payload["owner"]) is not None:
            raise Conflict("check execution owner is still alive")
        child = payload.get("child")
        if child is None:
            raise Conflict("check spawn ownership is unknown; quiescence cannot be established")
        if child["boot"] == native_processes.boot():
            if child["identity"] is not None and native_processes.current(child["identity"]) is not None:
                raise Conflict("check child is still alive")
            if group_present(child['group']):
                raise Conflict("check process group is still present")
        payload.update(reconciled=True, reusable=False)
        tx._change()
        tx.db.execute("DELETE FROM check_artifact_claims WHERE execution_id=?", (execution_id,))
        tx.db.execute("UPDATE check_artifacts SET status='finished',payload=?,payload_hash=? WHERE execution_id=?",
                      (canonical(payload), digest(payload), execution_id))
        return {"execution_id": execution_id, "reconciled": True}


def finish(tx, execution_id, artifact, payload):
    tx.db.execute("""UPDATE check_artifacts SET status='finished',artifact_hash=?,payload=?,payload_hash=?
                      WHERE execution_id=? AND status='running'""",
                  (artifact, canonical(payload), digest(payload), execution_id))
    tx.db.execute("INSERT INTO artifact_refs VALUES(?,?,?,?)",
                  ("check_execution", execution_id, "output", artifact))


def intact(store, provenance):
    """Validate immutable execution provenance without trusting receipt JSON."""
    if not isinstance(provenance, dict) or not {"execution_id", "identity_hash", "artifact_hash", "payload_hash"} <= provenance.keys():
        return False
    row = store.db.execute("SELECT * FROM check_artifacts WHERE execution_id=?",
                           (provenance["execution_id"],)).fetchone()
    if row is None or row["status"] != "finished":
        return False
    try:
        payload = json.loads(row["payload"])
    except ValueError:
        return False
    receipt = store.db.execute("SELECT valid FROM receipts WHERE receipt_id=?", (row["execution_id"],)).fetchone()
    return (receipt is not None and bool(receipt[0]) and isinstance(payload, dict)
            and row["identity_hash"] == provenance["identity_hash"]
            and row["artifact_hash"] == provenance["artifact_hash"]
            and row["payload_hash"] == provenance["payload_hash"]
            and digest(payload) == row["payload_hash"]
            and payload.get("reusable") is True)
