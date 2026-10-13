"""Natural source fixtures exercise graph behavior without a Natural runtime."""

import json

import pytest

from hx import appmap, code_index, code_natural
from hx.code_extract import extract
from hx.code_store import connect, namespace, span_sha
from hx.errors import ValidationError

FUNCTION = """* A function whose public name differs from its object name.
DEFINE FUNCTION F#TOTAL
RETURNS #RESULT (I4) BY VALUE
DEFINE DATA PARAMETER
1 #AMOUNT (I4) BY VALUE
1 #FACTOR (I4) BY VALUE OPTIONAL
LOCAL
1 #TEMP (I4)
END-DEFINE
#RESULT := #AMOUNT * #FACTOR
END-FUNCTION
END
"""


def by_name(rows, qualified):
    return next(r for r in rows if r["qualified"] == qualified)


def test_natural_function_signature_and_public_name():
    rows, _, _ = extract(FUNCTION, "TOTAL.NS7")
    function = by_name(rows, "F#TOTAL")
    assert function["name"] == "F#TOTAL" and function["kind"] == "function"
    assert function["signature"] == (
        "DEFINE FUNCTION F#TOTAL RETURNS #RESULT (I4) BY VALUE DEFINE DATA PARAMETER "
        "1 #AMOUNT (I4) BY VALUE 1 #FACTOR (I4) BY VALUE OPTIONAL LOCAL 1 #TEMP (I4) END-DEFINE"
    )
    assert (function["line"], function["end_line"]) == (2, 11)
    assert function["anchor_sha"] == span_sha(FUNCTION, 2, 11)
    assert (
        by_name(rows, "F#TOTAL.#FACTOR")["signature"]
        == "1 #FACTOR (I4) BY VALUE OPTIONAL"
    )
    assert by_name(rows, "F#TOTAL.#AMOUNT")["kind"] == "parameter"
    assert by_name(rows, "F#TOTAL.#TEMP")["kind"] == "variable"
    same_name, _, _ = extract(FUNCTION, "F#TOTAL.ns7")
    assert len([r for r in same_name if r["name"] == "F#TOTAL"]) == 1


def test_natural_numbered_source_hierarchies_and_nested_subroutines():
    source = """0010 DEFINE DATA LOCAL
0020 1 #GROUP
0030   2 #VALUE (A10)
0040     INIT <'hello'>
0050 1 #OTHER (I4)
0060 END-DEFINE
0070 PERFORM OUTER
0080 DEFINE SUBROUTINE OUTER
0090   PERFORM INNER
0100   DEFINE SUBROUTINE INNER
0110     WRITE #GROUP.#VALUE
0120   END-SUBROUTINE
0130 END-SUBROUTINE
0140 END
"""
    rows, refs, _ = extract(source, "ENTRY.nsp")
    group = by_name(rows, "ENTRY.#GROUP")
    assert (group["line"], group["end_line"]) == (2, 4)
    field = by_name(rows, "ENTRY.#GROUP.#VALUE")
    assert field["signature"] == "2 #VALUE (A10) INIT <'hello'>"
    assert field["column"] == 7 and field["anchor_sha"] == span_sha(source, 3, 4)
    outer = by_name(rows, "ENTRY.OUTER")
    inner = by_name(rows, "ENTRY.OUTER.INNER")
    assert outer["end_line"] == 13 and inner["end_line"] == 12
    assert outer["signature"] == "DEFINE SUBROUTINE OUTER"
    assert [(r["name"], r["line"]) for r in refs] == [("OUTER", 7), ("INNER", 9)]


def test_natural_comments_literals_arrays_and_dynamic_calls():
    source = """** CALLNAT 'FAKE1'
* DEFINE SUBROUTINE FAKE2
/* FETCH 'FAKE3'
WRITE 'CALLNAT ''FAKE4''' "PERFORM FAKE5"
WRITE '/* literal' *DATX
WRITE #ARRAY(1/*) /* CALLNAT 'FAKE6'
CALLNAT 'PAY-ONE'
CALLNAT #TARGET
CALLNAT TARGET
FETCH RETURN "MENU"
FETCH REPEAT #NEXT
PERFORM CHECK-INPUT
DEFINE PROTOTYPE VARIABLE #FCT
RETURNS (I4)
END-PROTOTYPE
WRITE #FCT(<1>) F#TOTAL(<2,3>)
END
"""
    rows, refs, _ = extract(source, "RUN.nat")
    assert not any(r["name"].startswith("FAKE") for r in rows + refs)
    assert [(r["name"], r["kind"]) for r in refs] == [
        ("PAY-ONE", "calls"),
        ("#TARGET", "dynamic_calls"),
        ("TARGET", "dynamic_calls"),
        ("MENU", "calls"),
        ("#NEXT", "dynamic_calls"),
        ("CHECK-INPUT", "calls"),
        ("#FCT", "dynamic_calls"),
        ("F#TOTAL", "calls"),
    ]
    clean = code_natural.source(source)
    assert len(clean) == len(source) and "#ARRAY(1/*)" in clean
    assert "*DATX" in clean and "/* literal" in clean


