from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from hx import appmap
from hx.continuity_store import Conflict, ContinuityStore, SCHEMA_VERSION, canonical
from hx.errors import ValidationError


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}).stdout.strip()


def commit(repo):
    git(repo, "add", ".")
    return git(repo, "commit", "-qm", "map fixture")


def record(identifier="compiler", kind="component", **changes):
    value = {"schema_version": 1, "id": identifier, "version": 1, "kind": kind,
             "claim": "required", "summary": "The compiler preserves current obligations.",
             "data": {"responsibility": "Assemble bounded continuation context."},
             "anchors": [], "edges": [], "attributes": {}, "replaces": []}
    return {**value, **changes}


def write(repo, node):
    path = repo / ".hx" / "map" / appmap.KINDS[node["kind"]] / f"{node['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(node) + "\n")
    return path


@pytest.fixture
def mapped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "compiler.py").write_text("class Compiler:\n    def build(self):\n        return 'context'\n")
    info = appmap.initialize(repo)
    behavior = record("bounded-context", "behavior", data={"outcome": "Continuation contains the current goal and obligations.",
        "preconditions": ["The task is assigned."], "inputs": ["Current task facts."], "outputs": ["A bounded packet."],
        "side_effects": [], "failure_modes": ["Required context exceeds the budget."], "invariants": ["Required constraints remain intact."]})
    component = record(claim="observed", anchors=[appmap.source_anchor(repo, "compiler.py", "Compiler.build")],
        attributes={"python.runtime": "3.14", "future.namespace": {"preserve": True}},
        edges=[{"kind": "implements", "to": behavior["id"], "status": "validated", "evidence": {"kind": "source", "anchors": [0]}}])
    write(repo, behavior)
    write(repo, component)
    commit(repo)
    with ContinuityStore(tmp_path / "ledger") as store:
        yield store, repo, info, component, behavior


def test_fresh_clone_reconstructs_the_same_portable_baseline(mapped, tmp_path):
    store, repo, info, component, _ = mapped
    first = appmap.import_baseline(store, repo)
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(repo), str(clone))
    assert appmap.check(clone)["map_hash"] == first["map_hash"]
    with ContinuityStore(tmp_path / "fresh-ledger") as other:
        imported = appmap.import_baseline(other, clone)
        assert imported == first
        selected = appmap.get_record(other, clone, imported["snapshot"], component["id"])
        assert selected["record"] == component and selected["applicability"] == "current"
    assert info["repo_id"] == first["repo_id"]


def test_import_is_idempotent_and_export_is_deterministic(mapped):
    store, repo, _, _, _ = mapped
    imported = appmap.import_baseline(store, repo)
    assert appmap.import_baseline(store, repo) == imported
    before = {str(path.relative_to(repo)): path.read_bytes() for path in (repo / ".hx" / "map").rglob("*.json")}
    appmap.export_snapshot(store, repo, imported["snapshot"])
    after = {str(path.relative_to(repo)): path.read_bytes() for path in (repo / ".hx" / "map").rglob("*.json")}
    assert before == after
    assert store.db.execute("SELECT count(*) FROM map_records").fetchone()[0] == 2


def test_stack_replacement_retains_responsibility_id_and_unknown_attributes(mapped):
    store, repo, _, component, _ = mapped
    old = appmap.import_baseline(store, repo)
    (repo / "compiler.py").unlink()
    (repo / "compiler.go").write_text("package compiler\nfunc Build() string { return \"context\" }\n")
    updated = copy.deepcopy(component)
    updated["version"] = 2
    updated["anchors"] = [appmap.source_anchor(repo, "compiler.go")]
    updated["attributes"].pop("python.runtime")
    updated["attributes"]["go.runtime"] = "1.x"
    write(repo, updated)
    commit(repo)
    new = appmap.import_baseline(store, repo)
    selected = appmap.get_record(store, repo, new["snapshot"], "compiler")["record"]
    assert selected["id"] == component["id"] and selected["data"] == component["data"]
    assert selected["attributes"]["future.namespace"] == {"preserve": True}
    assert new["snapshot"] != old["snapshot"]


