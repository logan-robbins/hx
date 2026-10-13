"""Tests for hx.code_index: deterministic SQLite/FTS5 code search (tmp repos only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import code_index


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "calc.py").write_text(
        '"""Calc helpers."""\n\n\n'
        "def compute_total(items):\n"
        "    return sum(items)\n\n\n"
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n\n"
        "    def total(self, items):\n"
        "        return compute_total(items)\n",
        encoding="utf-8",
    )
    (repo / "src" / "app.py").write_text(
        "from src.calc import compute_total\n\n\n"
        "def run():\n"
        "    return compute_total([1, 2, 3])\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_calc.py").write_text(
        "from src.calc import compute_total\n\n\n"
        "def test_compute_total():\n"
        "    assert compute_total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    (repo / "src" / "widget.ts").write_text(
        "import { useState } from 'react';\n"
        "export function useEffectShim() { return useState(0); }\n"
        "export class Widget { render() { return useEffectShim(); } }\n",
        encoding="utf-8",
    )
    return repo


@pytest.fixture()
def indexed(tmp_path):
    root = tmp_path / "hxroot"
    repo = _repo(tmp_path)
    result = code_index.index_repo(root, repo)
    return root, repo, result


def test_index_counts(indexed):
    root, _repo_path, result = indexed
    assert result["files"] == 4
    assert result["symbols"] >= 5
    assert code_index.db_path(root).is_file()


def test_python_qualified_symbols(indexed):
    root, _, _ = indexed
    calc = code_index.symbols_for(root, "Calculator.add")
    assert len(calc) == 1
    assert calc[0]["name"] == "add"
    assert calc[0]["qualified"] == "Calculator.add"
    assert calc[0]["kind"] == "class" or calc[0]["path"].endswith("calc.py")
    totals = code_index.symbols_for(root, "compute_total")
    assert totals and totals[0]["qualified"] == "compute_total"
    assert totals[0]["line"] >= 1 and totals[0]["end_line"] >= totals[0]["line"]


def test_search_finds_symbol_and_body(indexed):
    root, _, _ = indexed
    hits = code_index.search(root, "compute_total")
    assert hits, "expected compute_total hits"
    assert hits[0]["layer"] == "symbol"
    assert hits[0]["path"].endswith("calc.py")
    body_hits = code_index.search(root, "Calc helpers")
    assert any(h["path"].endswith("calc.py") for h in body_hits)
    for hit in hits:
        assert {"path", "title", "snippet", "score", "layer", "repo", "lane", "anchor_sha", "stale"} <= set(hit)
        assert len(hit["snippet"]) <= 320


def test_trigram_partial_match(indexed):
    root, _, _ = indexed
    hits = code_index.search(root, "useEff")
    assert any(h["path"].endswith("widget.ts") for h in hits), hits


def test_fuzzy_correction(indexed):
    root, _, _ = indexed
    # 'compute_totl' is a typo (missing a) with distance 1 from compute_total.
    hits = code_index.search(root, "compute_totl")
    assert hits, "fuzzy fallback should correct compute_totl"
    assert all(h["layer"] == "fuzzy" for h in hits)
    assert any(h["path"].endswith("calc.py") for h in hits)


def test_impact_includes_referencing_and_test_files(indexed):
    root, _, _ = indexed
    result = code_index.impact(root, ["src/calc.py"])
    names = result["symbol_names"]
    assert "compute_total" in names
    assert "src/app.py" in result["referencing_files"]
    assert "tests/test_calc.py" in result["referencing_files"]
    assert "tests/test_calc.py" in result["candidate_tests"]


def test_callers_excludes_defining_file(indexed):
    root, _, _ = indexed
    paths = [c["path"] for c in code_index.callers(root, "compute_total")]
    assert "src/calc.py" not in paths
    assert "src/app.py" in paths


def test_graph_edges_resolves_imports(indexed):
    root, _, _ = indexed
    edges = code_index.graph_edges(root)
    assert any(
        e["from_path"] == "src/app.py" and e["to_path"] == "src/calc.py"
        and e["kind"] == "imports" and e["status"] == "candidate"
        for e in edges
    ), edges


def test_reindex_picks_up_changed_file(indexed, tmp_path):
    root, repo, _ = indexed
    (repo / "src" / "calc.py").write_text(
        '"""Calc helpers v2."""\n\n\ndef compute_total(items):\n    return sum(items) * 2\n\n'
        "def brand_new_helper():\n    return 42\n",
        encoding="utf-8",
    )
    code_index.index_repo(root, repo)
    assert code_index.symbols_for(root, "brand_new_helper")
    hits = code_index.search(root, "brand_new_helper")
    assert hits and hits[0]["layer"] == "symbol"


def test_removed_file_disappears(indexed, tmp_path):
    root, repo, _ = indexed
    assert code_index.search(root, "useEff")
    (repo / "src" / "widget.ts").unlink()
    code_index.index_repo(root, repo)
    assert code_index.search(root, "useEff") == []
    assert code_index.symbols_for(root, "Widget") == []


def test_cli_json(indexed, tmp_path, capsys):
    root, repo, _ = indexed
    assert code_index.main(["search", "compute_total", "--limit", "5"], root) == 0
    hits = json.loads(capsys.readouterr().out)
    assert hits and hits[0]["path"].endswith("calc.py")

    assert code_index.main(["symbols", "compute_total"], root) == 0
    syms = json.loads(capsys.readouterr().out)
    assert syms and syms[0]["name"] == "compute_total"

    assert code_index.main(["impact", "src/calc.py"], root) == 0
    result = json.loads(capsys.readouterr().out)
    assert "tests/test_calc.py" in result["candidate_tests"]

    other_root = tmp_path / "hxroot2"
    assert code_index.main(["index", "--repo", str(repo)], other_root) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["files"] == 4
