"""Deterministic file PageRank and Louvain proposals; no curated assertions."""

from __future__ import annotations
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from . import code_queries as query, code_store as store


def pagerank(nodes, edges, focus=(), iterations=60):
    nodes = sorted(nodes)
    if not nodes:
        return {}
    wanted = set(focus) & set(nodes)
    teleport = {
        n: (1 / len(wanted) if n in wanted else 0) if wanted else 1 / len(nodes)
        for n in nodes
    }
    outbound = {n: set() for n in nodes}
    for a, b in edges:
        if a in outbound and b in outbound:
            outbound[a].add(b)
    rank = dict(teleport)
    for _ in range(iterations):
        dangling = sum(rank[n] for n in nodes if not outbound[n])
        next_rank = {
            n: 0.15 * teleport[n] + 0.85 * dangling * teleport[n] for n in nodes
        }
        for n in nodes:
            if outbound[n]:
                share = 0.85 * rank[n] / len(outbound[n])
                for target in sorted(outbound[n]):
                    next_rank[target] += share
        if sum(abs(rank[n] - next_rank[n]) for n in nodes) < 1e-12:
            rank = next_rank
            break
        rank = next_rank
    return rank


def adjacency(nodes, edges):
    graph = {n: {} for n in sorted(nodes)}
    for a, b in sorted(edges):
        if a == b or a not in graph or b not in graph:
            continue
        graph[a][b] = graph[a].get(b, 0) + 1
        graph[b][a] = graph[b].get(a, 0) + 1
    return graph


def modularity(graph, partition):
    degrees = {n: sum(graph[n].values()) for n in graph}
    total = sum(degrees.values())
    if not total:
        return 0.0
    inside = defaultdict(float)
    totals = defaultdict(float)
    for n in sorted(graph):
        group = partition[n]
        totals[group] += degrees[n]
        inside[group] += sum(
            w for peer, w in graph[n].items() if partition[peer] == group
        )
    return sum(inside[g] / total - (totals[g] / total) ** 2 for g in sorted(totals))


def _local(graph):
    nodes = sorted(graph)
    partition = {n: n for n in nodes}
    degrees = {n: sum(graph[n].values()) for n in nodes}
    total = sum(degrees.values())
    if not total:
        return partition
    totals = dict(degrees)
    for _ in range(100):
        changed = False
        for n in nodes:
            old = partition[n]
            degree = degrees[n]
            totals[old] -= degree
            weights = defaultdict(float)
            for peer, weight in graph[n].items():
                if peer != n:
                    weights[partition[peer]] += weight
            best = old
            best_gain = weights[old] - degree * totals[old] / total
            for group in sorted(weights):
                gain = weights[group] - degree * totals[group] / total
                if gain > best_gain + 1e-12:
                    best, best_gain = group, gain
            partition[n] = best
            totals[best] = totals.get(best, 0) + degree
            changed |= best != old
        if not changed:
            break
    return partition


def louvain(graph):
    """Multilevel local movement and aggregation, then connected-component refinement."""
    current = {n: dict(peers) for n, peers in graph.items()}
    members = {n: {n} for n in graph}
    original_partition = {n: n for n in graph}
    previous = modularity(graph, original_partition)
    for _ in range(20):
        partition = _local(current)
        groups = defaultdict(set)
        for n in sorted(current):
            groups[partition[n]].update(members[n])
        candidate = {n: min(group) for group in groups.values() for n in group}
        score = modularity(graph, candidate)
        if score < previous - 1e-12:
            break
        original_partition = candidate
        if len(groups) == len(current):
            break
        merged = {g: {} for g in sorted(groups)}
        for n in sorted(current):
            for peer, weight in current[n].items():
                a, b = partition[n], partition[peer]
                merged[a][b] = merged[a].get(b, 0) + weight
        current = merged
        members = dict(groups)
        previous = score
    refined = {}
    for group in sorted(set(original_partition.values())):
        remaining = {n for n in graph if original_partition[n] == group}
        while remaining:
            seed = min(remaining)
            visited = {seed}
            pending = [seed]
            while pending:
                node = pending.pop()
                for peer in sorted(graph[node]):
                    if peer in remaining and peer not in visited:
                        visited.add(peer)
                        pending.append(peer)
            for node in visited:
                refined[node] = seed
            remaining -= visited
    return refined


def _graph(conn, repo):
    nodes = {
        (r["repo"], r["path"])
        for r in conn.execute(
            "SELECT repo,path FROM files WHERE (? IS NULL OR repo=?) ORDER BY repo,path",
            (repo, repo),
        )
    }
    rows = query.edges(conn, repo=repo, limit=1_000_000)
    links = {
        ((r["repo"], r["from_path"]), (r["repo"], r["to_path"]))
        for r in rows
        if r["kind"] in {"imports", "calls", "tests"} and not r["stale"]
    }
    return nodes, rows, links


