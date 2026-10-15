"""Host repository protocol: immutable reads, shared CLI and bounded clients."""

import json
import subprocess
import threading
import tempfile
from pathlib import Path

import pytest

from hx import repo_service
from hx.errors import ValidationError


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout.decode().strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "main.py").write_text("def feature():\n    return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    before = git(repo, "rev-parse", "HEAD")
    (repo / "main.py").write_text("def feature():\n    return 2\n")
    git(repo, "commit", "-qam", "second")
    return repo, before


def test_exact_source_reads_do_not_follow_dirty_working_copy(tmp_path, repository):
    repo, before = repository
    root = tmp_path / "service"
    ns = repo_service.register(root, repo)["namespace"]
    (repo / "main.py").write_text("different dirty source\n")
    answer = repo_service.dispatch(root, {"method": "file", "namespace": ns, "at": before, "path": "main.py"})
    assert answer["text"] == "def feature():\n    return 1\n"
    assert answer["at"] == before and answer["complete"]
    diff = repo_service.dispatch(root, {"method": "diff", "namespace": ns, "at": "HEAD", "base": before})
    assert diff["paths"] == ["main.py"] and diff["complete"]
    history = repo_service.dispatch(root, {"method": "history", "namespace": ns, "path": "main.py", "limit": 1})
    assert len(history["commits"]) == 1 and not history["complete"]
    base = repo_service.dispatch(root, {"method": "merge-base", "namespace": ns, "base": before})
    assert base["merge_base"] == before


def test_traversal_symlink_and_partial_ranges_are_refused(tmp_path, repository):
    repo, before = repository
    root = tmp_path / "service"
    ns = repo_service.register(root, repo)["namespace"]
    with pytest.raises(ValidationError, match="inside"):
        repo_service.dispatch(root, {"method": "file", "namespace": ns, "path": "../private"})
    for field in ("line", "lines", "offset", "length"):
        with pytest.raises(ValidationError, match="complete source"):
            repo_service.dispatch(root, {"method": "file", "namespace": ns, "path": "main.py", field: 1})
    (repo / "link.py").symlink_to("main.py")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "link")
    with pytest.raises(ValidationError, match="regular"):
        repo_service.dispatch(root, {"method": "file", "namespace": ns, "path": "link.py"})


def test_complete_source_includes_late_function_long_lines_and_exact_unicode(tmp_path, repository):
    import hashlib
    repo, _ = repository
    source = "# orientation\n" * 300 + "value = '" + "é雪" * 400 + "'\n\ndef existing_provider():\n    return value\n"
    (repo / "large.py").write_text(source)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "late existing provider")
    root = tmp_path / "service"
    ns = repo_service.register(root, repo)["namespace"]
    answer = repo_service.dispatch(root, {"method": "file", "namespace": ns, "path": "large.py"})
    assert answer["text"] == source and answer["complete"] is True
    assert answer["bytes"] == len(source.encode())
    assert answer["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert answer["total_lines"] == len(source.splitlines())


def test_complete_source_rejects_invalid_utf8_instead_of_replacing_bytes(tmp_path, repository):
    repo, _ = repository
    (repo / "invalid.py").write_bytes(b"valid_prefix = True\n\xffunread_tail")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "invalid encoding")
    root = tmp_path / "service"
    ns = repo_service.register(root, repo)["namespace"]
    with pytest.raises(ValidationError, match="not UTF-8"):
        repo_service.dispatch(root, {"method": "file", "namespace": ns, "path": "invalid.py"})


