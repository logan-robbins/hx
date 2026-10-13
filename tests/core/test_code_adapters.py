import json
import shutil
from pathlib import Path
import pytest
from hx import code_adapters, code_index
from hx.code_store import connect, namespace
from hx.code_scip import ingest
from hx.code_import import import_graph


def test_pinned_tags_format():
    # tree-sitter v0.25.10 cli/src/tags.rs; Point's Display is zero-based (row, column).
    rows = code_adapters.parse_tags(
        "Foo       \t | class   \tdef (0, 0) - (3, 1) `class Foo {`\nRun       \t | call    \tref (2, 3) - (2, 8) `Run()`\n"
    )
    assert (
        rows[0]["line"] == 1 and rows[0]["end_line"] == 4 and rows[1]["role"] == "ref"
    )
    with pytest.raises(ValueError):
        code_adapters.parse_tags("unknown output")


def test_ctags_source_span_merge():
    source = "class Foo {\n public int Run(string name) { return 1; }\n}\n"
    symbols, refs, _ = __import__("hx.code_extract", fromlist=["extract"]).extract(
        source, "a.cs"
    )
    result, _ = code_adapters._merge(
        source,
        "a.cs",
        symbols,
        refs,
        [dict(name="Run", kind="method", line=2, end_line=2, scope="Foo")],
        "universal-ctags",
    )
    assert [s for s in result if s["name"] == "Run"][0][
        "signature"
    ] == "public int Run(string name)"