def test_line_movement_refreshes_hint_without_changing_semantic_identity(mapped):
    store, repo, _, component, _ = mapped
    imported = appmap.import_baseline(store, repo)
    source = repo / "compiler.py"
    source.write_text("# New heading\n\n" + source.read_text())
    selected = appmap.get_record(store, repo, imported["snapshot"], "compiler")
    assert selected["applicability"] == "current"
    assert selected["record"]["anchors"][0]["line"] == component["anchors"][0]["line"] + 2
    assert appmap.semantic_identity(selected["record"]) == appmap.semantic_identity(component)


def test_source_change_makes_current_read_stale_and_ci_refuses(mapped):
    store, repo, _, _, _ = mapped
    imported = appmap.import_baseline(store, repo)
    (repo / "compiler.py").write_text("class Compiler:\n    def build(self):\n        return 'stale'\n")
    selected = appmap.get_record(store, repo, imported["snapshot"], "compiler")
    assert selected["applicability"] == "stale" and selected["validated_edges"] == []
    with pytest.raises(ValidationError, match="stale map anchor"):
        appmap.check(repo)
    with pytest.raises(Conflict, match="stale map record"):
        appmap.export_snapshot(store, repo, imported["snapshot"])


def test_candidate_edges_do_not_become_validated_relationships(mapped):
    store, repo, _, component, _ = mapped
    component["claim"] = "hypothesis"
    component["edges"][0]["status"] = "candidate"
    component["edges"][0]["evidence"] = {}
    write(repo, component)
    commit(repo)
    imported = appmap.import_baseline(store, repo)
    assert appmap.get_record(store, repo, imported["snapshot"], "compiler")["validated_edges"] == []
    component["edges"][0]["status"] = "validated"
    with pytest.raises(ValidationError, match="hypotheses"):
        appmap.validate_record(component)


def test_dangling_and_mistyped_edges_fail_graph_validation(mapped):
    _, repo, _, component, _ = mapped
    component["edges"][0]["to"] = "absent"
    write(repo, component)
    with pytest.raises(ValidationError, match="no endpoint"):
        appmap.check(repo)
    component["edges"][0].update(to="bounded-context", kind="provides")
    write(repo, component)
    with pytest.raises(ValidationError, match="typed relationship"):
        appmap.check(repo)


def test_checked_by_requires_an_explicit_declaration(mapped):
    _, _, _, component, _ = mapped
    component["edges"] = [{"kind": "checked_by", "to": "regression", "status": "validated", "evidence": {"kind": "test_passed"}}]
    with pytest.raises(ValidationError, match="explicit check declaration"):
        appmap.validate_record(component)
    component["edges"][0]["evidence"] = {"kind": "check_declaration", "check_id": "regression", "declaration": "The regression asserts the required cursor invariant."}
    appmap.validate_record(component)


def test_branch_baselines_do_not_leak_into_sibling_worktrees(mapped):
    store, repo, _, component, _ = mapped
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "feature")
    component["attributes"]["feature.contract"] = "unmerged"
    write(repo, component)
    commit(repo)
    imported = appmap.import_baseline(store, repo)
    git(repo, "checkout", "-q", base)
    with pytest.raises(Conflict, match="not an ancestor"):
        appmap.get_record(store, repo, imported["snapshot"], "compiler")


def test_export_refuses_uncommitted_map_edits_without_changing_them(mapped):
    store, repo, _, component, _ = mapped
    imported = appmap.import_baseline(store, repo)
    component["summary"] = "An operator's current edit."
    path = write(repo, component)
    before = path.read_bytes()
    with pytest.raises(Conflict, match="uncommitted map edits"):
        appmap.export_snapshot(store, repo, imported["snapshot"])
    assert path.read_bytes() == before


def test_import_verifies_committed_objects_despite_assume_unchanged_flags(mapped):
    store, repo, _, component, _ = mapped
    path = write(repo, component)
    git(repo, "update-index", "--assume-unchanged", str(path.relative_to(repo)))
    component["summary"] = "A hidden working-tree edit."
    write(repo, component)
    assert not git(repo, "status", "--porcelain")
    with pytest.raises(Conflict, match="committed contents"):
        appmap.import_baseline(store, repo)
    assert store.db.execute("SELECT count(*) FROM map_records").fetchone()[0] == 0