def test_socket_serves_multiple_instances_and_real_code_cli(tmp_path, repository):
    repo, _ = repository
    root = tmp_path / "service"
    root.mkdir()
    short = tempfile.TemporaryDirectory(prefix="hx-repo-test-", dir="/tmp")
    path = Path(short.name) / "service.sock"
    server = repo_service.RepositoryServer(path, root, workers=2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        repo_service.request(path, {"method": "register", "repo": str(repo)})
        for name in ("one", "two"):
            instance = tmp_path / name
            (instance / "config").mkdir(parents=True)
            (instance / "config/hx.json").write_text(json.dumps({"repository_service": str(path)}))
            indexed = repo_service.code_request(instance, ["index", "--repo", str(repo)])
            assert indexed["exit_code"] == 0
            assert repo_service.code_request(instance, ["signature", "feature"])["result"][0]["signature"].startswith("def feature")
            assert not (instance / "state/code-index/index.db").exists()
        assert repo_service.request(path, {"method": "status"})["repositories"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        short.cleanup()


def test_socket_surfaces_errors_and_rejects_root_override(tmp_path):
    with pytest.raises(ValidationError, match="arguments"):
        repo_service.dispatch(tmp_path, {"method": "code", "argv": ["stats", "--root=/private"]})
    with pytest.raises(ValidationError, match="unknown"):
        repo_service.dispatch(tmp_path, {"method": "unknown", "namespace": "missing"})


def test_sparse_materialization_clean_native_identity_and_metadata_only_queries(tmp_path, repository):
    repo, before = repository
    (repo / 'other.py').write_text('unrelated = True\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'unrelated source')
    root = tmp_path / 'service'
    ns = repo_service.register(root, repo)['namespace']
    out = tmp_path / 'sparse'
    result = repo_service.dispatch(root, {'method':'materialize','namespace':ns,'at':'HEAD','paths':['main.py'],'out':str(out),'working_copy':True})
    assert result['working_copy'] and (out/'main.py').is_file()
    assert not (out/'other.py').exists()
    assert git(out, 'rev-parse', 'HEAD') == result['at'] and git(out, 'status', '--porcelain') == ''
    assert (out/'.git/objects/info/alternates').is_file()
    with pytest.raises(ValidationError, match='fresh'):
        repo_service.dispatch(root, {'method':'materialize','namespace':ns,'paths':['main.py'],'out':str(out)})
    telemetry = repo_service.dispatch(root, {'method':'status'})
    assert telemetry['latency'][0]['calls'] >= 1


def test_registered_source_supports_historical_query_before_live_index(tmp_path, repository):
    repo, before = repository
    root = tmp_path / 'service'
    ns = repo_service.register(root, repo)['namespace']
    result = repo_service.dispatch(root, {'method':'code','argv':['signature','feature','--namespace',ns,'--at',before]})
    assert result['exit_code'] == 0 and result['result'][0]['snapshot']


def test_sparse_paths_are_literal_not_patterns(tmp_path, repository):
    repo, _ = repository
    (repo / 'feature[1].py').write_text('exact = True\n')
    (repo / 'feature1.py').write_text('wrong = True\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'literal sparse names')
    root = tmp_path / 'service'
    ns = repo_service.register(root, repo)['namespace']
    out = tmp_path / 'sparse'
    repo_service.dispatch(root, {'method':'materialize','namespace':ns,'paths':['feature[1].py'],'out':str(out),'working_copy':True})
    assert (out / 'feature[1].py').read_text() == 'exact = True\n'
    assert not (out / 'feature1.py').exists()
    assert git(out, 'status', '--porcelain') == ''


def test_sparse_read_set_expands_at_fixed_source_without_removing_prior_files(tmp_path, repository):
    repo, _ = repository
    (repo / 'dependency.py').write_text('dependency = 1\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'dependency')
    root = tmp_path / 'service'
    ns = repo_service.register(root, repo)['namespace']
    out = tmp_path / 'sparse'
    initial = repo_service.dispatch(root, {'method':'materialize','namespace':ns,'paths':['main.py'],'out':str(out),'working_copy':True})
    repo_service.dispatch(root, {'method':'expand','namespace':ns,'at':initial['at'],'paths':['dependency.py'],'out':str(out)})
    assert (out / 'main.py').exists() and (out / 'dependency.py').read_text() == 'dependency = 1\n'
    (out / 'main.py').write_text('uncommitted work\n')
    with pytest.raises(ValidationError, match='clean copy'):
        repo_service.dispatch(root, {'method':'expand','namespace':ns,'at':initial['at'],'paths':['dependency.py'],'out':str(out)})
    assert (out / 'main.py').read_text() == 'uncommitted work\n'


@pytest.mark.parametrize('implicit', [False, True])
def test_materialization_preflights_all_selected_blobs_before_checkout(tmp_path, repository, monkeypatch, implicit):
    repo, _ = repository
    if implicit:
        (repo / '.hx/map').mkdir(parents=True)
        (repo / '.hx/map/manifest.json').write_text(json.dumps({'repo_id': '7d245a01-023b-49f7-94bc-28e780f8c655', 'padding': 'x' * 600000}))
        paths = ['main.py']
    else:
        (repo / 'huge.py').write_text('x' * 600000)
        paths = ['huge.py']
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'oversize source')
    root = tmp_path / 'service'
    ns = repo_service.register(root, repo)['namespace']
    original = subprocess.run
    copies = []
    def observed(argv, *args, **kwargs):
        if 'clone' in argv or 'checkout' in argv:
            copies.append(argv)
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', observed)
    out = tmp_path / 'copy'
    with pytest.raises(ValidationError, match='bounded regular'):
        repo_service.dispatch(root, {'method':'materialize','namespace':ns,'paths':paths,'out':str(out),'working_copy':True})
    assert copies == [] and not out.exists()


@pytest.mark.parametrize('required', [{'object': '0' * 40, 'mode': '100644'}, None])
def test_assignment_requires_exact_hard_dependency_blobs_before_provisioning(tmp_path, repository, required):
    repo, _ = repository
    root = tmp_path / 'service'
    ns = repo_service.register(root, repo)['namespace']
    out = tmp_path / 'copy'
    with pytest.raises(ValidationError, match='exact hard dependency'):
        repo_service.dispatch(root, {'method':'materialize','namespace':ns,'paths':['main.py'],'out':str(out),
            'required_files': {'main.py': required}, 'working_copy':True})
    assert not out.exists()
