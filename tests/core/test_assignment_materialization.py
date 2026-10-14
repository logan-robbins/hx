"""Sparse assignment provisioning derives source scope and preserves closure gaps."""

import json

from hx import assignment_materialization, planning, repo_service
from .test_appmap import git, mapped
from .test_map_updates import active
from .test_planning import plan
from .test_unit_execution import fleet, source_unit, commit


def setup(fleet, *, complete=True):
    store, repo = fleet[:2]
    for name in ('feature.py', 'dependency.py', 'unrelated.py'):
        (repo / name).write_text('value = 1\n')
    commit(repo)
    unit = source_unit(repo, 'feature', 'feature.py')
    if complete:
        unit['checks']['unit']['source'] = {'complete': True, 'inputs': ['feature.py'], 'dependencies': ['dependency.py']}
    planning.apply(store, plan(fleet, [unit]))
    return store, repo


def test_materializes_frozen_assignment_and_known_dependency_only(fleet, tmp_path):
    store, repo = setup(fleet)
    service = tmp_path / 'shared'
    repo_service.register(service, repo)
    out = tmp_path / 'worker-copy'
    body = assignment_materialization.request_body(store, 'feature', out)
    answer = repo_service.dispatch(service, body)
    assert answer['assignment']['closure_complete']
    assert (out / 'feature.py').is_file() and (out / 'dependency.py').is_file()
    assert (out / '.hx/map/manifest.json').is_file()
    assert not (out / 'unrelated.py').exists()
    assert git(out, 'rev-parse', 'HEAD') == answer['at']
    assert git(out, 'status', '--porcelain') == ''


def test_assignment_cli_and_missing_closure_remain_explicit(fleet, tmp_path, capsys):
    store, repo = setup(fleet, complete=False)
    repo_service.register(store.root, repo)
    out = tmp_path / 'worker-copy'
    assert repo_service.main(['assignment', 'feature', '--out', str(out)], store.root) == 0
    answer = json.loads(capsys.readouterr().out)
    assert not answer['assignment']['closure_complete']
    assert 'unknown source closure' in answer['assignment']['gaps'][0]
