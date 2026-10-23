"""Receipts of the searches a run made, and the packet areas a binding must have mapped.

A worker searches before it builds, and the runtime receipts every search its run makes: a
native Grep or Glob admitted through the hooks, whether executed or served from the box-wide
search cache, and every `hx code` query the worker runs from its shell. A receipt is the
search's kind, its query, the repository it answered for, the repository-relative scope it
covered and whether it was complete over that scope.

Three kinds. An `area` receipt is a focused graph map, `hx code map --focus AREA`, which
names every declaring file under its focus with its declarations; it is complete when the
budget omitted none. A `graph` receipt is any other graph query: a lookup or traversal by
name or path, complete when its limit did not cap it. A `native` receipt is a Grep or Glob,
complete only when the native result said so.

A packet's search areas are where a prior implementation of its behavior would live: the
directory of every declared write path (a reserved prefix is its own area) and the directory
of every source anchor of its map inputs; a packet that declares neither has the whole
repository as its one area. Only a complete area map covers an area, at the area or above:
a name lookup finds what the worker already knows the name of and a grep is lexical, so
neither establishes what exists there. The binding cites receipts of its run that together
map every area; `hx plan bind` refuses anything else, and known mutating native tools refuse
an unapproved binding, so the first edit cannot precede an enumeration of the areas the
packet builds in. Traversal from what the maps show (`callers`, `contract`, `precedent`,
`tests-for`) is cited beside them for the reviewer. `hx task` lists a run's areas and receipts.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import time

from .continuity_store import digest
from .errors import ValidationError

KINDS = ("area", "graph", "native")
#: `hx code` subcommands that search the graph; indexing, auditing, registering and the
#: journal are not searches.
GRAPH_QUERIES = {"search", "symbols", "type", "signature", "callers", "tests-for", "conform", "precedent",
                 "contract", "contracts", "map", "submaps", "edges", "impact"}
QUERY_BYTES = 256
CITATIONS = 32


def receipt_id(kind, query, scope, repository):
    return digest([kind, query, scope, repository])[:12]


def record(tx, run_id, kind, query, scope, repository, complete):
    """Receipt one search of `run_id`; a repeated search keeps its receipt, complete once any
    repetition answered completely."""
    if kind not in KINDS:
        raise ValidationError(f"search receipt kind is one of {', '.join(KINDS)}")
    query = query.encode()[:QUERY_BYTES].decode(errors="ignore")
    identity = receipt_id(kind, query, scope, repository)
    tx.db.execute("""INSERT INTO search_receipts VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,receipt_id)
        DO UPDATE SET complete=max(search_receipts.complete,excluded.complete)""",
                  (run_id, identity, kind, query, scope, repository, int(bool(complete)), time.time()))
    return identity


def listing(db, run_id):
    """A run's receipts, oldest first."""
    return [{"id": row[0], "kind": row[1], "query": row[2], "scope": row[3], "repository": row[4], "complete": bool(row[5])}
            for row in db.execute("SELECT receipt_id,kind,query,scope,repository,complete FROM search_receipts WHERE run_id=? ORDER BY stamp,receipt_id",
                                  (run_id,))]


def active_run(db, worker_id):
    """The worker's active run, or None."""
    row = db.execute("SELECT run_id FROM runs WHERE worker_id=? AND ended_at IS NULL", (worker_id,)).fetchone()
    return row[0] if row else None


def covers(scope, area):
    return scope == "." or area == scope or area.startswith(scope + "/")