@pytest.mark.parametrize("suffix,kind", list(code_natural.OBJECT_KINDS.items()))
def test_natural_source_extensions(suffix, kind):
    rows, _, _ = extract(
        "DEFINE DATA LOCAL\n1 #VALUE (A10)\nEND-DEFINE\nEND\n",
        "OBJECT" + suffix.upper(),
    )
    expected = "natural_object" if kind in {"function", "subroutine"} else kind
    assert rows[0]["kind"] == expected
    assert rows[0]["extractor"] == "stdlib-natural"


def test_external_subroutine_name_and_reporting_return():
    source = "DEFINE SUBROUTINE CHECK-INPUT\n WRITE 'ok'\nRETURN\nEND\n"
    rows, _, _ = extract(source, "CHECK.nss")
    routine = by_name(rows, "CHECK-INPUT")
    assert routine["kind"] == "subroutine" and routine["end_line"] == 3
    broken, _, _ = extract(source.replace("RETURN", "WRITE 'still open'"), "CHECK.nss")
    assert not any(r["kind"] == "subroutine" for r in broken)


def test_inline_subroutine_with_same_name_preserves_program_object():
    rows, _, _ = extract(
        "PERFORM RUN\nDEFINE SUBROUTINE RUN\n IGNORE\nEND-SUBROUTINE\nEND\n", "RUN.NSP"
    )
    assert by_name(rows, "RUN")["kind"] == "program"
    assert by_name(rows, "RUN.RUN")["kind"] == "subroutine"


def test_fetch_return_modifier_does_not_end_subroutine():
    source = "DEFINE SUBROUTINE SHOW-MENU\n FETCH RETURN REPEAT 'MENU'\n WRITE 'returned'\nEND-SUBROUTINE\nEND\n"
    rows, refs, _ = extract(source, "SHOW.NSS")
    assert by_name(rows, "SHOW-MENU")["end_line"] == 4
    assert [(r["name"], r["kind"]) for r in refs] == [("MENU", "calls")]


def test_external_subroutine_parameters_and_optional_subroutine_keyword():
    source = "DEFINE DATA PARAMETER\n1 #VALUE (I4) OPTIONAL\nEND-DEFINE\nDEFINE CHECK-INPUT\n WRITE #VALUE\nRETURN\nEND\n"
    rows, _, _ = extract(source, "CHECK.NSS")
    routine = by_name(rows, "CHECK-INPUT")
    assert (
        routine["signature"]
        == "DEFINE DATA PARAMETER 1 #VALUE (I4) OPTIONAL END-DEFINE DEFINE CHECK-INPUT"
    )
    assert (routine["line"], routine["end_line"]) == (1, 6)
    assert by_name(rows, "CHECK-INPUT.#VALUE")["kind"] == "parameter"


def test_external_subroutine_signature_includes_data_before_prototype():
    source = "DEFINE DATA PARAMETER\n1 #VALUE (I4)\nEND-DEFINE\nDEFINE PROTOTYPE F#TEXT\nRETURNS (A10)\nEND-PROTOTYPE\nDEFINE SUBROUTINE CHECK-INPUT\n WRITE F#TEXT(<>)\nEND-SUBROUTINE\nEND\n"
    rows, _, _ = extract(source, "CHECK.NSS")
    routine = by_name(rows, "CHECK-INPUT")
    assert routine["signature"].startswith(
        "DEFINE DATA PARAMETER 1 #VALUE (I4) END-DEFINE"
    )
    assert by_name(rows, "CHECK-INPUT.#VALUE")["kind"] == "parameter"


