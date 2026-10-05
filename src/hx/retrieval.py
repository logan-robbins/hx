"""Focused assignment briefs from indexed map records and a bounded neighborhood."""

from __future__ import annotations

import re
import time

from . import appmap
from .continuity_store import Conflict, canonical, digest
from .errors import ValidationError
from .facts import RequiredContextOverflow

STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "the", "to", "with"}

STOP |= {"add", "change", "check", "fix", "implement", "improve", "keep", "preserve", "support", "test", "update", "verify", "current", "requested", "should", "must"}


def plan_context(store, repository, snapshot, goal, *, required=(), max_bytes=16000):
    from .planning import write_scope
    if not isinstance(goal, str) or not goal.strip() or len(goal.encode()) > 16000:
        raise ValidationError("planning requires a nonempty goal of at most 16 KiB")
    if type(max_bytes) is not int or not 2000 <= max_bytes <= 64000 or len(required) > 16:
        raise ValidationError("planning brief needs a 2–64 KiB budget and at most 16 required IDs")
    info, _ = appmap._snapshot(store, repository, snapshot)
    key = (info["repo_id"], snapshot)
    started = time.monotonic()
    terms = list(dict.fromkeys(token.rstrip(".") for token in re.findall(r"[\w./-]+", goal.lower()) if token.rstrip(".") and token.rstrip(".") not in STOP))[:32]
    seeds = []
    for token in terms:
        exact = store.db.execute("SELECT record_id FROM map_heads WHERE repository=? AND snapshot=? AND record_id=?", (*key, token)).fetchone()
        if exact:
            seeds.append(exact[0])
        for row in store.db.execute("SELECT record_id FROM map_sources WHERE repository=? AND snapshot=? AND path=? ORDER BY record_id LIMIT 3", (*key, token)):
            seeds.append(row[0])
    if terms:
        # Learned terms only nominate candidates. Normal applicability and graph
        # validation below still decide what may enter the brief.
        slots = ','.join('?' for _ in terms)
        for row in store.db.execute(f"""SELECT a.record_id FROM map_aliases a
            JOIN map_heads h USING(repository,snapshot,record_id,version)
            WHERE a.repository=? AND a.snapshot=? AND a.term IN ({slots})
            GROUP BY a.record_id ORDER BY count(*) DESC,a.record_id LIMIT 3""", (*key, *terms)):
            seeds.append(row[0])
        # Exact qualified symbols can be more useful than a prose match.
        for term in terms[:8]:
            for row in store.db.execute("SELECT record_id FROM map_sources WHERE repository=? AND snapshot=? AND lower(symbol)=? ORDER BY record_id LIMIT 3", (*key, term)):
                seeds.append(row[0])
        query = " OR ".join('"' + token.replace('"', '""') + '"' for token in terms)
        for row in store.db.execute("""SELECT k.record_id FROM map_search
            JOIN map_search_keys k ON k.rowid=map_search.rowid
            WHERE map_search MATCH ? AND k.repository=? AND k.snapshot=?
            ORDER BY bm25(map_search,3.0,1.0),k.record_id LIMIT 3""", (query, *key)):
            seeds.append(row[0])
    seeds = list(dict.fromkeys(seeds))[:3]
    brief = {"schema_version": 1, "repo_id": key[0], "snapshot": snapshot, "goal": goal,
             "nodes": [], "relations": [], "owners": [], "discovery": [],
             "coverage": {"seed_ids": seeds, "considered": 0, "available_edges": 0, "omitted_edges": 0,
                          "strategy": "exact/lexical seeds, adjacent boundaries, and their interface consumers", "stop": "exhausted_neighborhood"}}
    selected, unavailable, visited = {}, set(), set()
    # Leave space for coverage and discovery so they are never silently cut off.
    content_limit = max_bytes - 1400
    def add(record_id, mandatory=False):
        if record_id in visited:
            return record_id in selected
        visited.add(record_id)
        brief["coverage"]["considered"] += 1
        try:
            found = appmap.get_record(store, repository, snapshot, record_id)
        except ValidationError:
            found = None
        if found is None or found["applicability"] != "current" or found["record"]["claim"] == "hypothesis":
            unavailable.add(record_id)
            if mandatory:
                raise Conflict(f"required planning record is absent, stale, pending, or hypothetical: {record_id}")
            return False
        record = found["record"]
        node = {field: record[field] for field in ("id", "version", "kind", "claim", "summary", "data", "anchors", "attributes")}
        if len(canonical({**brief, "nodes": [*brief["nodes"], node]}).encode()) > content_limit:
            if mandatory:
                raise RequiredContextOverflow("required assignment context exceeds the brief budget; split the goal")
            brief["coverage"]["stop"] = "budget"
            return False
        brief["nodes"].append(node)
        selected[record_id] = record["version"]
        return True
    for record_id in dict.fromkeys(required):
        appmap.identifier(record_id)
        add(record_id, True)
    for record_id in seeds:
        add(record_id)
    roots = list(dict.fromkeys([*required, *seeds]))
    # Interleave relation kinds and directions; common containment cannot hide all
    # consumers or checks. Optional expansion has an explicit small work limit.
    considered_edges, available = 0, 0
    edge_ids = set()
    def expand(boundaries):
        nonlocal considered_edges
        groups = []
        for relation in sorted(appmap.RELATIONS):
            for direction in ("source", "target"):
                for root in boundaries:
                    rows = store.db.execute(f"SELECT source,kind,target,status FROM map_relations WHERE repository=? AND snapshot=? AND {direction}=? AND kind=? ORDER BY source,target LIMIT 4", (*key, root, relation)).fetchall()
                    if rows:
                        groups.append(rows)
        for position in range(4):
            for group in groups:
                if position >= len(group):
                    continue
                if time.monotonic() - started >= 1.5 or len(visited) >= 24 or considered_edges >= 32:
                    brief["coverage"]["stop"] = "budget"
                    return
                edge = dict(group[position])
                identity = (edge["source"], edge["kind"], edge["target"])
                if identity in edge_ids:
                    continue
                edge_ids.add(identity)
                considered_edges += 1
                if edge["status"] != "validated":
                    unavailable.add(edge["target"])
                    continue
                if add(edge["source"]) and add(edge["target"]):
                    if len(canonical({**brief, "relations": [*brief["relations"], edge]}).encode()) <= content_limit:
                        brief["relations"].append(edge)
    expand(roots)
    interfaces = [node["id"] for node in brief["nodes"] if node["kind"] == "interface" and node["id"] not in roots]
    # Shared contracts are a planning boundary: identify their consumers without
    # recursively exploring the consumers' implementations or the whole graph.
    expand(interfaces)
    boundaries = [*roots, *interfaces]
    if boundaries:
        slots = ",".join("?" for _ in boundaries)
        available = store.db.execute(f"SELECT count(*) FROM map_relations WHERE repository=? AND snapshot=? AND (source IN ({slots}) OR target IN ({slots}))", (*key, *boundaries, *boundaries)).fetchone()[0]
    owner_ids = set()
    for node in brief["nodes"]:
        for anchor in node["anchors"]:
            path = write_scope(repository.resolve(), anchor["path"])
            for row in store.db.execute("""SELECT l.canonical_path,r.task_id,r.worker_id FROM leases l JOIN runs r USING(run_id)
                WHERE l.repository=? AND r.ended_at IS NULL AND (l.canonical_path=? OR substr(?,1,length(l.canonical_path)+1)=l.canonical_path||'/' OR substr(l.canonical_path,1,length(?)+1)=?||'/') LIMIT 16""", (key[0], path, path, path, path)):
                owner = dict(row)
                owner_key = tuple(row)
                if owner_key not in owner_ids:
                    if len(canonical({**brief, "owners": [*brief["owners"], owner]}).encode()) > content_limit:
                        brief["coverage"]["stop"] = "budget"
                        break
                    brief["owners"].append(owner)
                    owner_ids.add(owner_key)
    if not selected:
        brief["discovery"].append("Locate the goal's responsibility and add source-backed map records before decomposing implementation work.")
    if not any(node["kind"] == "check" for node in brief["nodes"]):
        brief["discovery"].append("Identify a check that asserts the requested behavior; no current declared check was selected.")
    if unavailable:
        brief["discovery"].append({"verify_records_or_relationships": sorted(unavailable)[:16]})
    brief["coverage"].update(available_edges=available, inspected_edges=considered_edges,
                            omitted_edges=max(0, available - len(brief["relations"])))
    if available > considered_edges:
        brief["discovery"].append("Unvisited relationships remain; expand the named boundary before declaring its dependencies complete.")
    elif available > len(brief["relations"]):
        brief["discovery"].append("Some relationships lack current evidence or do not fit; verify the omitted boundary before assigning dependent work.")
    # A brief pins its evidence; applying a plan revalidates these exact versions.
    for record_id, version in selected.items():
        current = store.db.execute("SELECT version FROM map_heads WHERE repository=? AND snapshot=? AND record_id=?", (*key, record_id)).fetchone()
        if current is None or current[0] != version:
            raise Conflict("map changed during assignment brief construction")
    brief["input_hash"] = digest({"snapshot": snapshot, "goal": goal, "versions": selected, "relations": brief["relations"]})
    if len(canonical(brief).encode()) > max_bytes:
        raise RequiredContextOverflow("assignment goal and required metadata exceed the brief budget")
    return brief
