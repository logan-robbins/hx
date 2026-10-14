"""Immutable revision identities, reusable objects, and durable delta refreshes."""

import concurrent.futures
import json
import subprocess
from pathlib import Path

import pytest

from hx import code_build, code_freshness, code_index, code_journal, code_snapshot
from hx.code_store import connect, namespace


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    )
    return result.stdout.decode().strip()


def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "remote", "add", "origin", "https://example.test/immutable-repo.git")
    (repo / "item.py").write_text('def original(): return "first-marker"\n')
    (repo / "caller.py").write_text("from item import original\noriginal()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "first")
    return repo


def test_at_pins_source_symbols_search_and_cli_even_without_checkout(tmp_path):
    repo = repository(tmp_path)
    root = tmp_path / "state"
    first = code_index.index_repo(root, repo)
    first_commit = first["head"]
    (repo / "item.py").write_text('def replacement(): return "second-marker"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-m", "second")
    second = code_index.index_repo(root, repo)
    assert first["snapshot"] != second["snapshot"]
    assert (
        code_index.symbols_for(root, "original", at=first_commit)[0]["source_proof"]
        == "immutable-snapshot"
    )
    assert not code_index.symbols_for(root, "original")
    assert code_index.search(root, "first-marker", at=first["snapshot"])
    assert code_index.callers(root, "original", at=first["snapshot"])
    status, result = code_index.execute(
        ["symbols", "original", "--at", first["snapshot"], "--expect", "fresh"], root
    )
    assert status == 0 and result[0]["snapshot"] == first["snapshot"]
    git_dir = repo / ".git"
    git_dir.rename(repo / ".git-away")
    (repo / "item.py").unlink()
    assert not code_index.symbols_for(root, "original", at=first["snapshot"])[0][
        "stale"
    ]
    assert code_index.search(root, "first-marker", at=first["snapshot"])


def test_identical_checkouts_share_snapshot_without_discovery_or_extraction(
    tmp_path, monkeypatch
):
    repo = repository(tmp_path)
    root = tmp_path / "state"
    first = code_index.index_repo(root, repo)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(repo), str(clone)], check=True, capture_output=True
    )
    git(clone, "remote", "set-url", "origin", git(repo, "remote", "get-url", "origin"))

    def unexpected(*args, **kwargs):
        raise AssertionError(
            "identical checkout repeated repository discovery or parsing"
        )

    monkeypatch.setattr(code_build, "discover", unexpected)
    monkeypatch.setattr(code_build.code_extract, "extract", unexpected)
    second = code_index.index_repo(root, clone)
    assert second["changed"] == 0
    assert first["snapshot"] == second["snapshot"]
    assert code_freshness.scope(root, repo, ["item.py"])["fresh"]
    assert code_freshness.scope(root, clone, ["item.py"])["fresh"]
    (repo / "item.py").write_text("def changed(): return 1\n")
    assert not code_freshness.scope(root, repo, ["item.py"])["fresh"]
    assert code_freshness.scope(root, clone, ["item.py"])["fresh"]


