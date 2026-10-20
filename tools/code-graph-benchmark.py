#!/usr/bin/env python3
"""Reproducible graph benchmark over real repositories; all index state stays external.

Run with tiktoken installed separately (o200k_base counts, not provider billing;
token fields are null without it):
python tools/code-graph-benchmark.py --hx . --polyglot /path/to/eShop --state /tmp/bench --out /tmp/results.json

`--worktrees N` adds the fleet case: N worktrees of one clone of `--hx` at one HEAD,
the first one dirty, every query run inside each worktree.
"""

from __future__ import annotations
import argparse
import contextlib
import hashlib
import io
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from hx import code_index, code_store
from hx.code_build import discover


def tokens(text):
    try:
        import tiktoken
    except ImportError:
        return None
    return len(tiktoken.get_encoding("o200k_base").encode(text, disallowed_special=()))


def invoke(root, args):
    output = io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(output):
        status = code_index.main(args, root)
    elapsed = (time.perf_counter() - started) * 1000
    if status != 0:
        raise RuntimeError("graph query failed: " + repr(args))
    raw = output.getvalue()
    return json.loads(raw), raw, elapsed


def answered(title, result, expected, repo):
    if title.startswith("existing type"):
        return any(
            r["path"].endswith(expected) and r["signature"] and not r["stale"]
            for r in result
        )
    if title == "exact signature":
        return any(r["signature"] == expected and not r["stale"] for r in result)
    if title == "callers":
        return any(
            r["path"].endswith(expected) and r["reference"] in {"syntactic", "resolved"}
            for r in result
        )
    if title == "tests":
        return any(r["path"].endswith(expected) for r in result)
    if title == "parity diagnostic":
        return (
            bool(result["interfaces"] and result["uncertain"]) and not result["clean"]
        )
    if title == "interface parity":
        return (
            any(
                r["path"].endswith("Repositories/OrderRepository.cs")
                for r in result["implementations"]
            )
            and not result["diffs"]
            and bool(result["uncertain"])
        )
    if title == "existing precedent":
        return any(
            expected in r["snippet"] and r["snippet"] in (repo / r["path"]).read_text()
            for r in result
        )
    if title == "current owner":
        return bool(result) and all(r.get("owners") == [] for r in result)
    if title.startswith("fuzzy"):
        return any(
            expected in r["path"] and r.get("jev_score") is not None for r in result
        )
    return False