def _map(conn, nodes, links, focus, budget, limit):
    if budget < 2 or budget > 1024 * 1024:
        raise ValueError("map budget must be between 2 and 1048576 bytes")
    focused = [
        n
        for n in nodes
        if any(n[1] == p or n[1].startswith(p.rstrip("/") + "/") for p in focus)
    ]
    rank = pagerank(nodes, links, focused)
    fan_in = defaultdict(set)
    for a, b in links:
        fan_in[b].add(a)
    result = []
    for repo, path in sorted(nodes, key=lambda n: (-rank[n], n)):
        definitions = conn.execute(
            "SELECT * FROM symbols WHERE repo=? AND path=? ORDER BY CASE lane WHEN 'resolved' THEN 0 WHEN 'syntax' THEN 1 ELSE 2 END,line LIMIT 8",
            (repo, path),
        ).fetchall()
        if not definitions:
            continue
        row = store.decorate(conn, definitions[0])
        item = {
            k: row[k]
            for k in ("repo", "path", "lane", "extractor", "anchor_sha", "stale")
        }
        item.update(
            rank=round(rank[(repo, path)], 12),
            fan_in=len(fan_in[(repo, path)]),
            scopes=sorted(
                {
                    r["qualified"].rsplit(".", 1)[0]
                    for r in definitions
                    if "." in r["qualified"]
                }
            ),
            definitions=[
                {
                    key: store.decorate(conn, r).get(key)
                    for key in (
                        "qualified",
                        "signature",
                        "line",
                        "lane",
                        "extractor",
                        "anchor_sha",
                        "stale",
                    )
                }
                for r in definitions
            ],
        )
        if len(json.dumps([*result, item]).encode()) <= budget:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _submaps(conn, nodes, rows, links, focus, limit):
    graph = adjacency(nodes, links)
    partition = louvain(graph)
    directories = {n: (n[0], str(Path(n[1]).parent)) for n in nodes}
    baseline = modularity(graph, directories)
    score = modularity(graph, partition)
    groups = defaultdict(list)
    for n in sorted(nodes):
        groups[partition[n]].append(n)
    candidates = []
    if score > baseline + 1e-12:
        for group in sorted(groups.values()):
            if (
                len(group) < 2
                or focus
                and not any(
                    any(
                        n[1] == p or n[1].startswith(p.rstrip("/") + "/") for p in focus
                    )
                    for n in group
                )
            ):
                continue
            member_set = set(group)
            boundary = [
                r
                for r in rows
                if ((r["repo"], r["from_path"]) in member_set)
                != ((r["repo"], r["to_path"]) in member_set)
            ]
            internal = sum(a in member_set and b in member_set for a, b in links)
            coupling = len(boundary) / max(1, internal + len(boundary))
            identity = (
                "graph.feature."
                + hashlib.sha256(json.dumps(group).encode()).hexdigest()[:16]
            )
            anchors = []
            for repo, path in group[:64]:
                row = conn.execute(
                    "SELECT sha256 FROM files WHERE repo=? AND path=?", (repo, path)
                ).fetchone()
                anchors.append(dict(path=path, symbol=None, sha256=row[0]))
            record = dict(
                schema_version=1,
                id=identity,
                version=1,
                kind="component",
                claim="hypothesis",
                summary="Candidate feature boundary from source coupling.",
                data={
                    "responsibility": "Review the shared responsibility of the measured member files."
                },
                anchors=anchors,
                edges=[],
                attributes={
                    "graph.members": [n[1] for n in group[:64]],
                    "graph.coupling": coupling,
                    "graph.modularity": score,
                    "graph.directory_baseline": baseline,
                },
                replaces=[],
            )
            candidates.append(
                dict(
                    repo=group[0][0],
                    status="candidate",
                    members=[n[1] for n in group[:64]],
                    member_count=len(group),
                    truncated=len(group) > 64,
                    boundary_edges=boundary[:16],
                    coupling_score=coupling,
                    stale=False,
                    read_versions={identity: 0},
                    operations=[{"op": "put", "record": record}],
                )
            )
            if len(candidates) >= min(limit, 16):
                break
    return dict(
        modularity=score,
        directory_baseline=baseline,
        beats_directory=score > baseline + 1e-12,
        algorithm="louvain + connected components (fixed order)",
        candidates=candidates,
    )


def run(conn, command, *, repo=None, focus=(), budget=None, limit=20):
    nodes, rows, links = _graph(conn, repo)
    if command == "map":
        return _map(conn, nodes, links, focus, budget, limit)
    return _submaps(conn, nodes, rows, links, focus, limit)
