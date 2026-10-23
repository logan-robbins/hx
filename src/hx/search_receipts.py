"""Receipts of the searches a run made, and the packet areas a binding must have searched.

A worker searches before it builds, and the runtime receipts every search its run makes: a
native Grep or Glob admitted through the hooks, whether executed or served from the box-wide
search cache, and every `hx code` query the worker runs from its shell. A receipt is the
search's kind, its query, the repository it answered for and the repository-relative scope it
covered: the path a native search was pointed at, each focus of a focused graph map, or `.`
for a graph query, which answers over the whole indexed repository.

A packet's search areas are where a prior implementation of its behavior would live: the
directory of every declared write path (a reserved prefix is its own area) and the directory
of every source anchor of its map inputs; a packet that declares neither has the whole
repository as its one area. The binding cites receipts of its run, each covering at least
one area and together covering every area. `hx plan bind` refuses anything else, and known
mutating native tools refuse an unapproved binding, so the first edit cannot precede a search
of the areas the packet builds in. `hx task` lists a run's areas and receipts.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import time

from .continuity_store import digest
from .errors import ValidationError

KINDS = ("native", "graph")
#: `hx code` subcommands that search the graph; indexing, auditing, registering and the
#: journal are not searches.
GRAPH_QUERIES = {"search", "symbols", "type", "signature", "callers", "tests-for", "conform", "precedent",
                 "contract", "contracts", "map", "submaps", "edges", "impact"}
QUERY_BYTES = 256
CITATIONS = 32


def receipt_id(kind, query, scope):
    return digest([kind, query, scope])[:12]


def record(tx, run_id, kind, query, scope, repository):
    """Receipt one search of `run_id`; an identical search keeps its receipt."""
    if kind not in KINDS:
        raise ValidationError(f"search receipt kind is one of {', '.join(KINDS)}")
    query = query.encode()[:QUERY_BYTES].decode(errors="ignore")
    identity = receipt_id(kind, query, scope)
    tx.db.execute("INSERT OR IGNORE INTO search_receipts VALUES(?,?,?,?,?,?,?)",
                  (run_id, identity, kind, query, scope, repository, time.time()))
    return identity


def listing(db, run_id):
    """A run's receipts, oldest first."""
    return [{"id": row[0], "kind": row[1], "query": row[2], "scope": row[3], "repository": row[4]}
            for row in db.execute("SELECT receipt_id,kind,query,scope,repository FROM search_receipts WHERE run_id=? ORDER BY stamp,receipt_id",
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
    """Validate a binding's `searches`: 1–32 unique receipt ids of this run, each covering an
    area of the packet, together covering every area."""
    if (not isinstance(cited, list) or not 1 <= len(cited) <= CITATIONS or len(set(cited)) != len(cited)
            or any(not isinstance(item, str) for item in cited)):
        raise ValidationError(f"binding searches cite 1–{CITATIONS} unique search receipt ids of this run; hx task lists them")
    required = areas(tx.db, payload)
    receipts = {row["receipt_id"]: row for row in tx.db.execute("SELECT * FROM search_receipts WHERE run_id=?", (run_id,))}
    covered = set()
    for identity in cited:
        receipt = receipts.get(identity)
        if receipt is None:
            raise ValidationError(f"search receipt {identity} is not a search of this run")
        if receipt["repository"] != payload["repository"]:
            raise ValidationError(f"search receipt {identity} answered for another repository")
        hits = {area for area in required if covers(receipt["scope"], area)}
        if not hits:
            raise ValidationError(f"search receipt {identity} ({receipt['query']}, scope {receipt['scope']}) "
                                  f"covers none of the packet areas: {', '.join(required)}")
        covered |= hits
    missing = [area for area in required if area not in covered]
    if missing:
        raise ValidationError("binding searches leave packet areas unsearched: " + ", ".join(missing))


def graph_queries(argv):
    """`(query, scope)` for one `hx code` argv that searched the graph, one per focus of a
    focused map; nothing for a command that is not a search."""
    from .code_index import _parser
    try:
        args = _parser().parse_args(argv)
    except SystemExit:
        return []
    if args.command not in GRAPH_QUERIES:
        return []
    shown = [value for index, value in enumerate(argv)
             if value != "--root" and not value.startswith("--root=") and (index == 0 or argv[index - 1] != "--root")]
    query = "hx code " + " ".join(shown)
    scopes = [PurePosixPath(focus).as_posix().strip("/") or "." for focus in getattr(args, "focus", None) or []] or ["."]
    return [(query, scope) for scope in scopes]


def receipt_graph_query(root, argv, status, env):
    """Receipt a finished `hx code` search to the calling worker's active run; the Partner,
    the operator and a worker without a run leave none. An `--expect` miss (exit 3) is
    still a search with an answer."""
    from .caller import caller
    from .ids import PARTNER
    who = caller(env)
    if status not in {0, 3} or who is None or who == PARTNER:
        return
    queries = graph_queries(argv)
    if not queries:
        return
    from . import fingerprints
    from .code_snapshot import caller_checkout
    repo = next((argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--repo"), None)
    checkout = caller_checkout(repo)
    found = fingerprints.checkout(checkout) if checkout else None
    if found is None:
        return
    from .continuity_store import ContinuityStore
    with ContinuityStore(root) as store:
        run_id = active_run(store.db, who)
        if run_id is None:
            return
        with store.transaction() as tx:
            for query, scope in queries:
                record(tx, run_id, "graph", query, scope, found["namespace"])
