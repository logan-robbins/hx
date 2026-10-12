import pytest

from hx import content_store as cs


def test_chunk_markdown_keeps_code_block_intact(tmp_path):
    text = "# Guide\n\nIntro.\n\n```python\n# not a heading\nx = 1\n```\n\nAfter.\n"
    chunks = cs.chunk_markdown(text)
    assert len(chunks) == 1
    assert "```python\n# not a heading\nx = 1\n```" in chunks[0]["content"]
    assert chunks[0]["title"] == "Guide"
    assert chunks[0]["has_code"] is True


def test_index_and_search_exact_code(tmp_path):
    root = tmp_path
    md = "# Auth\n\nUse this:\n\n```python\ndef authenticate_user(token):\n    return verify(token)\n```\n"
    cs.index_content(root, md, "docs")
    out = cs.search(root, ["authenticate_user"])
    results = out["authenticate_user"]
    assert results
    assert "authenticate_user" in results[0]["content"]
    assert results[0]["source"] == "docs"


def test_trigram_partial_match(tmp_path):
    root = tmp_path
    cs.index_content(root, "# Words\n\nThe xylophoneunicorn plays loudly.\n", "docs")
    out = cs.search(root, ["lophoneuni"])
    assert out["lophoneuni"], "trigram should match substring"


def test_fuzzy_fallback(tmp_path):
    root = tmp_path
    cs.index_content(root, "# DB\n\nThe database connection pool is configured here.\n", "docs")
    out = cs.search(root, ["databse"])
    assert out["databse"]
    assert out["databse"][0]["layer"] == "fuzzy"


def test_source_filter(tmp_path):
    root = tmp_path
    cs.index_content(root, "# A\n\nsharedkeyword alpha content.\n", "alpha")
    cs.index_content(root, "# B\n\nsharedkeyword beta content.\n", "beta")
    out = cs.search(root, ["sharedkeyword"], source="beta")
    results = out["sharedkeyword"]
    assert results
    assert all(r["source"] == "beta" for r in results)


def test_content_type_filter(tmp_path):
    root = tmp_path
    md = ("# Mixed\n\nproseonlyword paragraph.\n\n"
          "```python\ncodeonlyword = 1\n```\n")
    # code fence is minority here, add a mostly-code doc as separate source
    cs.index_content(root, "# Code\n\n```python\ncodeonlyword = compute()\nprint(codeonlyword)\n```\n", "codedoc")
    cs.index_content(root, "# Prose\n\ncodeonlyword mentioned in prose.\n", "prosedoc")
    out = cs.search(root, ["codeonlyword"], content_type="code")
    results = out["codeonlyword"]
    assert results
    assert all(r["content_type"] == "code" for r in results)


def test_staleness_after_file_change(tmp_path):
    root = tmp_path
    f = tmp_path / "note.md"
    f.write_text("# Note\n\noriginalkeyword content.\n")
    cs.index_path(root, f)
    out = cs.search(root, ["originalkeyword"])
    assert out["originalkeyword"]
    assert out["originalkeyword"][0]["stale"] is False
    f.write_text("# Note\n\nchanged content entirely.\n")
    out2 = cs.search(root, ["originalkeyword"])
    assert out2["originalkeyword"]
    assert out2["originalkeyword"][0]["stale"] is True


def test_directory_index(tmp_path):
    root = tmp_path / "proj"
    docs = tmp_path / "docsdir"
    docs.mkdir()
    (docs / "a.md").write_text("# Alpha\n\nalphakeyword here.\n")
    (docs / "b.py").write_text("def beta_function():\n    return 'betakeyword'\n")
    (docs / "skip.json").write_text("{}")
    result = cs.index_path(root, docs, source="projdocs")
    assert result["files"] == 3
    assert result["chunks"] >= 2
    out = cs.search(root, ["alphakeyword"], source="projdocs/a.md")
    assert out["alphakeyword"]


def test_redact_masks_secrets(tmp_path):
    text = "password: hunter2 api_key=abc123secret Authorization: Bearer xyz789 token = tok123"
    out = cs.redact(text)
    assert "hunter2" not in out
    assert "abc123secret" not in out
    assert "xyz789" not in out
    assert "tok123" not in out
    assert "***" in out


def test_fetch_blocks_link_local(tmp_path):
    with pytest.raises(ValueError):
        cs.fetch_and_index(tmp_path, "http://169.254.169.254/latest/meta-data/")


def test_fetch_blocks_file_scheme(tmp_path):
    with pytest.raises(ValueError):
        cs.fetch_and_index(tmp_path, "file:///etc/passwd")


def test_stats_and_multi_query(tmp_path):
    root = tmp_path
    cs.index_content(root, "# One\n\nfirstkeyword here.\n\n```python\nsecondkeyword = 1\nx = secondkeyword\n```\n", "s1")
    s = cs.stats(root)
    assert s["sources"] == 1
    assert s["chunks"] >= 1
    assert s["bytes_indexed"] > 0
    out = cs.search(root, ["firstkeyword", "secondkeyword"])
    assert set(out.keys()) == {"firstkeyword", "secondkeyword"}
    assert out["firstkeyword"]
