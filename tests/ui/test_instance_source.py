"""UI sources use the same packet readers and observe SQLite WAL commits."""
import json
import pytest
from core.test_board import seed
from hx.continuity_store import ContinuityStore
from hx.ui.data import InstanceSource, NotFound, FixtureSource, SourceError
from hx.ui.server import Watcher


def test_live_source_reads_ledger(tmp_path):
    seed(tmp_path)
    source=InstanceSource(tmp_path)
    assert source.board()['tasks'][0]['task_id']=='orders.work'
    assert source.show('run-1')['runs'][0]['run_id']=='run-1'
    assert source.goals()['plans'][0]['plan_id']=='orders'
    with pytest.raises(NotFound): source.show('unknown')
    with pytest.raises(NotFound): source.show('../state')


def test_sweep_detects_wal_changes_and_checkpoint_removal(tmp_path):
    seed(tmp_path)
    source=InstanceSource(tmp_path)
    watcher=Watcher(source)
    watcher._seen=source.scan()
    with ContinuityStore(tmp_path) as store:
        store.db.execute('UPDATE ledger_meta SET revision=revision+1')
        store.db.commit()
        assert 'board' in watcher._changed()
    assert 'board' in watcher._changed()  # closing the writer checkpoints/deletes the WAL


def test_sweep_ignores_obsolete_files(tmp_path):
    source=InstanceSource(tmp_path)
    before=source.scan()
    (tmp_path/'tasks.json').write_text('obsolete')
    (tmp_path/'pods').mkdir()
    assert source.scan()==before


def test_fixture_refuses_old_schema(tmp_path):
    (tmp_path/'board.json').write_text(json.dumps({'items':[]}))
    with pytest.raises(SourceError,match='schema_version=2'):
        FixtureSource(tmp_path).board()
