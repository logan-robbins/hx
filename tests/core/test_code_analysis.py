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
    assert len(capsys.readouterr().out.strip().encode()) <= 700
    assert code_index.main(["submaps"], root) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["beats_directory"] and len(result["candidates"]) == 2
    for candidate in result["candidates"]:
        appmap.validate_record(candidate["operations"][0]["record"])
    conn = connect(root)
    code_index.select(conn, caller=repo)
    assert code_analysis.run(conn, "submaps") == result
    conn.close()