def test_path_map_changes_share_nodes_and_extract_only_commit_delta(
    tmp_path, monkeypatch
):
    repo = repository(tmp_path)
    for i in range(180):
        (repo / f"unit_{i:03}.py").write_text(f"def unique_{i}(): return {i}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "many paths")
    root = tmp_path / "state"
    first = code_index.index_repo(root, repo)
    conn = connect(root)
    old_nodes = {r[0] for r in conn.execute("SELECT identity FROM snapshot_nodes")}
    old_objects = conn.execute("SELECT count(*) FROM graph_objects").fetchone()[0]
    conn.close()
    (repo / "unit_080.py").write_text("def revised(): return 80\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "one delta")
    original = code_build.code_extract.extract
    calls = []

    def extract(text, path):
        calls.append(path)
        return original(text, path)

    monkeypatch.setattr(code_build.code_extract, "extract", extract)
    monkeypatch.setattr(
        code_build,
        "discover",
        lambda *a, **kw: pytest.fail("commit delta must not enumerate repository"),
    )
    second = code_index.index_repo(root, repo)
    assert calls == ["unit_080.py"] and second["changed"] == 1
    conn = connect(root)
    new_nodes = {r[0] for r in conn.execute("SELECT identity FROM snapshot_nodes")}
    assert 0 < len(new_nodes - old_nodes) < 40
    assert (
        conn.execute("SELECT count(*) FROM graph_objects").fetchone()[0] - old_objects
        == 1
    )
    before = code_snapshot.resolve(conn, first["snapshot"])
    after = code_snapshot.resolve(conn, second["snapshot"])
    tree = code_snapshot.PathMap(conn)
    before_map = dict(tree.items(before["map_root"]))
    after_map = dict(tree.items(after["map_root"]))
    assert sum(before_map[p] != after_map[p] for p in before_map) == 1
    conn.close()


def test_scope_refresh_and_admission_never_enumerate_unrelated_paths(
    tmp_path, monkeypatch
):
    repo = repository(tmp_path)
    root = tmp_path / "state"
    code_index.index_repo(root, repo)
    original_run = subprocess.run

    def run(argv, **kwargs):
        if "ls-files" in argv:
            pytest.fail("exact path must not enumerate git files")
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        code_build,
        "discover",
        lambda *a, **kw: pytest.fail("exact path must not enumerate repository"),
    )
    assert code_freshness.scope(root, repo, ["item.py"])["fresh"]
    (repo / "item.py").write_text('def original(): return "changed"\n')
    assert code_index.index_repo(root, repo, paths=["item.py"])["changed"] == 1
    assert code_freshness.scope(root, repo, ["item.py"])["fresh"]
    assert not code_freshness.scope(root, repo, ["caller.py"])["fresh"]


def test_crash_journal_replay_pins_bytes_modes_deletes_and_is_idempotent(
    tmp_path, monkeypatch
):
    repo = repository(tmp_path)
    root = tmp_path / "state"
    code_index.index_repo(root, repo)
    (repo / "caller.py").unlink()
    (repo / "item.py").write_text('def prepared(): return "durable"\n')
    (repo / "item.py").chmod(0o755)
    original_apply = code_build.apply_changeset

    def crash(*args, **kwargs):
        raise RuntimeError("crash after durable preparation")

    monkeypatch.setattr(code_build, "apply_changeset", crash)
    with pytest.raises(RuntimeError):
        code_index.index_repo(root, repo, paths=["item.py", "caller.py"])
    pending = code_journal.pending(root)
    assert len(pending) == 1
    (repo / "item.py").write_text('def later(): return "not-in-changeset"\n')
    monkeypatch.setattr(code_build, "apply_changeset", original_apply)
    applied = code_journal.replay(root, pending[0]["identity"])
    assert code_index.symbols_for(root, "prepared", at=applied["snapshot"])
    assert code_index.symbols_for(root, "prepared")[0]["stale"]
    assert not code_index.symbols_for(root, "later", at=applied["snapshot"])
    conn = connect(root)
    rows = {
        r["path"]: dict(r)
        for r in conn.execute(
            "SELECT * FROM code_changes WHERE identity=?", (pending[0]["identity"],)
        )
    }
    assert (
        rows["caller.py"]["operation"] == "delete" and rows["caller.py"]["sha"] is None
    )
    assert rows["item.py"]["mode"] == "100755" and rows["item.py"]["sha"]
    conn.close()
    replayed = code_journal.replay(root, pending[0]["identity"])
    assert replayed["changed"] == 0 and replayed["snapshot"] == applied["snapshot"]
    assert not code_journal.pending(root)


def test_concurrent_heads_and_dirty_overlays_remain_isolated(tmp_path):
    repo = repository(tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(repo), str(clone)], check=True, capture_output=True
    )
    git(clone, "remote", "set-url", "origin", git(repo, "remote", "get-url", "origin"))
    git(clone, "config", "user.email", "test@example.com")
    git(clone, "config", "user.name", "Test")
    (repo / "item.py").write_text("def branch_one(): return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "one")
    (clone / "item.py").write_text("def branch_two(): return 2\n")
    git(clone, "add", ".")
    git(clone, "commit", "-m", "two")
    root = tmp_path / "state"
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(code_index.index_repo, root, path) for path in (repo, clone)
        ]
        one, two = [f.result() for f in futures]
    assert one["head"] != two["head"] and one["snapshot"] != two["snapshot"]
    assert code_index.symbols_for(root, "branch_one", at=one["snapshot"])
    assert not code_index.symbols_for(root, "branch_two", at=one["snapshot"])
    assert code_index.symbols_for(root, "branch_two", at=two["snapshot"])
    assert code_freshness.scope(root, repo, ["item.py"])["fresh"]
    assert code_freshness.scope(root, clone, ["item.py"])["fresh"]
    (repo / "item.py").write_text("def dirty_one(): return 11\n")
    dirty = code_index.index_repo(root, repo)
    assert dirty["head"] == one["head"] and dirty["snapshot"] != one["snapshot"]
    assert code_index.symbols_for(root, "dirty_one", at=dirty["snapshot"])
    assert code_index.symbols_for(root, "branch_one", at=one["head"])


