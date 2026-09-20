"""Instance-mode plumbing: the token file, the port, and the spec 16.1 sweep.

Pure filesystem and pure argument handling, so none of it needs `hx`. The
readers that do are in tests/ui/test_instance_source.py.
"""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from hx.ui.data import InstanceSource, SourceUnavailable
from hx.ui.server import DEFAULT_PORT, instance_port, instance_token


def build_root(root):
    """A HARNESS_ROOT holding one of every path spec 16.1 watches."""
    files = [
        "tasks.json",
        "pods/partner/partner-working.md",
        "pods/engineers/eng-001-working.md",
        "pods/engineers/archive/eng-001-2026-09-19T10:00:00Z.md",
        "orders/eng-001.md",
        "orders/eng-001.addendum.md",
        "state/eng-001/eng-001-main.json",
        "logs/eng-001/eng-001-main.jsonl",
        "run/eng-001/turn",
        "run/eng-001/goal",
        # Not watched: spec 16.1 names `run/*/turn` and `run/*/goal` only.
        "run/eng-001/persona.md",
        "run/eng-001/eng-001-main.context.md",
        "run/ui-token",
        "config/ui.json",
        "PARTNER.md",
    ]
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    return root


def touch(path, *, ahead=2):
    stamp = time.time() + ahead
    os.utime(path, (stamp, stamp))


# -- token ---------------------------------------------------------------

def test_the_token_file_is_created_with_mode_0600(tmp_path):
    token = instance_token(tmp_path)
    path = tmp_path / "run" / "ui-token"
    assert token and path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text().strip() == token


def test_an_existing_token_is_reused(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "ui-token").write_text("already-here\n", encoding="utf-8")
    assert instance_token(tmp_path) == "already-here"


def test_a_token_is_not_regenerated_between_calls(tmp_path):
    assert instance_token(tmp_path) == instance_token(tmp_path)


def test_an_empty_token_file_is_replaced(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "ui-token").write_text("\n", encoding="utf-8")
    assert instance_token(tmp_path).strip()


# -- port ----------------------------------------------------------------

def test_the_default_port_is_8765(tmp_path):
    assert DEFAULT_PORT == 8765
    assert instance_port(tmp_path) == 8765


def test_the_port_comes_from_config_ui_json(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "ui.json").write_text(json.dumps({"port": 9101}), encoding="utf-8")
    assert instance_port(tmp_path) == 9101


@pytest.mark.parametrize("content", ["not json", "[]", '{"port": "8765"}', '{"port": 0}', '{"port": 99999}', "{}"])
def test_an_unusable_config_falls_back_to_the_default(tmp_path, content):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "ui.json").write_text(content, encoding="utf-8")
    assert instance_port(tmp_path) == DEFAULT_PORT


# -- the sweep -----------------------------------------------------------

def test_scan_covers_exactly_the_watched_paths(tmp_path):
    scopes = InstanceSource(build_root(tmp_path)).scan()
    assert set(scopes) == {"tasks", "partner", "eng-001"}


@pytest.mark.parametrize(
    "name, scope",
    [
        ("tasks.json", "tasks"),
        ("pods/engineers/eng-001-working.md", "eng-001"),
        ("pods/partner/partner-working.md", "partner"),
        ("pods/engineers/archive/eng-001-2026-09-19T10:00:00Z.md", "eng-001"),
        ("orders/eng-001.md", "eng-001"),
        ("orders/eng-001.addendum.md", "eng-001"),
        ("state/eng-001/eng-001-main.json", "eng-001"),
        ("logs/eng-001/eng-001-main.jsonl", "eng-001"),
        ("run/eng-001/turn", "eng-001"),
        ("run/eng-001/goal", "eng-001"),
    ],
)
def test_a_touched_path_moves_its_scope(tmp_path, name, scope):
    source = InstanceSource(build_root(tmp_path))
    before = source.scan()
    touch(tmp_path / name)
    after = source.scan()
    assert after[scope] > before[scope], name


@pytest.mark.parametrize(
    "name",
    ["run/eng-001/persona.md", "run/eng-001/eng-001-main.context.md", "run/ui-token", "config/ui.json", "PARTNER.md"],
)
def test_unwatched_paths_do_not_move_anything(tmp_path, name):
    source = InstanceSource(build_root(tmp_path))
    before = source.scan()
    touch(tmp_path / name)
    assert source.scan() == before, f"{name} is not in the spec 16.1 watch list"


def test_scan_of_a_missing_root_is_empty(tmp_path):
    assert InstanceSource(tmp_path / "nothing-here").scan() == {}


# -- the readers ---------------------------------------------------------
# `InstanceSource.board/show/orders/archive/wake_partner` are exercised in
# tests/ui/test_instance_source.py, against a real `HARNESS_ROOT` built with
# `hx install --skeleton-only` and against a stub `hx`.


# -- the entry point -----------------------------------------------------

def test_the_entry_point_requires_a_root_or_fixtures():
    from hx.ui.server import main

    with pytest.raises(SystemExit):
        main([])
    with pytest.raises(SystemExit):
        main(["--root", "/tmp/x", "--fixtures", "/tmp/y"])
