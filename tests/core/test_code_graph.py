"""Graph contracts: namespaces, provenance, scale, lints and manifest facts."""

import json
import subprocess
from pathlib import Path
import pytest
from hx import code_index
from hx.code_store import connect, namespace


def git(repo, *args):
    return (
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )


def make_repo(tmp_path, name="repo"):
    p = tmp_path / name
    p.mkdir()
    git(p, "init")
    git(p, "config", "user.email", "test@example.com")
    git(p, "config", "user.name", "Test")
    (p / "a.cs").write_text(
        "namespace Acme;\npublic interface IFoo { int Run(string name); }\npublic class Foo : IFoo { public int Run(string value) { return 1; } }\n"
    )
    (p / "app.cs").write_text('services.AddScoped<IFoo,Foo>();\nnew Foo().Run("x");\n')
    (p / "test.cs").write_text(
        '[TestFixture]\nclass FooTests { [Test] public void Works() { new Foo().Run("x"); } }\n'
    )
    git(p, "add", ".")
    git(p, "commit", "-m", "fixture")
    return p


def test_namespace_isolation_and_incremental(tmp_path):
    root = tmp_path / "root"
    a = make_repo(tmp_path, "a")
    b = make_repo(tmp_path, "b")
    code_index.index_repo(root, a)
    code_index.index_repo(root, b)
    assert len(code_index.symbols_for(root, "IFOO")) == 2
    assert len(code_index.symbols_for(root, "ifoo", repo=namespace(a))) == 1
    (a / "a.cs").unlink()
    code_index.index_repo(root, a)
    assert len(code_index.symbols_for(root, "IFoo")) == 1
    assert code_index.index_repo(root, b)["changed"] == 0


def test_no_cap_and_single_body_external_fts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "root"
    for i in range(2005):
        (repo / f"{i}.py").write_text("def hello(): return 1\n")
    assert code_index.index_repo(root, repo)["files"] == 2005
    conn = connect(root)
    assert (
        "content='files'"
        in conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='code_search'"
        ).fetchone()[0]
    )
    conn.close()


def test_provenance_and_dirty_staleness(tmp_path):
    repo = make_repo(tmp_path)
    root = tmp_path / "root"
    code_index.index_repo(root, repo)
    row = code_index.symbols_for(root, "Acme.Foo.Run")[0]
    assert row["signature"] == "public int Run(string value)"
    assert row["lane"] == "syntax" and not row["stale"]
    (repo / "a.cs").write_text(
        (repo / "a.cs").read_text().replace("return 1", "return 2")
    )
    assert code_index.symbols_for(root, "Acme.Foo.Run")[0]["stale"]


@pytest.mark.parametrize(
    "expect,query,expected",
    [
        ("exists", "Foo", 0),
        ("exists", "Missing", 3),
        ("absent", "Missing", 0),
        ("absent", "Foo", 3),
        ("one", "Acme.Foo", 0),
        ("one", "Run", 3),
        ("fresh", "Foo", 0),
        ("fresh", "Missing", 3),
    ],
)
def test_lint_modes(tmp_path, capsys, expect, query, expected):
    repo = make_repo(tmp_path)
    root = tmp_path / "root"
    code_index.index_repo(root, repo)
    assert code_index.main(["signature", query, "--expect", expect], root) == expected
    assert json.loads(capsys.readouterr().out) is not None


