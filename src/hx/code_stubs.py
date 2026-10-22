"""Published contract declarations answer in the graph before the stub is merged.

`hx plan publish-contract` parses the exact published files with the indexer's own
extractors and registers their declarations under the repository namespace with lane
`contract`; the ledger row (plan, task, revision, output, version, commit) is their
provenance. Every live read of the namespace, from any checkout, answers them until
that checkout's own snapshot extracts a real declaration of the same name at the same
path. Publishing a newer version of the output, or applying a newer revision of its
producer, removes them. Historical (`--at`) reads answer immutable snapshots only.

Rows are keyed by the publishing ledger (the hx root), so one shared repository
service holds every instance's stubs without task identities colliding.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import code_store
from .errors import ValidationError

LANE = "contract"
PROOF = "immutable-ledger-publication"


def extract(blobs):
    """Declarations of the published files, `{path: bytes}`, as the indexer parses them."""
    from . import code_extract

    found = []
    for path, data in sorted(blobs.items()):
        symbols, _, _ = code_extract.extract(data.decode("utf-8", errors="replace"), path)
        found.extend(dict(symbol, lane=LANE) for symbol in symbols)
    return found


def publication(root, task, proof, symbols):
    """The register operation for one published contract output."""
    return {
        "ledger": str(Path(root).resolve()),
        "register": {
            "repo": task["payload"]["repository"],
            "plan_id": task["payload"]["plan_id"],
            "task_id": proof["producer"],
            "task_revision": proof["task_revision"],
            "output_id": proof["id"],
            "version": proof["version"],
            "commit": proof["commit"],
            "symbols": symbols,
        },
    }


def send(root, operation, env=None):
    """Apply `operation` to the graph that answers this root's reads: the configured
    repository service's, else the root's own index."""
    from .repo_service import configured_socket, request

    socket = configured_socket(root, env)
    if socket is None:
        return apply(root, operation)
    from .continuity_store import canonical, digest
    from .store import atomic_write_text

    # A publication can exceed one service request; the service reads it from disk.
    file = Path(root) / "state" / "contract-stubs" / (digest(operation) + ".json")
    atomic_write_text(file, canonical(operation))
    try:
        return request(socket, {"method": "contract-stubs", "file": str(file)})
    finally:
        file.unlink(missing_ok=True)


def prune(root, revisions, env=None):
    """Remove stubs that `{task_id: revision}` supersedes."""
    if revisions:
        send(root, {"ledger": str(Path(root).resolve()), "prune": revisions}, env)


def _text(value, label):
    if not isinstance(value, str) or not value:
        raise ValidationError(f"contract stub {label} must be a nonempty string")


def _revision(value, label):
    if type(value) is not int or value < 1:
        raise ValidationError(f"contract stub {label} must be a positive integer")


def apply(root, operation):
    if not isinstance(operation, dict) or operation.keys() not in ({"ledger", "register"}, {"ledger", "prune"}):
        raise ValidationError("contract stub operation requires ledger and register or prune")
    _text(operation["ledger"], "ledger")
    ledger = operation["ledger"]
    if "prune" in operation:
        revisions = operation["prune"]
        if not isinstance(revisions, dict):
            raise ValidationError("contract stub prune maps task IDs to revisions")
        for task_id, revision in revisions.items():
            _text(task_id, "task")
            _revision(revision, "revision")
        conn = code_store.connect(root, existing=True)
        if conn is None:
            return {"removed": 0}
        try:
            with conn:
                removed = sum(
                    conn.execute(
                        "DELETE FROM main.contract_stubs WHERE ledger=? AND task_id=? AND task_revision<?",
                        (ledger, task_id, revision),
                    ).rowcount
                    for task_id, revision in sorted(revisions.items())
                )
            return {"removed": removed}
        finally:
            conn.close()
    body = operation["register"]
    fields = {"repo", "plan_id", "task_id", "task_revision", "output_id", "version", "commit", "symbols"}
    if not isinstance(body, dict) or body.keys() != fields:
        raise ValidationError("contract stub registration has missing or unknown fields")
    for key in ("repo", "plan_id", "task_id", "output_id", "commit"):
        _text(body[key], key)
    _revision(body["task_revision"], "task revision")
    _revision(body["version"], "version")
    symbols = body["symbols"]
    keys = {"name", "qualified", "kind", "path", "line", "end_line", "signature"}
    if not isinstance(symbols, list) or any(not isinstance(s, dict) or not keys <= s.keys() for s in symbols):
        raise ValidationError("contract stub symbols are declaration rows")
    conn = code_store.connect(root)
    try:
        with conn:
            # The same version replaces itself; older versions and producer
            # revisions of this output are superseded by it.
            removed = conn.execute(
                "DELETE FROM main.contract_stubs WHERE ledger=? AND task_id=? AND (task_revision<? OR (output_id=? AND version<=?))",
                (ledger, body["task_id"], body["task_revision"], body["output_id"], body["version"]),
            ).rowcount
            for ordinal, symbol in enumerate(symbols):
                conn.execute(
                    "INSERT INTO main.contract_stubs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ledger,
                        body["repo"],
                        body["plan_id"],
                        body["task_id"],
                        body["task_revision"],
                        body["output_id"],
                        body["version"],
                        body["commit"],
                        ordinal,
                        symbol["path"],
                        symbol["name"].casefold(),
                        symbol["qualified"].casefold(),
                        json.dumps(symbol, sort_keys=True),
                    ),
                )
        return {"registered": len(symbols), "removed": removed}
    finally:
        conn.close()


def _view(row):
    symbol = json.loads(row["data"])
    return symbol | dict(
        repo=row["repo"],
        lane=LANE,
        stale=False,
        unanchored=False,
        source_proof=PROOF,
        provenance=dict(
            ledger=row["ledger"],
            plan_id=row["plan_id"],
            task_id=row["task_id"],
            task_revision=row["task_revision"],
            output_id=row["output_id"],
            version=row["version"],
            commit=row["commit_sha"],
        ),
    )


def superseded(conn, stub):
    """Whether the read checkout's own snapshot holds the real declaration."""
    from .code_snapshot import facts_in_path

    qualified, name = stub["qualified"].casefold(), stub["name"].casefold()
    return any(
        fact["lane"] != LANE
        and (
            fact["qualified"].casefold() == qualified
            or (fact["name"].casefold() == name and fact["kind"] == stub["kind"])
        )
        for fact in facts_in_path(conn, "symbols", stub["path"], repo=stub["repo"])
    )


def _rows(conn, name, repo, path):
    if conn.snapshot is None or conn.historical:
        return
    folded = None if name is None else name.casefold()
    for row in conn.execute(
        "SELECT * FROM main.contract_stubs WHERE (? IS NULL OR name_folded=? OR qualified_folded=?) "
        "AND (? IS NULL OR repo=?) AND (? IS NULL OR path=?) "
        "ORDER BY repo,path,ledger,task_id,output_id,version,ordinal",
        (folded, folded, folded, repo, repo, path, path),
    ):
        if row["repo"] in conn.snapshot_maps:
            yield _view(row)


def declarations(conn, name, *, repo=None, path=None, limit=100):
    """Stub declarations named `name` that the read checkout does not yet hold."""
    result = []
    for stub in _rows(conn, name, repo, path):
        if len(result) >= limit:
            break
        if not superseded(conn, stub):
            result.append(stub)
    return result


def publications(conn, name, *, repo=None, limit=100):
    """Every current stub declaration named `name`, superseded or not: the contract
    stays published after its real declaration is indexed."""
    result = []
    for stub in _rows(conn, name, repo, None):
        if len(result) >= limit:
            break
        result.append(stub | dict(superseded=superseded(conn, stub)))
    return result