def bench(root, repo, label, questions):
    before_bytes = (
        code_store.db_path(root).stat().st_size
        if code_store.db_path(root).exists()
        else 0
    )
    started = time.perf_counter()
    indexed = code_index.index_repo(root, repo)
    duration = time.perf_counter() - started
    identity = code_store.namespace(repo)
    stats, _, _ = invoke(root, ["stats", "--namespace", identity])
    all_files = discover(repo)
    typed = [
        p
        for p in all_files
        if Path(p).suffix in {".py", ".cs", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}
    ]
    conn = code_store.connect(root)
    indexed_typed = sum(
        conn.execute(
            "SELECT 1 FROM files WHERE repo=? AND path=?", (identity, p)
        ).fetchone()
        is not None
        for p in typed
    )
    signatures = conn.execute(
        "SELECT count(*),sum(signature<>'') FROM symbols WHERE repo=?", (identity,)
    ).fetchone()
    references = conn.execute(
        "SELECT count(*),sum(lane='resolved') FROM refs WHERE repo=?", (identity,)
    ).fetchone()
    conn.close()
    results = []
    for title, args, term, expected in questions:
        samples = []
        for _ in range(3):
            result, raw, elapsed = invoke(root, [*args, "--namespace", identity])
            samples.append(elapsed)
        grep = subprocess.run(
            ["rg", "--no-heading", "-n", "-i", "-F", "--", term, str(repo)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        grep_raw = grep.stdout.replace(str(repo) + "/", "")
        found = answered(title, result, expected, repo)
        results.append(
            dict(
                question=title,
                command=args,
                graph_ms_median=round(statistics.median(samples), 3),
                graph_tokens=tokens(raw),
                grep_tokens=tokens(grep_raw),
                graph_bytes=len(raw.encode()),
                grep_bytes=len(grep_raw.encode()),
                expected=expected,
                answered=found,
                raw_grep_required=not found,
                sift_status=sorted(
                    {r.get("sift_status", "not-requested") for r in result}
                )
                if isinstance(result, list)
                else [],
                jev_scored=sum(r.get("jev_score") is not None for r in result)
                if isinstance(result, list)
                else 0,
                answer_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            )
        )
    submaps, _, elapsed = invoke(
        root, ["submaps", "--namespace", identity, "--limit", "8"]
    )
    return dict(
        label=label,
        repo=identity,
        head=indexed["head"],
        dirty=bool(code_store.git(repo, "status", "--porcelain")),
        index_seconds=round(duration, 3),
        index_bytes=stats["index_bytes"],
        index_bytes_added=stats["index_bytes"] - before_bytes,
        tracked_or_untracked_files=len(all_files),
        indexed_files=indexed["files"],
        file_coverage_percent=round(indexed["files"] / len(all_files) * 100, 2),
        source_file_coverage_percent=round(indexed_typed / max(1, len(typed)) * 100, 2),
        symbols=signatures[0],
        signature_coverage_percent=round(
            (signatures[1] or 0) / max(1, signatures[0]) * 100, 2
        ),
        references=references[0],
        resolved_reference_percent=round(
            (references[1] or 0) / max(1, references[0]) * 100, 2
        ),
        extractors=stats["extractors"],
        queries=results,
        submaps=dict(
            query_ms=round(elapsed, 3),
            modularity=submaps["modularity"],
            directory_baseline=submaps["directory_baseline"],
            beats_directory=submaps["beats_directory"],
            candidates=[
                dict(
                    members=c["members"],
                    member_count=c["member_count"],
                    coupling=c["coupling_score"],
                )
                for c in submaps["candidates"]
            ],
        ),
    )


def multi_worktree(state, source, count, questions):
    """Fleet shape: one namespace, N roots at one HEAD, one dirty overlay."""
    work = state / "multi-worktree"
    clone = work / "clone"
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(source), str(clone)], check=True
    )
    origin = code_store.git(source, "remote", "get-url", "origin")
    if origin:
        subprocess.run(
            ["git", "-C", str(clone), "remote", "set-url", "origin", origin], check=True
        )
    trees = []
    for i in range(count):
        tree = work / f"wt-{i:02}"
        subprocess.run(
            ["git", "-C", str(clone), "worktree", "add", "--quiet", "--detach", str(tree)],
            check=True,
        )
        trees.append(tree.resolve())
    (trees[0] / "bench_dirty_marker.py").write_text("class BenchDirtyMarker:\n    pass\n")
    index_root = work / "state"
    identity = code_store.namespace(trees[0])
    indexed = []
    for tree in trees:
        started = time.perf_counter()
        result = code_index.index_repo(index_root, tree)
        indexed.append(
            dict(
                changed=result["changed"],
                seconds=round(time.perf_counter() - started, 3),
                snapshot=result["snapshot"],
            )
        )
    timed = [q for q in questions if "--sift" not in q[1]]
    samples, per_tree = [], []
    home = Path.cwd()
    try:
        for i, tree in enumerate(trees):
            # The command runs inside the worktree, exactly as a fleet agent's does.
            os.chdir(tree)
            medians = []
            for _, args, _, _ in timed:
                medians.append(
                    statistics.median(
                        invoke(index_root, [*args, "--namespace", identity])[2]
                        for _ in range(3)
                    )
                )
            found, _, _ = invoke(
                index_root, ["type", "BenchDirtyMarker", "--namespace", identity]
            )
            per_tree.append(
                dict(
                    worktree=i,
                    query_ms_median=round(statistics.median(medians), 3),
                    dirty_marker_visible=bool(found),
                    answers_for_caller=bool(found) == (i == 0),
                )
            )
            samples.extend(medians)
    finally:
        os.chdir(home)
    return dict(
        worktrees=count,
        repo=identity,
        index=indexed,
        questions=len(timed),
        warm_query_ms_median=round(statistics.median(samples), 3),
        answers_for_caller=all(t["answers_for_caller"] for t in per_tree),
        per_worktree=per_tree,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hx", type=Path, required=True)
    parser.add_argument("--polyglot", type=Path)
    parser.add_argument("--worktrees", type=int, default=0)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    # Question set is pinned to real declarations; no synthetic benchmark repository.
    hx_questions = [
        (
            "existing type, case folded",
            ["type", "continuitystore"],
            "ContinuityStore",
            "src/hx/continuity_store.py",
        ),
        (
            "exact signature",
            ["signature", "ContinuityStore.transaction"],
            "def transaction",
            "def transaction(self) -> Iterator[Transaction]",
        ),
        (
            "callers",
            ["callers", "source_anchor"],
            "source_anchor",
            "src/hx/map_updates.py",
        ),
        (
            "tests",
            ["tests-for", "src/hx/appmap.py"],
            "appmap",
            "tests/core/test_appmap.py",
        ),
        ("parity diagnostic", ["conform", "MapConflict"], "MapConflict", "uncertain"),
        (
            "existing precedent",
            ["precedent", "BEGIN IMMEDIATE"],
            "BEGIN IMMEDIATE",
            "BEGIN IMMEDIATE",
        ),
        (
            "current owner",
            ["type", "ContinuityStore", "--owners"],
            "ContinuityStore",
            "owners",
        ),
        (
            "fuzzy service intent with Jev",
            [
                "search",
                "native process tracking",
                "--limit",
                "10",
                "--sift",
                "Is there already code that tracks managed native processes?",
            ],
            "native processes",
            "native_processes.py",
        ),
    ]
    cs_questions = [
        (
            "existing type, case folded",
            ["type", "iorderrepository"],
            "IOrderRepository",
            "IOrderRepository.cs",
        ),
        (
            "exact signature",
            [
                "signature",
                "eShop.Ordering.Domain.AggregatesModel.OrderAggregate.IOrderRepository.Add",
            ],
            "Order Add",
            "Order Add(Order order)",
        ),
        (
            "callers",
            ["callers", "IOrderRepository"],
            "IOrderRepository",
            "CancelOrderCommandHandler.cs",
        ),
        (
            "tests",
            ["tests-for", "OrderRepository"],
            "IOrderRepository",
            "NewOrderCommandHandlerTest.cs",
        ),
        (
            "interface parity",
            ["conform", "IOrderRepository"],
            "IOrderRepository",
            "OrderRepository",
        ),
        (
            "existing precedent",
            ["precedent", "AddScoped", "--language", "csharp"],
            "AddScoped",
            "AddScoped",
        ),
        (
            "current owner",
            ["type", "IOrderRepository", "--owners"],
            "IOrderRepository",
            "owners",
        ),
        (
            "fuzzy service intent with Jev",
            [
                "search",
                "publish integration event",
                "--limit",
                "10",
                "--sift",
                "Is there already code that publishes integration events?",
            ],
            "integration event",
            "IntegrationEvent",
        ),
    ]
    args.state.mkdir(parents=True, exist_ok=True)
    repositories = [bench(args.state, args.hx.resolve(), "hx", hx_questions)]
    if args.polyglot:
        repositories.append(
            bench(args.state, args.polyglot.resolve(), "eShop", cs_questions)
        )
    fleet = (
        multi_worktree(args.state, args.hx.resolve(), args.worktrees, hx_questions)
        if args.worktrees
        else None
    )
    contracts = [
        dict(
            name=name,
            path=path,
            version="1",
            producer=label,
            repo=code_store.namespace(repo),
            sha=code_store.git(repo, "rev-parse", "HEAD"),
        )
        for repo, label, name, path in [
            (args.hx, "hx", "ContinuityStore", "src/hx/continuity_store.py"),
            (
                args.polyglot,
                "eShop",
                "IOrderRepository",
                "src/Ordering.Domain/AggregatesModel/OrderAggregate/IOrderRepository.cs",
            ),
        ]
        if label == "hx" or args.polyglot
    ]
    file = args.state / "contracts.json"
    file.write_text(json.dumps(contracts))
    invoke(args.state, ["contracts", "--register", str(file)])
    result, raw, ms = invoke(args.state, ["contracts"])
    report = dict(
        tokenizer="tiktoken o200k_base (comparison count, not billing)",
        query_latency="in-process warm queries, median of three; includes SQLite open/close",
        repositories=repositories,
        multi_worktree=fleet,
        shared_contract_query=dict(
            answered=len(result) == len(repositories),
            namespaces=len({r["repo"] for r in result}),
            tokens=tokens(raw),
            ms=round(ms, 3),
        ),
        promotion=dict(
            live_waves=0,
            promoted=None,
            reason="No live fleet wave has been run; promotion is not expanded.",
        ),
        baseline="rg -n -i -F: a literal-match output baseline; it does not prove parity or ownership",
        no_model_accuracy_claim=True,
    )
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                repos=[
                    dict(
                        label=r["label"],
                        seconds=r["index_seconds"],
                        bytes=r["index_bytes"],
                        answered=sum(q["answered"] for q in r["queries"]),
                        questions=len(r["queries"]),
                    )
                    for r in repositories
                ],
                warm_query_ms_median=[
                    statistics.median(q["graph_ms_median"] for q in r["queries"])
                    for r in repositories
                ],
                multi_worktree=fleet
                and dict(
                    warm_query_ms_median=fleet["warm_query_ms_median"],
                    answers_for_caller=fleet["answers_for_caller"],
                ),
                output=str(args.out),
            )
        )
    )


if __name__ == "__main__":
    main()