def test_extractor_path_and_build_context_are_in_cache_identity(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    root = tmp_path / "state"
    first = code_index.index_repo(root, repo)
    (repo / "copied.py").write_bytes((repo / "item.py").read_bytes())
    second = code_index.index_repo(root, repo, paths=["copied.py"])
    assert second["reused_extractions"] == 0
    (repo / "pyproject.toml").write_text('[project]\nname="changed-build-context"\n')
    third = code_index.index_repo(root, repo, paths=["item.py"])
    assert third["changed"] == 1 and third["reused_extractions"] == 0
    assert code_index.symbols_for(root, "original", at=first["snapshot"])
    from hx import code_adapters

    original_detect = code_adapters.detect

    def detect():
        value = original_detect()
        value["generation-for-test"] = 2
        return value

    monkeypatch.setattr(code_adapters, "detect", detect)
    fourth = code_index.index_repo(root, repo, paths=["item.py"])
    assert fourth["changed"] == 1 and fourth["reused_extractions"] == 0
    conn = connect(root)
    values = conn.execute(
        'SELECT DISTINCT identity FROM extraction_objects WHERE path="item.py"'
    ).fetchall()
    assert len(values) == 3
    conn.close()


def test_imported_lane_is_pinned_in_immutable_overlay(tmp_path):
    from hx.code_import import import_graph

    repo = repository(tmp_path)
    root = tmp_path / "state"
    first = code_index.index_repo(root, repo)
    symbols = tmp_path / "symbols.jsonl"
    edges = tmp_path / "edges.jsonl"
    symbols.write_text(
        json.dumps(
            dict(
                name="External",
                qualified="External",
                kind="function",
                path="item.py",
                line=1,
                end_line=1,
                signature="external declaration",
            )
        )
        + "\n"
    )
    edges.write_text("")
    imported = import_graph(root, namespace(repo), symbols, edges)
    assert not code_index.symbols_for(root, "External", at=first["snapshot"])
    rows = code_index.symbols_for(root, "External", at=imported["snapshot"])
    assert rows[0]["lane"] == "imported" and not rows[0]["unanchored"]
    symbols.write_text("")
    removed = import_graph(root, namespace(repo), symbols, edges)
    assert not code_index.symbols_for(root, "External", at=removed["snapshot"])
    assert code_index.symbols_for(root, "External", at=imported["snapshot"])


def test_explicit_unindexed_revision_captures_git_objects_and_keeps_live_overlay(
    tmp_path,
):
    repo = repository(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    root = tmp_path / "state"
    (repo / "item.py").write_text('def dirty_live(): return "dirty-only"\n')
    live = code_index.index_repo(root, repo)
    assert code_index.symbols_for(root, "dirty_live")
    # A commit label must capture the clean tree even if only a dirty overlay was indexed.
    rows = code_index.symbols_for(root, "original", at=head)
    assert rows and not rows[0]["stale"]
    assert not code_index.symbols_for(root, "dirty_live", at=head)
    assert code_index.symbols_for(root, "dirty_live")
    conn = connect(root)
    assert (
        conn.execute(
            "SELECT snapshot FROM snapshot_refs WHERE ref='latest'"
        ).fetchone()[0]
        == live["snapshot"]
    )
    conn.close()
    status, result = code_index.execute(
        ["index", "--repo", str(repo), "--at", head], root
    )
    assert status == 0 and result["historical"] and result["changed"] == 0


def test_historical_parent_delta_and_explicit_budget(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    root = tmp_path / "state"
    first = code_index.index_repo(root, repo)
    (repo / "item.py").write_text('def historical_child(): return "child-only"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-m", "unindexed child")
    head = git(repo, "rev-parse", "HEAD")
    called = []
    extract = code_build.code_extract.extract

    def wrapped(text, path):
        called.append(path)
        return extract(text, path)

    monkeypatch.setattr(code_build.code_extract, "extract", wrapped)
    captured = code_build.index_revision(root, repo, head)
    assert called == ["item.py"] and captured["changed"] == 1
    assert code_index.symbols_for(root, "original")
    assert code_index.symbols_for(root, "historical_child", at=head)
    (repo / "big.py").write_text("x" * 2048)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "budgeted")
    with pytest.raises(ValueError, match="budget"):
        code_build.index_revision(root, repo, "HEAD", max_revision_bytes=1024)
    assert code_index.symbols_for(root, "original", at=first["snapshot"])


def test_scoped_refresh_keeps_checkout_parent_when_other_branch_is_latest(tmp_path):
    repo = repository(tmp_path)
    (repo / "branch.py").write_text("def branch_a(): return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "branch a")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(repo), str(clone)], check=True, capture_output=True
    )
    git(clone, "remote", "set-url", "origin", git(repo, "remote", "get-url", "origin"))
    git(clone, "config", "user.email", "test@example.com")
    git(clone, "config", "user.name", "Test")
    (clone / "branch.py").write_text("def branch_b(): return 2\n")
    git(clone, "add", ".")
    git(clone, "commit", "-m", "branch b")
    root = tmp_path / "state"
    a = code_index.index_repo(root, repo)
    b = code_index.index_repo(root, clone)
    (repo / "item.py").write_text("def scoped_a(): return 3\n")
    scoped = code_index.index_repo(root, repo, paths=["item.py"])
    assert code_index.symbols_for(root, "branch_a", at=scoped["snapshot"])
    assert not code_index.symbols_for(root, "branch_b", at=scoped["snapshot"])
    assert code_index.symbols_for(root, "branch_a")
    assert code_index.symbols_for(root, "scoped_a")
    assert code_freshness.scope(root, repo, ["branch.py"])["fresh"]
    assert code_freshness.scope(root, clone, ["branch.py"])["fresh"]
    assert code_index.symbols_for(root, "branch_b", at=b["snapshot"])
    # A path present only in the checkout's old map must be removed even when
    # another branch's mutable projection no longer contains it.
    (repo / "branch.py").unlink()
    removed = code_index.index_repo(root, repo, paths=["branch.py"])
    assert not code_index.symbols_for(root, "branch_a", at=removed["snapshot"])
    assert code_index.symbols_for(root, "branch_a", at=a["snapshot"])


def test_live_and_historical_search_keep_porter_and_substring_lanes(tmp_path):
    repo = repository(tmp_path)
    (repo / "notes.txt").write_text("registration registers registered registering\n")
    root = tmp_path / "state"
    indexed = code_index.index_repo(root, repo)
    assert code_index.search(root, "register")
    assert code_index.search(root, "register", at=indexed["snapshot"])
    assert code_index.search(root, "gistrat", at=indexed["snapshot"])


def test_parser_preparation_holds_no_writer_transaction(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    root = tmp_path / "state"
    original = code_build.code_extract.extract
    calls = []

    def extract(text, path):
        probe = connect(root)
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()
        probe.close()
        calls.append(path)
        return original(text, path)

    monkeypatch.setattr(code_build.code_extract, "extract", extract)
    code_index.index_repo(root, repo)
    assert calls


def test_git_modes_and_symlink_content_are_in_journal_and_freshness(tmp_path):
    repo = repository(tmp_path)
    (repo / "linked.py").symlink_to("item.py")
    root = tmp_path / "state"
    first = code_index.index_repo(root, repo)
    assert code_freshness.scope(root, repo, ["linked.py"])["fresh"]
    (repo / "item.py").chmod(0o755)
    assert not code_freshness.scope(root, repo, ["item.py"])["fresh"]
    assert code_index.symbols_for(root, "original")[0]["stale"]
    second = code_index.index_repo(root, repo, paths=["item.py"])
    conn = connect(root)
    assert (
        conn.execute(
            "SELECT mode FROM code_changes WHERE identity=?", (second["journal"],)
        ).fetchone()[0]
        == "100755"
    )
    linked = conn.execute(
        'SELECT mode,sha FROM code_changes WHERE identity=? AND path="linked.py"',
        (first["journal"],),
    ).fetchone()
    assert linked["mode"] == "120000"
    import hashlib

    assert linked["sha"] == hashlib.sha256(b"item.py").hexdigest()
    conn.close()


def test_resolved_process_preparation_holds_no_writer(tmp_path, monkeypatch):
    from hx import code_scip

    repo = repository(tmp_path)
    root = tmp_path / "state"
    calls = []

    def prepare(*args, **kwargs):
        assert kwargs["prepare_only"]
        probe = connect(root)
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()
        probe.close()
        calls.append(True)
        return dict(status="skipped", reason="test preparation")

    monkeypatch.setattr(code_scip, "index", prepare)
    result = code_index.index_repo(root, repo, resolve=True)
    assert calls and result["resolved"]["status"] == "skipped"


def test_configured_service_routes_admission_and_completion(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from hx import repo_service

    socket = tmp_path / "shared.sock"
    monkeypatch.setattr(repo_service, "configured_socket", lambda root: socket)
    calls = []

    def request(path, payload):
        calls.append((path, payload))
        return dict(fresh=True, repo="shared", paths=[], reasons=[])

    monkeypatch.setattr(repo_service, "request", request)
    result = code_freshness.scope(
        tmp_path / "instance", tmp_path / "repo", ["src/a.py"], bootstrap=True
    )
    assert result["fresh"] and calls[0][1]["method"] == "scope"
    assert calls[0][1]["paths"] == ["src/a.py"]
    monkeypatch.setattr(
        repo_service,
        "code_request",
        lambda root, argv: dict(
            exit_code=0, result=dict(changed=1, snapshot="shared-snapshot")
        ),
    )
    refreshed = code_freshness.refresh_completion(
        SimpleNamespace(root=tmp_path / "instance"), tmp_path / "repo", ["src/a.py"]
    )
    assert refreshed["snapshot"] == "shared-snapshot"


def test_named_queries_decompress_only_matching_paths_in_large_snapshot(
    tmp_path, monkeypatch
):
    from hx import code_queries

    repo = repository(tmp_path)
    for i in range(900):
        (repo / f"unrelated_{i:04}.py").write_text(f"def unrelated_{i}(): return {i}\n")
    root = tmp_path / "state"
    indexed = code_index.index_repo(root, repo)
    conn = connect(root)
    code_snapshot.activate_latest(conn)
    counts = dict(graph=0, view=0)
    original = code_snapshot.unpacked

    def unpack(value):
        counts["graph"] += 1
        return original(value)

    def unpack_view(value):
        counts["view"] += 1
        raise AssertionError("named query walked the global JSON snapshot view")

    monkeypatch.setattr(code_snapshot, "unpacked", unpack)
    monkeypatch.setattr(
        code_snapshot.PathMap,
        "items",
        lambda *a: pytest.fail("named query enumerated the full snapshot map"),
    )
    conn.create_function("hx_unpack", 1, unpack_view)
    statements = []
    conn.set_trace_callback(statements.append)
    assert code_queries.symbols(conn, "original")
    assert code_queries.callers(conn, "original", count=True)["count"] == 1
    assert code_queries.search(conn, "original")
    assert counts == dict(graph=2, view=0)
    path_reads = [
        sql
        for sql in statements
        if "SELECT * FROM snapshot_nodes WHERE identity=" in sql
    ]
    assert len(path_reads) < 60
    conn.close()
    # Explicit historical lookup follows exactly the same indexed membership path.
    conn = connect(root)
    code_snapshot.activate(conn, indexed["snapshot"])
    conn.create_function("hx_unpack", 1, unpack_view)
    counts["graph"] = 0
    assert code_queries.symbols(conn, "original")
    assert counts["graph"] == 1 and counts["view"] == 0
    conn.close()


def test_old_postings_cannot_consume_current_symbol_or_search_candidate_cap(tmp_path):
    repo = repository(tmp_path)
    for i in range(135):
        (repo / f"old_{i:03}.py").write_text(
            f'def sought(): return "historical-token {i}"\n'
        )
    (repo / "z_current.py").write_text('def sought(): return "current-token"\n')
    root = tmp_path / "state"
    first = code_index.index_repo(root, repo)
    for i in range(135):
        (repo / f"old_{i:03}.py").write_text(
            f'def retired_{i}(): return "replacement {i}"\n'
        )
    (repo / "z_notes.txt").write_text("historical-token valid-current-source\n")
    code_index.index_repo(root, repo)
    current = code_index.symbols_for(root, "sought", limit=1)
    assert len(current) == 1 and current[0]["path"] == "z_current.py"
    assert (
        code_index.symbols_for(root, "sought", at=first["snapshot"], limit=1)[0]["path"]
        == "old_000.py"
    )
    hits = code_index.search(root, "historical-token", limit=1)
    assert hits and hits[0]["path"] == "z_notes.txt"


def test_caller_count_excludes_all_definition_files_beyond_symbol_output_cap(tmp_path):
    repo = repository(tmp_path)
    for i in range(125):
        (repo / f"definition_{i:03}.py").write_text(
            "def shared_name(): return shared_name()\n"
        )
    for i in range(140):
        (repo / f"reference_{i:03}.py").write_text("shared_name()\n")
    root = tmp_path / "state"
    code_index.index_repo(root, repo)
    count = code_index.callers(root, "shared_name", count=True, limit=3)
    assert count["count"] == 140
    rows = code_index.callers(root, "shared_name", limit=3)
    assert len(rows) == 3 and all(r["path"].startswith("reference_") for r in rows)


def test_existing_snapshots_backfill_indexed_postings_without_reextracting(
    tmp_path, monkeypatch
):
    repo = repository(tmp_path)
    root = tmp_path / "state"
    indexed = code_index.index_repo(root, repo)
    conn = connect(root)
    for table in (
        "snapshot_symbol_postings",
        "snapshot_ref_postings",
        "snapshot_edge_postings",
        "snapshot_file_postings",
    ):
        conn.execute("DROP TABLE " + table)
    conn.execute("DELETE FROM metadata WHERE key='snapshot_postings_version'")
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        code_build.code_extract,
        "extract",
        lambda *a: pytest.fail("posting upgrade must not parse source"),
    )
    assert code_index.symbols_for(root, "original", at=indexed["snapshot"])
    assert code_index.callers(root, "original", count=True)["count"] == 1


def test_named_reuse_and_impact_avoid_global_json_views(tmp_path, monkeypatch):
    from hx import code_reuse

    repo = repository(tmp_path)
    (repo / "a.cs").write_text(
        "namespace Acme;\npublic interface IFoo { int Run(string name); }\npublic class Foo : IFoo { public int Run(string value) { return 1; } }\n"
    )
    (repo / "app.cs").write_text(
        'services.AddScoped<IFoo,Foo>();\nnew Foo().Run("x");\n'
    )
    (repo / "test.cs").write_text(
        '[TestFixture]\nclass FooTests { [Test] public void Works() { new Foo().Run("x"); } }\n'
    )
    for i in range(300):
        (repo / f"noise_{i:03}.cs").write_text(f"public class Unrelated{i} {{}}\n")
    root = tmp_path / "state"
    indexed = code_index.index_repo(root, repo)
    activate = code_snapshot.activate

    def selected(*args, **kwargs):
        result = activate(*args, **kwargs)
        args[0].create_function(
            "hx_unpack",
            1,
            lambda value: pytest.fail("named reuse walked global JSON views"),
        )
        return result

    monkeypatch.setattr(code_snapshot, "activate", selected)
    monkeypatch.setattr(
        code_snapshot.PathMap,
        "items",
        lambda *a: pytest.fail("named reuse enumerated map"),
    )
    conn = connect(root)
    code_snapshot.activate_latest(conn)
    assert code_reuse.conform(conn, "IFoo")["clean"]
    assert code_reuse.tests_for(conn, "Foo")
    assert code_reuse.run(conn, "register", "IFoo")
    assert code_reuse.run(conn, "precedent", "AddScoped")
    assert len(conn.graph_cache) == 3
    conn.close()
    assert code_index.impact(root, ["a.cs"], at=indexed["snapshot"])["symbols"]
    status, result = code_index.execute(
        ["conform", "IFoo", "--at", indexed["snapshot"], "--expect", "clean"], root
    )
    assert status == 0 and result["clean"]


def test_unchanged_index_reuses_snapshot_counts_without_graph_dependency_reads(
    tmp_path, monkeypatch
):
    repo = repository(tmp_path)
    for i in range(650):
        (repo / f"constant_{i:03}.py").write_text(f"def stable_{i}(): return {i}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "large clean snapshot")
    root = tmp_path / "state"
    first = code_index.index_repo(root, repo)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(repo), str(clone)], check=True, capture_output=True
    )
    git(clone, "remote", "set-url", "origin", git(repo, "remote", "get-url", "origin"))
    from hx import code_invalidation

    monkeypatch.setattr(
        code_invalidation,
        "dependencies",
        lambda *a, **kw: pytest.fail("unchanged index rebuilt dependency metadata"),
    )
    monkeypatch.setattr(
        code_snapshot.PathMap,
        "items",
        lambda *a: pytest.fail("unchanged index enumerated snapshot"),
    )
    unpack = code_snapshot.unpacked

    def no_graph(value):
        result = unpack(value)
        assert not isinstance(result, dict) or "files" not in result, (
            "unchanged index decompressed graph"
        )
        return result

    monkeypatch.setattr(code_snapshot, "unpacked", no_graph)

    def no_discovery(*a, **kw):
        raise AssertionError("unchanged checkout re-enumerated source")

    monkeypatch.setattr(code_build, "discover", no_discovery)
    monkeypatch.setattr(code_build.code_extract, "extract", no_discovery)
    for checkout in (repo, clone):
        result = code_index.index_repo(root, checkout)
        assert result["snapshot"] == first["snapshot"] and result["changed"] == 0
        assert (result["files"], result["symbols"]) == (
            first["files"],
            first["symbols"],
        )


def test_scoped_invalidation_visits_only_symbol_and_import_dependents(
    tmp_path, monkeypatch
):
    repo = repository(tmp_path)
    (repo / "pkg").mkdir()
    (repo / "pkg/helper.py").write_text("def helper_work(): return 1\n")
    (repo / "importer.py").write_text("import pkg.helper\n")
    (repo / "outer.py").write_text("import importer\n")
    for i in range(450):
        (repo / f"noise_{i:03}.py").write_text(f"def noise_{i}(): return {i}\n")
    root = tmp_path / "state"
    code_index.index_repo(root, repo)
    paths = []
    graph = code_snapshot.graph_for

    def visited(conn, identity, path):
        paths.append(path)
        assert not path.startswith("noise_"), "invalidation read unrelated source graph"
        return graph(conn, identity, path)

    monkeypatch.setattr(code_snapshot, "graph_for", visited)
    monkeypatch.setattr(
        code_snapshot.PathMap,
        "items",
        lambda *a: pytest.fail("scoped invalidation enumerated entire map"),
    )
    (repo / "pkg/helper.py").write_text("def helper_work(): return 2\n")
    changed = code_index.index_repo(root, repo, paths=["pkg/helper.py"])
    assert changed["changed"] == 1
    assert {"pkg/helper.py", "importer.py", "outer.py"} <= set(paths)
    assert not code_freshness.scope(root, repo, ["importer.py"])["fresh"]
    assert not code_freshness.scope(root, repo, ["outer.py"])["fresh"]
    assert code_freshness.scope(root, repo, ["noise_001.py"])["fresh"]


def test_new_and_deleted_definitions_invalidate_existing_callers(tmp_path):
    repo = repository(tmp_path)
    (repo / "waiting.py").write_text("new_definition()\n")
    root = tmp_path / "state"
    code_index.index_repo(root, repo)
    (repo / "new.py").write_text("def new_definition(): return 1\n")
    added = code_index.index_repo(root, repo, paths=["new.py"])
    assert not code_freshness.scope(root, repo, ["waiting.py"])["fresh"]
    code_index.index_repo(root, repo)
    (repo / "new.py").unlink()
    deleted = code_index.index_repo(root, repo, paths=["new.py"])
    assert not code_freshness.scope(root, repo, ["waiting.py"])["fresh"]
    assert code_index.symbols_for(root, "new_definition", at=added["snapshot"])
    assert not code_index.symbols_for(root, "new_definition", at=deleted["snapshot"])


def test_historical_capture_cannot_inherit_dirty_untracked_paths(tmp_path):
    repo = repository(tmp_path)
    commit = git(repo, "rev-parse", "HEAD")
    (repo / "private.py").write_text("def dirty_only(): return 1\n")
    root = tmp_path / "state"
    live = code_index.index_repo(root, repo)
    captured = code_build.index_revision(root, repo, commit)
    assert not code_index.symbols_for(root, "dirty_only", at=captured["snapshot"])
    assert code_index.symbols_for(root, "dirty_only", at=live["snapshot"])
    conn = connect(root)
    snapshot = code_snapshot.resolve(conn, captured["snapshot"])
    assert snapshot["files_count"] == 2
    conn.close()


@pytest.mark.parametrize("historical", [False, True])
def test_thirteen_parallel_cold_requests_extract_same_snapshot_once(
    tmp_path, monkeypatch, historical
):
    import threading

    repo = repository(tmp_path)
    (repo / "caller.py").unlink()
    git(repo, "add", ".")
    git(repo, "commit", "-m", "one cold source object")
    root = tmp_path / "state"
    barrier = threading.Barrier(13)
    count = 0
    guard = threading.Lock()
    extract = code_build.code_extract.extract

    def measured(text, path):
        nonlocal count
        with guard:
            count += 1
        # Source preparation must still leave SQLite's writer available.
        probe = connect(root)
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()
        probe.close()
        return extract(text, path)

    monkeypatch.setattr(code_build.code_extract, "extract", measured)

    def request():
        barrier.wait(timeout=15)
        return (
            code_build.index_revision(root, repo, "HEAD")
            if historical
            else code_index.index_repo(root, repo)
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=13) as pool:
        futures = [pool.submit(request) for _ in range(13)]
        results = [future.result(timeout=30) for future in futures]
    assert count == 1
    assert len({r["snapshot"] for r in results}) == 1
    assert sum(r["changed"] for r in results) == 1
    assert len(list((root / "state/code-index/preparation-locks").iterdir())) == 1


@pytest.mark.parametrize("historical", [False, True])
def test_thirteen_cold_shared_service_requests_use_one_extraction(
    tmp_path, monkeypatch, historical
):
    import tempfile
    import threading

    from hx import repo_service

    repo = repository(tmp_path)
    (repo / "caller.py").unlink()
    git(repo, "add", ".")
    git(repo, "commit", "-m", "one shared cold source")
    root = tmp_path / "service"
    root.mkdir()
    counter = 0
    guard = threading.Lock()
    extract = code_build.code_extract.extract

    def measured(text, path):
        nonlocal counter
        with guard:
            counter += 1
        return extract(text, path)

    monkeypatch.setattr(code_build.code_extract, "extract", measured)
    barrier = threading.Barrier(13)
    argv = ["index", "--repo", str(repo)] + (["--at", "HEAD"] if historical else [])
    with tempfile.TemporaryDirectory(prefix="hx-cold-shared-", dir="/tmp") as short:
        socket = Path(short) / "service.sock"
        server = repo_service.RepositoryServer(socket, root, workers=4)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:

            def request():
                barrier.wait(timeout=15)
                return repo_service.request(socket, dict(method="code", argv=argv))

            with concurrent.futures.ThreadPoolExecutor(max_workers=13) as pool:
                futures = [pool.submit(request) for _ in range(13)]
                results = [future.result(timeout=30) for future in futures]
            assert all(r["exit_code"] == 0 for r in results)
            assert counter == 1
            assert len({r["result"]["snapshot"] for r in results}) == 1
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_oversize_fingerprint_streams_without_archiving_or_reading_whole_body(
    tmp_path, monkeypatch
):
    repo = repository(tmp_path)
    oversized = repo / "large.py"
    oversized.write_text("x" * 8192)
    read = Path.read_bytes

    def bounded(path):
        if path == oversized:
            pytest.fail("oversize body was read wholesale")
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", bounded)
    root = tmp_path / "state"
    result = code_index.index_repo(root, repo, max_file_bytes=128)
    assert result["skipped"] == 1
    assert code_freshness.scope(root, repo, ["large.py"])["fresh"]
    conn = connect(root)
    excluded = conn.execute(
        'SELECT sha256,size FROM exclusions WHERE path="large.py"'
    ).fetchone()
    assert excluded["size"] == 8192
    assert not conn.execute(
        "SELECT 1 FROM source_objects WHERE sha=?", (excluded["sha256"],)
    ).fetchone()
    conn.close()


def test_live_absence_requires_complete_snapshot_after_scoped_bootstrap(tmp_path):
    repo = repository(tmp_path)
    (repo / "hidden.py").write_text("class Hidden: pass\n")
    root = tmp_path / "state"
    code_index.index_repo(root, repo, paths=["item.py"])
    status, result = code_index.execute(["type", "Hidden", "--expect", "absent"], root)
    assert status == 3 and result == []
    status, result = code_index.execute(
        ["callers", "Missing", "--count", "--expect", "absent"], root
    )
    assert status == 3 and result["count"] == 0
    status, result = code_index.execute(
        ["type", "Missing", "--namespace", "unindexed", "--expect", "absent"], root
    )
    assert status == 3 and result == []
    code_index.index_repo(root, repo)
    status, result = code_index.execute(["type", "Hidden", "--expect", "absent"], root)
    assert status == 3 and result
    status, result = code_index.execute(["type", "Missing", "--expect", "absent"], root)
    assert status == 0 and result == []


@pytest.mark.parametrize("shared", [False, True])
def test_live_absence_requires_current_source_but_historical_proof_is_pinned(
    tmp_path, shared
):
    from hx import repo_service

    repo = repository(tmp_path)
    root = tmp_path / "state"
    original = code_index.index_repo(root, repo)

    def absent(name, *options):
        argv = ["type", name, "--expect", "absent", *options]
        if shared:
            answer = repo_service.dispatch(root, dict(method="code", argv=argv))
            return answer["exit_code"], answer["result"]
        return code_index.execute(argv, root)

    assert absent("Hidden") == (0, [])
    (repo / "item.py").write_text("class Hidden: pass\n")
    assert absent("Hidden") == (3, [])
    assert absent("Hidden", "--at", original["head"]) == (0, [])
    code_index.index_repo(root, repo)
    (repo / "new.py").write_text("class NewHidden: pass\n")
    assert absent("NewHidden") == (3, [])
    git(repo, "add", ".")
    git(repo, "commit", "-m", "new source not indexed")
    assert absent("NewHidden") == (3, [])


@pytest.mark.parametrize("partial_bootstrap", [False, True])
def test_full_natural_refresh_rebinds_cached_caller_after_provider_appears(
    tmp_path, monkeypatch, partial_bootstrap
):
    repo = repository(tmp_path)
    (repo / "RUN.NSP").write_text(
        "DEFINE DATA LOCAL\n1 #TARGET (A8)\nEND-DEFINE\n"
        "CALLNAT 'UTIL'\nCALLNAT #TARGET\nEND\n"
    )
    provider = "DEFINE DATA PARAMETER\n1 #VALUE (I4) OPTIONAL\nEND-DEFINE\nEND\n"
    if partial_bootstrap:
        (repo / "UTIL.NSN").write_text(provider)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Natural caller")
    root = tmp_path / "state"
    code_index.index_repo(root, repo, paths=["RUN.NSP"] if partial_bootstrap else None)
    if not partial_bootstrap:
        (repo / "UTIL.NSN").write_text(provider)
    extract = code_build.code_extract.extract
    parsed = []

    def measured(text, path):
        parsed.append(path)
        conn = connect(root)
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        conn.close()
        return extract(text, path)

    monkeypatch.setattr(code_build.code_extract, "extract", measured)
    result = code_index.index_repo(root, repo)
    assert "RUN.NSP" not in parsed and "UTIL.NSN" in parsed
    assert result["reused_extractions"] >= 1
    assert code_freshness.scope(root, repo, ["."], local=True)["fresh"]
    assert code_index.execute(["type", "Missing", "--expect", "absent"], root) == (
        0,
        [],
    )
    callers = code_index.callers(root, "UTIL")
    assert [r["path"] for r in callers] == ["RUN.NSP"]
    assert all(not r["stale"] for r in callers)