def test_project_manifests_and_text_fallback(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "root"
    (repo / "A.csproj").write_text(
        '<Project><ItemGroup><ProjectReference Include="B.csproj"/><PackageReference Include="Microsoft.NET.Test.Sdk" Version="1"/></ItemGroup></Project>'
    )
    (repo / "B.csproj").write_text("<Project/>")
    (repo / "legacy.nat").write_text("LEGACY-NATURAL-CAPABILITY\n")
    code_index.index_repo(root, repo)
    assert code_index.search(root, "LEGACY-NATURAL-CAPABILITY")
    assert code_index.main(["projects", "A"], root) == 0
    project = json.loads(capsys.readouterr().out)[0]
    assert (
        project["data"]["test_project"]
        and project["dependencies"][0]["dst_text"] == "B.csproj"
    )


def test_state_never_written_in_product(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="outside"):
        code_index.index_repo(repo, repo)
    assert not (repo / "state").exists()


def test_reuse_queries_and_parity(tmp_path, capsys):
    repo = make_repo(tmp_path)
    root = tmp_path / "root"
    code_index.index_repo(root, repo)
    for command, name in [
        ("type", "ifoo"),
        ("signature", "Acme.Foo.Run"),
        ("register", "IFoo"),
        ("tests-for", "Foo"),
        ("precedent", "AddScoped"),
    ]:
        assert code_index.main([command, name, "--expect", "exists"], root) == 0
        assert json.loads(capsys.readouterr().out)
    assert code_index.main(["conform", "IFoo", "--expect", "clean"], root) == 0
    assert json.loads(capsys.readouterr().out)["clean"]
    (repo / "a.cs").write_text(
        (repo / "a.cs")
        .read_text()
        .replace("public int Run(string value)", "public int Run(int value)")
    )
    code_index.index_repo(root, repo)
    assert code_index.main(["conform", "IFoo", "--expect", "clean"], root) == 3
    assert json.loads(capsys.readouterr().out)["diffs"][0]["kind"] == "signature"


def test_count_and_usage_errors(tmp_path, capsys):
    repo = make_repo(tmp_path)
    root = tmp_path / "root"
    code_index.index_repo(root, repo)
    assert (
        code_index.main(["callers", "Run", "--count", "--expect", "exists"], root) == 0
    )
    assert json.loads(capsys.readouterr().out)["count"] == 2
    assert code_index.main(["type", "Foo", "--limit", "101"], root) == 2


def test_contract_registry_shared_namespaces(tmp_path, capsys):
    a = make_repo(tmp_path, "source")
    b = make_repo(tmp_path, "target")
    root = tmp_path / "root"
    code_index.index_repo(root, a)
    code_index.index_repo(root, b)
    records = [
        dict(
            name="IFoo",
            path="a.cs",
            version="1",
            producer="source-unit" if p == a else "target-unit",
            repo=namespace(p),
            sha=git(p, "rev-parse", "HEAD"),
        )
        for p in [a, b]
    ]
    file = tmp_path / "contracts.json"
    file.write_text(json.dumps(records))
    assert code_index.main(["contracts", "--register", str(file)], root) == 0
    capsys.readouterr()
    assert code_index.main(["contract", "ifoo", "--expect", "exists"], root) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["producer"] for r in rows} == {"source-unit", "target-unit"}
    assert all(r["sha"] and not r["stale"] for r in rows)
    assert code_index.main(["contract", "ifoo", "--expect", "absent"], root) == 3


def test_ownership_read_through_without_graph_columns(tmp_path, capsys):
    from hx.continuity_store import ContinuityStore

    root = tmp_path / "root"
    repo = make_repo(tmp_path)
    code_index.index_repo(root, repo)
    with ContinuityStore(root) as ledger:
        with ledger.transaction() as tx:
            tx.db.execute("INSERT INTO task_heads VALUES('T',1)")
            tx.db.execute("INSERT INTO tasks VALUES('T',1,NULL,'{}',0)")
            tx.db.execute(
                "INSERT INTO runs VALUES('R','T',1,'eng-001',0,'working',NULL,NULL,0,NULL)"
            )
            tx.db.execute(
                "INSERT INTO leases VALUES(?,?,?,?)",
                (namespace(repo), "a.cs", "R", "{}"),
            )
        assert code_index.main(["type", "Foo", "--owners"], root) == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["owners"][0]["worker"] == "eng-001"
        with ledger.transaction() as tx:
            tx.db.execute("DELETE FROM leases WHERE run_id='R'")
        assert code_index.main(["type", "Foo", "--owners"], root) == 0
        assert json.loads(capsys.readouterr().out)[0]["owners"] == []
    conn = connect(root)
    for table in ["symbols", "refs", "edges"]:
        assert "owner" not in {
            r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")
        }
    conn.close()


def test_fresh_scope_query_and_dependency_invalidation(tmp_path, capsys):
    repo = make_repo(tmp_path)
    root = tmp_path / "root"
    code_index.index_repo(root, repo)
    assert code_index.main(["fresh", "a.cs", "--expect", "fresh"], root) == 0
    capsys.readouterr()
    (repo / "a.cs").write_text(
        (repo / "a.cs").read_text().replace("return 1", "return 2")
    )
    assert code_index.main(["fresh", "a.cs", "--expect", "fresh"], root) == 3
    capsys.readouterr()
    code_index.index_repo(root, repo, paths=["a.cs"])
    assert any(r["stale"] for r in code_index.callers(root, "Run"))
    assert code_index.main(["fresh", "app.cs", "--expect", "fresh"], root) == 3
    capsys.readouterr()
    code_index.index_repo(root, repo)
    assert code_index.main(["fresh", "app.cs", "--expect", "fresh"], root) == 0