def test_scip_superset_and_lane_rank(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "root"
    (repo / "a.py").write_text("def login(user):\n return user\n")
    (repo / "b.py").write_text("login(1)\n")
    code_index.index_repo(root, repo)
    conn = connect(root)
    identity = namespace(repo)
    data = {
        "documents": [
            {
                "relative_path": "a.py",
                "symbols": [
                    {
                        "symbol": "scip-python pkg 1 a/login().",
                        "display_name": "login",
                        "signature_documentation": {
                            "text": "def login(user: str) -> str"
                        },
                        "kind": 12,
                    }
                ],
                "occurrences": [
                    {
                        "range": [0, 4, 9],
                        "symbol": "scip-python pkg 1 a/login().",
                        "symbol_roles": 1,
                    }
                ],
            },
            {
                "relative_path": "b.py",
                "occurrences": [
                    {
                        "range": [0, 0, 5],
                        "symbol": "scip-python pkg 1 a/login().",
                        "symbol_roles": 0,
                    }
                ],
            },
        ]
    }
    result = ingest(conn, identity, data)
    conn.commit()
    conn.close()
    assert result["references"] == 1
    rows = code_index.symbols_for(root, "login")
    assert (
        rows[0]["lane"] == "resolved"
        and rows[0]["signature"] == "def login(user: str) -> str"
    )
    assert code_index.callers(root, "login")[0]["path"] == "b.py"
    assert any(r["reference"] == "resolved" for r in code_index.callers(root, "login"))


def test_import_custom_kinds_and_idempotency(tmp_path, capsys):
    root = tmp_path / "root"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.nat").write_text("PROGRAM SOURCE\n")
    code_index.index_repo(root, repo)
    ns = namespace(repo)
    symbol = tmp_path / "symbols.jsonl"
    edge = tmp_path / "edges.jsonl"
    symbol.write_text(
        json.dumps(
            dict(
                name="Source",
                qualified="Source",
                kind="program",
                path="a.nat",
                line=1,
                end_line=1,
                signature="PROGRAM SOURCE",
            )
        )
        + "\n"
        + json.dumps(
            dict(
                name="Missing",
                qualified="Missing",
                kind="program",
                path="absent.nat",
                line=1,
                end_line=1,
                signature="PROGRAM MISSING",
            )
        )
        + "\n"
    )
    edge.write_text(
        "\n".join(
            json.dumps(
                {
                    "from": "Source",
                    "to": "Missing",
                    "kind": kind,
                    "evidence": {"tool": "legacy"},
                }
            )
            for kind in ["CALLS_STATIC", "PERFORMS", "INPUTS_MAP"]
        )
    )
    for _ in range(2):
        import_graph(root, ns, symbol, edge)
    assert code_index.symbols_for(root, "Missing")[0]["unanchored"]
    assert len(code_index.graph_edges(root)) == 3
    assert len(code_index.callers(root, "Missing")) == 3
    assert code_index.main(["signature", "Missing", "--expect", "fresh"], root) == 3
    capsys.readouterr()
    conn = connect(root)
    assert (
        conn.execute("SELECT count(*) FROM symbols WHERE lane='imported'").fetchone()[0]
        == 2
    )
    conn.close()


@pytest.mark.parametrize("tool", ["tree-sitter", "ctags", "scip"])
def test_optional_live_binary_detection(tool):
    detected = code_adapters.detect()
    if tool == "scip":
        if not detected["scip"] or not any(detected["indexers"].values()):
            pytest.skip("SCIP CLI/language indexer not installed")
    elif not detected[tool]["available"]:
        pytest.skip(tool + " prerequisites/version not installed")
    assert shutil.which(tool)
    if tool == "ctags":
        import tempfile
        from hx.code_extract import extract

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            text = (
                "public class Fixture { public int Run(int value) { return value; } }\n"
            )
            (repo / "fixture.cs").write_text(text)
            detected["tree-sitter"]["available"] = False
            symbols, refs, _ = extract(text, "fixture.cs")
            actual, _ = code_adapters.syntax(
                repo, "fixture.cs", text, symbols, refs, detected
            )
            assert any(s["extractor"] == "universal-ctags" for s in actual)


def test_scip_local_definitions_typed_ranges_and_atomic_rejection(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "root"
    (repo / "a.py").write_text("def calculate():\n    value = 1\n    return value\n")
    code_index.index_repo(root, repo)
    conn = connect(root)
    ns = namespace(repo)
    data = {
        "documents": [
            {
                "relativePath": "a.py",
                "occurrences": [
                    {
                        "symbol": "scip-python pkg 1 a/calculate().",
                        "symbolRoles": 1,
                        "singleLineRange": {
                            "line": 0,
                            "startCharacter": 4,
                            "endCharacter": 13,
                        },
                    },
                    {"symbol": "local 0", "symbolRoles": 1, "range": [1, 4, 9]},
                    {"symbol": "local 0", "symbolRoles": 0, "range": [2, 11, 16]},
                ],
            }
        ]
    }
    ingest(conn, ns, data)
    conn.commit()
    names = [
        r[0] for r in conn.execute("SELECT name FROM symbols WHERE lane='resolved'")
    ]
    assert names.count("calculate") == 1 and "value" in names
    before = conn.execute(
        "SELECT count(*) FROM symbols WHERE lane='resolved'"
    ).fetchone()[0]
    broken = {
        "documents": [
            {
                "relative_path": "a.py",
                "occurrences": [{"symbol": "broken", "symbol_roles": 1, "range": []}],
            }
        ]
    }
    with pytest.raises(ValueError):
        ingest(conn, ns, broken)
    assert (
        conn.execute("SELECT count(*) FROM symbols WHERE lane='resolved'").fetchone()[0]
        == before
    )
    conn.close()


def test_external_tag_merge_preserves_same_line_overloads():
    from hx.code_extract import extract

    source = "interface IFoo { int Run(string name); int Run(int name); }"
    symbols, refs, _ = extract(source, "a.cs")
    rows = [dict(name="Run", kind="method", line=1, end_line=1, role="def")]
    merged, _ = code_adapters._merge(
        source, "a.cs", symbols, refs, rows, "tree-sitter-0.25.10"
    )
    assert len([r for r in merged if r["name"] == "Run"]) == 2


def test_ctags_wrapper_json_fixture_and_safe_degradation(tmp_path, monkeypatch):
    from hx.code_extract import extract

    source = "class Fixture { public int Run(int value) { return value; } }\n"
    (tmp_path / "a.cs").write_text(source)
    symbols, refs, _ = extract(source, "a.cs")
    detected = {
        "tree-sitter": {"available": False},
        "ctags": {"available": True, "binary": "ctags"},
    }
    calls = []

    def output(argv, **kwargs):
        calls.append(argv)
        return json.dumps(
            {
                "_type": "tag",
                "name": "Run",
                "kind": "method",
                "line": 1,
                "scope": "Fixture",
                "end": 1,
            }
        )

    monkeypatch.setattr(code_adapters, "run", output)
    actual, _ = code_adapters.syntax(tmp_path, "a.cs", source, symbols, refs, detected)
    assert any(s["extractor"] == "universal-ctags" for s in actual)
    assert "--output-format=json" in calls[0]
    monkeypatch.setattr(code_adapters, "run", lambda *a, **k: "unsupported format")
    degraded, _ = code_adapters.syntax(
        tmp_path, "a.cs", source, symbols, refs, detected
    )
    assert all(s["extractor"] == "stdlib-csharp" for s in degraded)