def test_wrapped_return_layout_and_casefolded_crlf_anchor(tmp_path):
    source = "DEFINE FUNCTION F#TEXT\nRETURNS\n #RESULT\n (A)\n DYNAMIC\nDEFINE DATA PARAMETER\n1 #VALUE (A10)\nEND-DEFINE\n#RESULT := #VALUE\nEND-FUNCTION\nEND\n".replace(
        "\n", "\r\n"
    )
    rows, _, _ = extract(source, "TEXT.NS7")
    function = by_name(rows, "F#TEXT")
    assert function["signature"].startswith(
        "DEFINE FUNCTION F#TEXT RETURNS #RESULT (A) DYNAMIC DEFINE DATA PARAMETER"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "TEXT.NS7").write_bytes(source.encode())
    root = tmp_path / "instance"
    code_index.index_repo(root, repo)
    anchor = appmap.source_anchor(repo, "TEXT.NS7", "f#text", index_root=root)
    assert anchor == appmap.source_anchor(repo, "TEXT.NS7", "f#text")
    assert anchor["sha256"] == span_sha(source, 1, 10)


def test_natural_dependencies_data_views_and_layout_sources():
    source = """DEFINE DATA PARAMETER USING ARGS
LOCAL USING LOCAL-D
LOCAL
1 EMP-VIEW VIEW OF EMPLOYEE
2 FULL-NAME
END-DEFINE
INCLUDE COMMON-C
INCLUDE LANG&
INPUT USING MAP 'SCREEN'
INPUT USING MAP #SCREEN
END
"""
    rows, refs, edges = extract(source, "USE.nsp")
    assert by_name(rows, "USE.EMP-VIEW")["kind"] == "view"
    assert [target for _, target, _ in edges] == [
        "ARGS",
        "LOCAL-D",
        "EMPLOYEE",
        "COMMON-C",
        "SCREEN",
    ]
    assert next(r for r in refs if r["name"] == "LANG&")["kind"] == "dynamic_calls"
    for suffix in ("nsm", "nsd"):
        layout, refs, edges = extract("MAP LAYOUT CALLNAT 'TEXT'\n", "SCREEN." + suffix)
        assert len(layout) == 1 and refs == edges == []


def natural_repo(tmp_path):
    repo = tmp_path / "natural"
    repo.mkdir()
    fixtures = {
        "TOTAL.NS7": FUNCTION,
        "PAY-ONE.NSN": "DEFINE DATA PARAMETER\n1 #AMOUNT (I4)\nEND-DEFINE\nEND\n",
        "ARGS.NSA": "DEFINE DATA PARAMETER\n1 #AMOUNT (I4)\nEND-DEFINE\n",
        "SCREEN.NSM": "screen layout\n",
        "COMMON.NSC": "WRITE 'common'\n",
        "RUN.NSP": "DEFINE DATA PARAMETER USING args\nEND-DEFINE\nCALLNAT 'pay-one'\nWRITE f#total(<2,3>)\nINCLUDE common\nINPUT USING MAP 'screen'\nEND\n",
        "DYNAMIC.NSP": "DEFINE DATA LOCAL\n1 PAY-ONE (A8)\nEND-DEFINE\nCALLNAT PAY-ONE\nEND\n",
    }
    for path, source in fixtures.items():
        (repo / path).write_text(source)
    return repo


