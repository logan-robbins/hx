import json
from hx import appmap, code_analysis, code_index
from hx.code_store import connect


def test_louvain_two_clusters_repeatable():
    nodes = [str(i) for i in range(8)]
    edges = [
        (a, b)
        for group in [nodes[:4], nodes[4:]]
        for a in group
        for b in group
        if a < b
    ] + [("3", "4")]
    graph = code_analysis.adjacency(nodes, edges)
    first = code_analysis.louvain(graph)
    assert first == code_analysis.louvain(graph)
    assert len(set(first.values())) == 2
    assert (
        len({first[n] for n in nodes[:4]}) == 1
        and len({first[n] for n in nodes[4:]}) == 1
    )


def test_pagerank_focus_and_determinism():
    rank = code_analysis.pagerank(["a", "b", "c"], [("a", "b"), ("c", "b")])
    assert rank["b"] > rank["a"]
    assert rank == code_analysis.pagerank(["c", "b", "a"], [("c", "b"), ("a", "b")])
    assert abs(sum(rank.values()) - 1) < 1e-10
    assert code_analysis.pagerank(["a", "b"], [], ["a"])["a"] > 0.99


def test_map_budget_and_candidate_patch(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "root"
    for name, peers in [
        ("a", ["b", "c"]),
        ("b", ["a", "c"]),
        ("c", ["a", "b"]),
        ("x", ["y", "z"]),
        ("y", ["x", "z"]),
        ("z", ["x", "y"]),
    ]:
        (repo / (name + ".py")).write_text(
            "".join("import " + peer + "\n" for peer in peers)
            + "def "
            + name
            + "(): return 1\n"
        )
    code_index.index_repo(root, repo)
    assert code_index.main(["map", "--budget", "700", "--focus", "a.py"], root) == 0
    shown = capsys.readouterr().out
    assert len(shown.strip().encode()) <= 700
    shown = json.loads(shown)
    assert [item["path"] for item in shown["files"]] == ["a.py"] and shown["more"] == [] and shown["complete"]
    # A focus names every declaring file under it: the budget decides detail, not membership.
    detailed = len(json.dumps(shown["files"][0]).encode())
    assert code_index.main(["map", "--budget", str(detailed + 400), "--focus", "."], root) == 0
    shown = json.loads(capsys.readouterr().out)
    assert 1 <= len(shown["files"]) < 6 and shown["omitted"] == 0 and shown["complete"]
    assert sorted(item["path"] for item in [*shown["files"], *shown["more"]]) == sorted(f"{n}.py" for n in "abcxyz")
    assert shown["more"][0]["definitions"] == [shown["more"][0]["path"][0]]
    assert code_index.main(["map", "--budget", "200", "--focus", "."], root) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["omitted"] > 0 and not shown["complete"]
    assert code_index.main(["submaps"], root) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["beats_directory"] and len(result["candidates"]) == 2
    for candidate in result["candidates"]:
        appmap.validate_record(candidate["operations"][0]["record"])
    conn = connect(root)
    code_index.select(conn, caller=repo)
    assert code_analysis.run(conn, "submaps") == result
    conn.close()