def areas(db, payload):
    """Where a prior implementation of the packet's behavior would live, as sorted
    repository-relative directories; `.` when the packet declares no location."""
    workdir = Path(payload["workdir"])
    found = set()
    for path in payload["write_paths"]:
        found.add(path if (workdir / path).is_dir() else PurePosixPath(path).parent.as_posix())
    for ref in payload["map_inputs"]:
        row = db.execute("SELECT payload FROM map_records WHERE repository=? AND snapshot=? AND record_id=? AND version=?",
                         (ref["repository"], ref["snapshot"], ref["id"], ref["version"])).fetchone()
        if row is None:
            raise ValidationError(f"map input {ref['id']}@{ref['version']} is not in the ledger")
        for anchor in json.loads(row[0])["anchors"]:
            found.add(PurePosixPath(anchor["path"]).parent.as_posix())
    return sorted(found) or ["."]


def cite(tx, run_id, payload, cited):
    """Validate a binding's `searches`: 1–32 unique receipt ids of this run for the packet's
    repository, whose complete area maps together cover every packet area."""
    if (not isinstance(cited, list) or not 1 <= len(cited) <= CITATIONS or len(set(cited)) != len(cited)
            or any(not isinstance(item, str) for item in cited)):
        raise ValidationError(f"binding searches cite 1–{CITATIONS} unique search receipt ids of this run; hx task lists them")
    required = areas(tx.db, payload)
    receipts = {row["receipt_id"]: row for row in tx.db.execute("SELECT * FROM search_receipts WHERE run_id=?", (run_id,))}
    maps = []
    for identity in cited:
        receipt = receipts.get(identity)
        if receipt is None:
            raise ValidationError(f"search receipt {identity} is not a search of this run")
        if receipt["repository"] != payload["repository"]:
            raise ValidationError(f"search receipt {identity} answered for another repository")
        if receipt["kind"] == "area" and receipt["complete"]:
            maps.append(receipt["scope"])
    missing = [area for area in required if not any(covers(scope, area) for scope in maps)]
    if missing:
        raise ValidationError("binding searches leave packet areas without a complete area map: " + ", ".join(missing)
                              + "; cite a complete `hx code map --focus AREA --budget BYTES` of each")


def graph_queries(argv):
    """The parsed `hx code` arguments and `(kind, query, scope)` for each search they make:
    one `area` per focus of a focused map, else one `graph` over the repository; nothing for
    a command that is not a search."""
    from .code_index import _parser
    try:
        args = _parser().parse_args(argv)
    except SystemExit:
        return None, []
    if args.command not in GRAPH_QUERIES:
        return args, []
    shown = [value for index, value in enumerate(argv)
             if value != "--root" and not value.startswith("--root=") and (index == 0 or argv[index - 1] != "--root")]
    query = "hx code " + " ".join(shown)
    focus = getattr(args, "focus", None) or []
    if args.command == "map" and focus:
        return args, [("area", query, PurePosixPath(item).as_posix().strip("/") or ".") for item in focus]
    return args, [("graph", query, ".")]


def receipt_graph_query(root, argv, status, env, result):
    """Receipt a finished `hx code` search to the calling worker's active run; the Partner,
    the operator and a worker without a run leave none. An `--expect` miss (exit 3) is
    still a search with an answer."""
    from .caller import caller
    from .ids import PARTNER
    who = caller(env)
    if status not in {0, 3} or who is None or who == PARTNER:
        return
    args, queries = graph_queries(argv)
    if not queries:
        return
    from . import fingerprints
    from .code_metadata import rows_of
    from .code_snapshot import caller_checkout
    checkout = caller_checkout(getattr(args, "repo", None))
    found = fingerprints.checkout(checkout) if checkout else None
    if found is None:
        return
    # An area map says whether its budget omitted anything; a bounded query is complete
    # when its limit did not cap it.
    if queries[0][0] == "area":
        complete = isinstance(result, dict) and result.get("complete") is True
    else:
        complete = len(rows_of(result)) < getattr(args, "limit", 0)
    from .continuity_store import ContinuityStore
    with ContinuityStore(root) as store:
        run_id = active_run(store.db, who)
        if run_id is None:
            return
        with store.transaction() as tx:
            for kind, query, scope in queries:
                record(tx, run_id, kind, query, scope, found["namespace"], complete)