def test_natural_graph_queries_cli_namespace_and_dynamic_edges(tmp_path, capsys):
    repo = natural_repo(tmp_path)
    root = tmp_path / "instance"
    identity = namespace(repo)
    code_index.index_repo(root, repo)
    assert (
        code_index.symbols_for(root, "f#total", repo=identity)[0]["path"] == "TOTAL.NS7"
    )
    assert code_index.callers(root, "f#total", repo=identity)[0]["path"] == "RUN.NSP"
    edges = code_index.graph_edges(root, repo=identity)
    assert {(r["from_path"], r["to_path"], r["kind"]) for r in edges} == {
        ("RUN.NSP", "ARGS.NSA", "imports"),
        ("RUN.NSP", "COMMON.NSC", "imports"),
        ("RUN.NSP", "SCREEN.NSM", "imports"),
        ("RUN.NSP", "PAY-ONE.NSN", "calls"),
        ("RUN.NSP", "TOTAL.NS7", "calls"),
    }
    assert all(r["status"] == "candidate" and not r["stale"] for r in edges)
    assert all(r["extractor"] == "stdlib-natural" for r in edges)
    assert (
        code_index.main(
            ["signature", "f#total", "--namespace", identity, "--expect", "one"], root
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)[0]["extractor"] == "stdlib-natural"
    assert code_index.main(["type", "args", "--expect", "one"], root) == 0
    assert json.loads(capsys.readouterr().out)[0]["kind"] == "data_area"
    assert (
        code_index.main(
            ["precedent", "CALLNAT", "--language", "natural", "--expect", "exists"],
            root,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)
    assert code_index.main(["stats"], root) == 0
    assert json.loads(capsys.readouterr().out)["coverage"]["natural"]["files"] == 7
    assert code_index.index_repo(root, repo)["changed"] == 0
    assert not code_index.symbols_for(root, "f#total", repo="another")


def test_natural_anchor_staleness_and_scoped_dependency_invalidation(tmp_path):
    repo = natural_repo(tmp_path)
    root = tmp_path / "instance"
    code_index.index_repo(root, repo)
    indexed = appmap.source_anchor(repo, "TOTAL.NS7", "F#TOTAL", index_root=root)
    assert indexed == appmap.source_anchor(repo, "TOTAL.NS7", "F#TOTAL")
    assert indexed["sha256"] == span_sha(FUNCTION, 2, 11)
    (repo / "TOTAL.NS7").write_text(FUNCTION.replace("* #FACTOR", "+ #FACTOR"))
    with pytest.raises(ValidationError, match="stale"):
        appmap.source_anchor(repo, "TOTAL.NS7", "F#TOTAL", index_root=root)
    code_index.index_repo(root, repo, paths=["TOTAL.NS7"])
    assert code_index.symbols_for(root, "RUN")[0]["stale"]
    code_index.index_repo(root, repo)
    assert not code_index.symbols_for(root, "RUN")[0]["stale"]
    (repo / "ARGS.NSA").write_text(
        "DEFINE DATA PARAMETER\n1 #AMOUNT (I8)\nEND-DEFINE\n"
    )
    code_index.index_repo(root, repo, paths=["ARGS.NSA"])
    assert code_index.symbols_for(root, "RUN")[0]["stale"]


def test_natural_prototype_keeps_real_callers_and_is_not_a_call_target(tmp_path):
    repo = natural_repo(tmp_path)
    prototype = "DEFINE PROTOTYPE F#TOTAL\nRETURNS (I4)\nDEFINE DATA PARAMETER\n1 #A (I4)\n1 #B (I4)\nEND-DEFINE\nEND-PROTOTYPE\n"
    (repo / "RUN.NSP").write_text(prototype + "WRITE F#TOTAL(<1,2>)\nEND\n")
    (repo / "INTERFACE.NSC").write_text(prototype)
    root = tmp_path / "instance"
    code_index.index_repo(root, repo)
    assert [r["path"] for r in code_index.callers(root, "F#TOTAL")] == ["RUN.NSP"]
    assert {
        (r["from_path"], r["to_path"])
        for r in code_index.graph_edges(root)
        if r["kind"] == "calls"
    } == {("RUN.NSP", "TOTAL.NS7")}


def test_natural_import_resolution_prefers_local_and_preserves_ambiguity(tmp_path):
    from hx.code_queries import resolve_path

    paths = {"LIB1/SAME.NSA", "LIB2/same.nsa"}
    assert resolve_path(None, None, "LIB1/RUN.NSP", "same", paths) == "LIB1/SAME.NSA"
    assert resolve_path(None, None, "LIB3/RUN.NSP", "SAME", paths) is None


def test_natural_module_changes_affect_parser_generation(tmp_path, monkeypatch):
    repo = natural_repo(tmp_path)
    root = tmp_path / "instance"
    code_index.index_repo(root, repo)
    original = code_natural.__file__
    parser = tmp_path / "parser.py"
    parser.write_text("new Natural parser generation")
    monkeypatch.setattr(code_natural, "__file__", str(parser))
    assert code_index.index_repo(root, repo, paths=["TOTAL.NS7"])["changed"] == 1
    assert code_index.symbols_for(root, "RUN")[0]["stale"]
    monkeypatch.setattr(code_natural, "__file__", original)
    with connect(root) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM symbols WHERE extractor='stdlib-natural'"
            ).fetchone()[0]
            > 0
        )