@pytest.mark.parametrize("change", [
    {"schema_version": 99}, {"kind": "unknown"}, {"attributes": {"unscoped": True}},
    {"anchors": [{"path": "../outside", "symbol": None, "sha256": "0" * 64}]},
    {"anchors": [{"path": ".hx/map/manifest.json", "symbol": None, "sha256": "0" * 64}]},
])
def test_portable_schema_rejects_ambiguous_or_unsafe_structure(change):
    with pytest.raises(ValidationError):
        appmap.validate_record(record(**change))


def test_map_directory_cannot_redirect_writes_outside_repository(tmp_path):
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / ".hx").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValidationError, match="symlinks"):
        appmap.initialize(repo)
    assert not list(outside.iterdir())


def test_schema_five_upgrade_preserves_authority(mapped):
    store, _, _, _, _ = mapped
    store.db.execute("DROP TABLE map_snapshots")
    store.db.execute("PRAGMA user_version=5")
    with ContinuityStore(store.root) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert upgraded.db.execute("SELECT count(*) FROM map_snapshots").fetchone()[0] == 0


def test_map_cli_validates_and_reads_one_record(mapped, run_hx):
    store, repo, _, _, _ = mapped
    validated = run_hx("map", "check", "--repo", str(repo), "--root", str(store.root))
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["records"] == 2
    imported = appmap.import_baseline(store, repo)
    read = run_hx("map", "get", "compiler", "--repo", str(repo), "--snapshot", imported["snapshot"], "--root", str(store.root))
    assert read.returncode == 0, read.stderr
    assert json.loads(read.stdout)["record"]["id"] == "compiler"


def test_unchanged_map_lookup_does_not_reread_source_files(mapped, monkeypatch):
    store, repo, _, _, _ = mapped
    imported = appmap.import_baseline(store, repo)
    def unexpected_read(*args, **kwargs):
        pytest.fail("unchanged map lookup reread the source file")
    monkeypatch.setattr(appmap, "source_anchor", unexpected_read)
    assert appmap.get_record(store, repo, imported["snapshot"], "compiler")["applicability"] == "current"


def test_interface_schema_and_invariants_survive_portable_import(mapped):
    store, repo, _, _, _ = mapped
    contract = record("packet-contract", "interface", summary="A packet names every required obligation.",
        data={"contract": {"type": "object", "required": ["goal"], "properties": {"goal": {"type": "string"}}},
              "invariants": ["The goal must retain its conditions and negation."]})
    write(repo, contract)
    commit(repo)
    imported = appmap.import_baseline(store, repo)
    assert appmap.get_record(store, repo, imported["snapshot"], "packet-contract")["record"]["data"] == contract["data"]


def test_initializers_publish_one_complete_repository_identity(tmp_path, monkeypatch):
    original = os.link
    winner = []
    def publish(source, target, *args, **kwargs):
        if not winner:
            monkeypatch.setattr(os, "link", original)
            winner.append(appmap.initialize(tmp_path))
        return original(source, target, *args, **kwargs)
    monkeypatch.setattr(os, "link", publish)
    assert appmap.initialize(tmp_path) == winner[0] == appmap.manifest(tmp_path)


def test_file_anchor_reads_bounded_chunks_and_rejects_growth(tmp_path, monkeypatch):
    target = tmp_path / "large.bin"
    target.write_bytes(b"x" * 200000)
    original = os.fdopen
    read_sizes = []
    class GrowingFile:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def fileno(self):
            return self.handle.fileno()
        def read(self, size):
            assert 0 < size <= 65536
            read_sizes.append(size)
            with target.open("ab") as writer:
                writer.write(b"new")
            return self.handle.read(size)
    monkeypatch.setattr(os, "fdopen", lambda *args, **kwargs: GrowingFile(original(*args, **kwargs)))
    with pytest.raises(ValidationError, match="changed"):
        appmap.source_anchor(tmp_path, "large.bin")
    assert sum(read_sizes) == 200000


def test_batch_map_validation_closes_its_sqlite_connection(mapped, monkeypatch):
    _, repo, _, _, _ = mapped
    original = appmap.sqlite3.connect
    connections = []
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(appmap.sqlite3, "connect", connect)
    appmap.check(repo)
    with pytest.raises(appmap.sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