def test_post_merge_trigger_uses_instance_state(tmp_path):
    import os
    import sys

    script = Path(__file__).parents[2] / "src/hx/skeleton/bin/code-index-post-merge"
    repo = make_repo(tmp_path)
    root = tmp_path / "root"
    binary = tmp_path / "hx-wrapper"
    # The interpreter running the tests, not a `.venv` guessed from the checkout: a worktree
    # has none of its own.
    binary.write_text("#!/bin/sh\nexec " + sys.executable + ' -m hx "$@"\n')
    binary.chmod(0o755)
    result = subprocess.run(
        [str(script), "0"],
        cwd=repo,
        env={
            **os.environ,
            "HARNESS_ROOT": str(root),
            "HX_BIN": str(binary),
            "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["files"] == 3
    assert not (repo / "state").exists()


def test_porter_only_persists_and_trigram_rebuilds_existing_content(tmp_path):
    root = tmp_path / "root"
    repo = make_repo(tmp_path)
    code_index.index_repo(root, repo, trigram=False)
    conn = connect(root)
    assert [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name='code_trigram'"
        )
    ] == []
    conn.close()
    assert code_index.search(root, "Foo")
    code_index.index_repo(root, repo, trigram=True)
    assert code_index.search(root, "AddSco")


def test_extractor_generation_change_refreshes_unchanged_and_invalidates_scope(
    tmp_path, monkeypatch
):
    from hx import code_adapters

    repo = make_repo(tmp_path)
    root = tmp_path / "root"
    code_index.index_repo(root, repo)
    original = code_adapters.detect

    def changed():
        result = original()
        result["operator-generation"] = "new"
        return result

    monkeypatch.setattr(code_adapters, "detect", changed)
    assert code_index.index_repo(root, repo, paths=["a.cs"])["changed"] == 1
    assert code_index.symbols_for(root, "FooTests")[0]["stale"]
    code_index.index_repo(root, repo)
    assert not code_index.symbols_for(root, "FooTests")[0]["stale"]


def test_missing_index_cannot_certify_absence(tmp_path, capsys):
    assert (
        code_index.main(["type", "Absent", "--expect", "absent"], tmp_path / "root")
        == 3
    )
    assert json.loads(capsys.readouterr().out) == []


@pytest.mark.parametrize(
    "member",
    [
        "int Run<T>(T value);",
        "int Run(int value = 1);",
        "int Run(string value); int Run(int value);",
    ],
)
def test_conform_uncertain_generics_defaults_and_overloads(tmp_path, capsys, member):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "root"
    (repo / "a.cs").write_text(
        "interface IFoo { "
        + member
        + " }\nclass Foo : IFoo { public int Run(int input) { return 1; } }\n"
    )
    code_index.index_repo(root, repo)
    assert code_index.main(["conform", "IFoo", "--expect", "clean"], root) == 3
    assert json.loads(capsys.readouterr().out)["uncertain"]


def test_excluded_bodies_keep_scope_fingerprints_and_absence_is_not_certified(
    tmp_path, capsys
):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "root"
    (repo / "image.bin").write_bytes(b"image\x00payload")
    (repo / "large.py").write_text("def Hidden():\n return 1\n")
    with pytest.raises(ValueError, match="large.py.*index aborted"):
        code_index.index_repo(root, repo, max_file_bytes=16)
    result = code_index.index_repo(root, repo)
    assert result["skipped"] == 1
    assert code_index.symbols_for(root, "Hidden")
    assert code_index.main(["fresh", ".", "--expect", "fresh"], root) == 0
    capsys.readouterr()
    assert code_index.main(["symbols", "Hidden", "--expect", "absent"], root) == 3
    capsys.readouterr()
    (repo / "image.bin").write_bytes(b"changed\x00payload")
    assert code_index.main(["fresh", "image.bin", "--expect", "fresh"], root) == 3
    capsys.readouterr()
    code_index.index_repo(root, repo)
    assert code_index.symbols_for(root, "Hidden")
    (repo / "image.bin").unlink()
    code_index.index_repo(root, repo)
    conn = connect(root)
    assert (
        conn.execute(
            "SELECT count(*) FROM exclusions WHERE path='image.bin'"
        ).fetchone()[0]
        == 0
    )
    conn.close()


def test_qualified_unicode_case_folding(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text('class Café:\n def add(self): return "café"\n')
    root = tmp_path / "root"
    code_index.index_repo(root, repo)
    assert code_index.symbols_for(root, "CAFÉ.ADD")[0]["qualified"] == "Café.add"


def test_go_parity_normalizes_named_parameters_and_results():
    from hx.code_reuse import normalize

    expected = normalize("Read(p []byte) (n int, err error)", language="go")
    actual = normalize(
        "func (s *Store) Read(data []byte) (count int, failure error)", language="go"
    )
    assert expected == actual == ("Read([]byte)(int,error)", False)
