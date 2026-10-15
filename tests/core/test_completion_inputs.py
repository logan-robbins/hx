"""Native completion requires owned, clean, immutable assignment inputs."""
import pytest

from hx import complete, native_completion, native_launch
from hx.continuity_store import Conflict
from .test_appmap import mapped
from .test_map_updates import active
from .test_native_tools import runtime, configured, assignment, fleet, submitted


def test_native_completion_refuses_dirty_assignment(runtime):
    store, repo, run, env = runtime
    submitted(runtime)
    (repo / 'compiler.py').write_text('uncommitted output\n')
    with pytest.raises(Conflict, match='clean'):
        complete.complete(store.root, env={**env, 'HARNESS_ID': 'eng-001', 'HX_CONTINUITY_RUN': run})
    assert not store.db.execute("SELECT 1 FROM runtime_cycles WHERE scope=?", ('finish:' + run,)).fetchone()
    assert store.db.execute('SELECT ended_at FROM runs WHERE run_id=?', (run,)).fetchone()[0] is None


def test_native_completion_refuses_another_workers_run(runtime):
    store, _, run, env = runtime
    submitted(runtime)
    with pytest.raises(Conflict, match='another worker'):
        complete.complete(store.root, env={**env, 'HARNESS_ID': 'eng-002', 'HX_CONTINUITY_RUN': run})


def test_completion_request_retains_execution_ownership_until_runtime_checks(runtime):
    store, _, run, env = runtime
    submitted(runtime)
    result = complete.complete(store.root, env={**env, 'HARNESS_ID': 'eng-001', 'HX_CONTINUITY_RUN': run})
    assert result['status'] == 'requested'
    assert native_launch._row(store, run)['status'] == 'submitted'
    assert store.db.execute('SELECT ended_at FROM runs WHERE run_id=?', (run,)).fetchone()[0] is None
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?', (run,)).fetchone()[0]
    assert not store.db.execute('SELECT 1 FROM unit_completions WHERE run_id=?', (run,)).fetchone()
