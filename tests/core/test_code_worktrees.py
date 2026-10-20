"""Every graph read answers for the checkout the command runs in (D1).

Two worktrees of one repository share a namespace, source objects and extractions,
but never each other's answers: search, absence, freshness, revisions and imports
resolve against the caller's checkout, and indexing a clean sibling of a dirty
worktree converges in one full pass.
"""

import json
import subprocess

import pytest

from hx import code_build, code_freshness, code_index, repo_service
from hx.code_store import connect, namespace


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    )
    return result.stdout.decode().strip()


@pytest.fixture
def worktrees(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "remote", "add", "origin", "https://example.test/worktrees.git")
    (repo / "item.py").write_text('def shared():\n    return "shared-marker"\n')
    (repo / "caller.py").write_text("from item import shared\nshared()\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "first")
    a, b = tmp_path / "a", tmp_path / "b"
    git(repo, "worktree", "add", "--detach", str(a))
    git(repo, "worktree", "add", "--detach", str(b))
    assert git(a, "rev-parse", "HEAD") == git(b, "rev-parse", "HEAD")
    # A is dirty: one tracked edit and one untracked declaration.
    (a / "item.py").write_text('def shared():\n    return "aeditmarker"\n')
    (a / "dirty.py").write_text('class OnlyInA:\n    marker = "onlyinamarker"\n')
    return tmp_path / "state", a.resolve(), b.resolve()


def run(monkeypatch, where, *argv):
    monkeypatch.chdir(where)
    return code_index.execute(list(argv), where.parent / "state")


def paths(result):
    return {r["path"] for r in result}


def test_reads_answer_for_the_worktree_the_command_runs_in(worktrees, monkeypatch):
    root, a, b = worktrees
    first_a = code_index.index_repo(root, a)
    first_b = code_index.index_repo(root, b)
    # B differs from A's dirty overlay, so it must not inherit A's snapshot.
    assert first_b["changed"] > 0 and first_b["snapshot"] != first_a["snapshot"]
    assert code_index.index_repo(root, b)["changed"] == 0
    assert code_index.index_repo(root, a)["snapshot"] == first_a["snapshot"]
    conn = connect(root)
    roots = {r["root"] for r in conn.execute("SELECT root FROM repos")}
    conn.close()
    assert roots == {str(a), str(b)}

    # search
    status, hits = run(monkeypatch, a, "search", "onlyinamarker")
    assert status == 0 and "dirty.py" in paths(hits)
    status, hits = run(monkeypatch, b, "search", "onlyinamarker")
    assert status == 0 and "dirty.py" not in paths(hits)
    status, hits = run(monkeypatch, a, "search", "aeditmarker")
    assert status == 0 and "item.py" in paths(hits)
    status, hits = run(monkeypatch, b, "search", "shared-marker")
    assert status == 0 and "item.py" in paths(hits)
    assert all(not r["stale"] for r in hits)

    # --expect absent
    assert run(monkeypatch, b, "type", "OnlyInA", "--expect", "absent") == (0, [])
    status, rows = run(monkeypatch, a, "type", "OnlyInA", "--expect", "absent")
    assert status == 3 and paths(rows) == {"dirty.py"}
    assert not rows[0]["stale"]

    # --at HEAD resolves the caller's HEAD to its clean commit, never an overlay.
    for where in (a, b):
        assert run(monkeypatch, where, "type", "OnlyInA", "--at", "HEAD") == (0, [])
        status, hits = run(monkeypatch, where, "search", "aeditmarker", "--at", "HEAD")
        assert status == 0 and "item.py" not in paths(hits)

    # fresh: each worktree reports its own scope.
    for where in (a, b):
        status, result = run(monkeypatch, where, "fresh", ".", "--expect", "fresh")
        assert status == 0 and result["fresh"], (where, result)
    (b / "caller.py").write_text("from item import shared\nshared()\nshared()\n")
    status, result = run(monkeypatch, b, "fresh", "caller.py", "--expect", "fresh")
    assert status == 3 and result["paths"] == ["caller.py"]
    status, result = run(monkeypatch, a, "fresh", "caller.py", "--expect", "fresh")
    assert status == 0 and result["fresh"]
    assert code_freshness.scope(root, a, ["."], local=True)["fresh"]
    # A stale caller cannot prove absence; the clean sibling still can.
    assert run(monkeypatch, b, "type", "Missing", "--expect", "absent")[0] == 3
    assert run(monkeypatch, a, "type", "Missing", "--expect", "absent") == (0, [])
    status, rows = run(monkeypatch, b, "symbols", "shared")
    assert status == 0 and any(r["path"] == "item.py" and not r["stale"] for r in rows)
    assert code_index.index_repo(root, b, paths=["caller.py"])["changed"] == 1
    assert run(monkeypatch, b, "fresh", ".", "--expect", "fresh")[0] == 0

    # An explicit checkout overrides the working directory; none is ambiguous.
    neutral = root.parent / "elsewhere"
    neutral.mkdir()
    status, rows = run(monkeypatch, neutral, "type", "OnlyInA", "--repo", str(a))
    assert status == 0 and paths(rows) == {"dirty.py"}
    assert run(monkeypatch, neutral, "type", "OnlyInA", "--repo", str(b)) == (0, [])
    status, error = run(monkeypatch, neutral, "type", "OnlyInA")
    assert status == 2 and "--repo" in error["error"]
    assert paths(code_index.symbols_for(root, "OnlyInA", checkout=a)) == {"dirty.py"}
    assert code_index.symbols_for(root, "OnlyInA", checkout=b) == []

    # --at HEAD follows each worktree's own HEAD after they diverge.
    (b / "newer.py").write_text("class OnlyInBCommit:\n    pass\n")
    git(b, "add", ".")
    git(b, "commit", "-m", "b only")
    code_index.index_repo(root, b)
    status, rows = run(monkeypatch, b, "type", "OnlyInBCommit", "--at", "HEAD")
    assert status == 0 and paths(rows) == {"newer.py"}
    assert run(monkeypatch, a, "type", "OnlyInBCommit", "--at", "HEAD") == (0, [])
    assert run(monkeypatch, a, "type", "OnlyInBCommit") == (0, [])
    assert run(monkeypatch, a, "callers", "shared", "--count")[1]["count"] == 1


def test_import_binds_the_callers_checkout(worktrees, monkeypatch):
    root, a, b = worktrees
    code_index.index_repo(root, a)
    code_index.index_repo(root, b)
    identity = namespace(a)
    symbols = root.parent / "symbols.jsonl"
    edges = root.parent / "edges.jsonl"
    symbols.write_text(
        json.dumps(
            dict(
                name="Imported",
                qualified="Imported",
                kind="function",
                path="item.py",
                line=1,
                end_line=2,
                signature="imported declaration",
            )
        )
        + "\n"
    )
    edges.write_text("")
    # The last indexer was B; an import run in A anchors and publishes for A.
    status, result = run(
        monkeypatch, a, "import", "--namespace", identity,
        "--symbols", str(symbols), "--edges", str(edges),
    )
    assert status == 0, result
    status, rows = run(monkeypatch, a, "symbols", "Imported")
    assert status == 0 and len(rows) == 1
    assert rows[0]["lane"] == "imported" and not rows[0]["unanchored"]
    assert not rows[0]["stale"]
    assert run(monkeypatch, b, "symbols", "Imported") == (0, [])
    status, result = run(
        monkeypatch, b, "import", "--namespace", identity, "--repo", str(b),
        "--symbols", str(symbols), "--edges", str(edges),
    )
    assert status == 0, result
    status, rows = run(monkeypatch, b, "symbols", "Imported")
    assert status == 0 and len(rows) == 1 and not rows[0]["stale"]
    assert rows[0]["anchor_sha"] != run(monkeypatch, a, "symbols", "Imported")[1][0]["anchor_sha"]


def test_shared_service_queries_carry_the_client_checkout(worktrees, monkeypatch):
    root, a, b = worktrees
    code_index.index_repo(root, a)
    code_index.index_repo(root, b)
    instance = root.parent / "instance"
    instance.mkdir()
    socket = root.parent / "service.sock"
    monkeypatch.setattr(repo_service, "configured_socket", lambda r, env=None: socket)
    # The server process runs elsewhere; only the forwarded argv names the caller.
    served = []

    def request(path, body):
        served.append(body["argv"])
        monkeypatch.chdir(instance)
        status, result = code_index.execute(body["argv"], root, read_ledger=False)
        return {"exit_code": status, "result": result}

    monkeypatch.setattr(repo_service, "request", request)
    monkeypatch.chdir(a)
    answer = repo_service.code_request(instance, ["type", "OnlyInA"])
    assert answer["exit_code"] == 0 and paths(answer["result"]) == {"dirty.py"}
    assert served[-1][-2:] == ["--repo", str(a)]
    monkeypatch.chdir(b)
    answer = repo_service.code_request(instance, ["type", "OnlyInA", "--expect", "absent"])
    assert answer == {"exit_code": 0, "result": []}
    monkeypatch.chdir(b)
    answer = repo_service.code_request(instance, ["index", "--repo", str(b)])
    assert "--repo" in served[-1] and served[-1].count("--repo") == 1


def test_index_converges_when_a_dirty_sibling_indexed_last(worktrees):
    root, a, b = worktrees
    code_index.index_repo(root, b)
    clean = code_index.index_repo(root, b)
    code_index.index_repo(root, a)
    # Reverting A's edit must converge too: the overlay names every dirty path.
    (a / "dirty.py").unlink()
    git(a, "checkout", "--", "item.py")
    reverted = code_index.index_repo(root, a)
    assert reverted["changed"] >= 2 and reverted["snapshot"] == clean["snapshot"]
    assert code_freshness.scope(root, a, ["."], local=True)["fresh"]
    assert code_build.index_repo(root, b)["snapshot"] == clean["snapshot"]
