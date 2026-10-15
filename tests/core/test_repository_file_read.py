"""Actual shell-only harnesses preserve exact immutable repository file delivery."""
import hashlib
import json
import shlex

import pytest

from hx import file_reads, repo_service, repository_file_read
from hx.errors import ValidationError
from .test_repo_service import repository, git


def test_shell_file_request_reads_complete_pinned_git_source(tmp_path, repository):
    repo, before = repository
    root = tmp_path / 'service'
    namespace = repo_service.register(root, repo)['namespace']
    (repo / 'main.py').write_text('dirty = True\n')
    command = shlex.join(['hx', 'repo', 'file', '--namespace', namespace, '--at', before, '--path', 'main.py'])
    text, receipt = file_reads.prepare({'payload': {'required_files': []}}, {'workdir': str(repo)},
        {'tool_name': 'exec_command', 'tool_input': {'cmd': command}}, root=root)
    assert text == 'def feature():\n    return 1\n'
    assert receipt['path'].startswith('git:') and receipt['repository_file']['at'] == before
    assert receipt['bytes'] == len(text.encode())
    assert receipt['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert receipt['files'] == []


def test_repository_cli_never_prints_unadmitted_source_to_managed_shell(tmp_path, repository, capsys):
    repo, _ = repository
    root = tmp_path / 'service'
    namespace = repo_service.register(root, repo)['namespace']
    with pytest.raises(ValidationError, match='managed native run'):
        repo_service.main(['file', '--namespace', namespace, '--path', 'main.py'], root,
                          env={'HARNESS_ID': 'eng-001'})
    assert capsys.readouterr().out == ''
    assert repo_service.main(['file', '--namespace', namespace, '--path', 'main.py'], root, env={}) == 0
    assert json.loads(capsys.readouterr().out)['text'] == 'def feature():\n    return 2\n'


@pytest.mark.parametrize('alter', [lambda r: {**r, 'complete': False}, lambda r: {**r, 'bytes': 1},
                                  lambda r: {**r, 'text': r['text'][:4]}])
def test_service_partial_or_mismatched_source_cannot_become_a_read_receipt(tmp_path, repository, alter):
    repo, _ = repository
    root = tmp_path / 'service'
    namespace = repo_service.register(root, repo)['namespace']
    result = repo_service.dispatch(root, {'method': 'file', 'namespace': namespace, 'path': 'main.py'})
    with pytest.raises(ValidationError, match='complete|mismatched'):
        repository_file_read.from_result(alter(result))


def test_immutable_repository_read_refuses_range_flags(tmp_path, repository):
    repo, _ = repository
    root = tmp_path / 'service'
    namespace = repo_service.register(root, repo)['namespace']
    with pytest.raises(ValidationError, match='without slices'):
        repo_service.repository_read_request(root, ['file', '--namespace', namespace, '--path', 'main.py', '--lines', '10'])
