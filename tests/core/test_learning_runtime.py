import json

import pytest

from hx import companion, learning_companion, streams
from hx.continuity_store import ContinuityStore
from hx.errors import Refused
from .test_native_companion import planned


def test_learning_cli_prepares_and_inspects_independent_job(planned, capsys, monkeypatch):
    store, run, _ = planned
    monkeypatch.setattr(learning_companion, 'execute', lambda *a, **kw: pytest.fail('Prepare-only cannot call a model.'))
    args = ['eng-001', '--learning', '--run', run, '--request', 'learning-cli', '--prepare-only']
    assert companion.main(args, store.root) == 0
    first = json.loads(capsys.readouterr().out)
    assert first['status'] == 'prepared'
    assert companion.main(args, store.root) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert companion.main(['eng-001', '--learning', '--job', first['job_id']], store.root) == 0
    assert json.loads(capsys.readouterr().out) == first
    with pytest.raises(Refused, match='another agent'):
        companion.main(['eng-002', '--learning', '--job', first['job_id']], store.root)
    assert not store.db.execute('SELECT 1 FROM companion_jobs').fetchone()
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0


def test_learning_cli_rejects_context_patch_options(planned):
    store, run, _ = planned
    with pytest.raises(SystemExit):
        companion.main(['eng-001', '--learning', '--run', run, '--request', 'invalid', '--map-record', 'node'], store.root)


def test_partner_log_retention_waits_for_both_companion_cursors(instance):
    path = streams.main_stream(instance, 'partner')
    for index in range(12):
        streams.append_record(instance, 'partner', 'partner-main', {'event': 'user', 'input': f'Instruction {index}'})
    # Context finished all 12 events; learner has not assessed any yet.
    assert streams.truncate(path, state_seq=12, max_records=2, keep_behind=0) == 0
    with ContinuityStore(instance) as store:
        assert store.db.execute("SELECT assessed_seq FROM learning_cursors WHERE agent_id='partner' AND stream_id='partner-main'").fetchone()[0] == 0
        with store.transaction() as tx:
            tx.db.execute("UPDATE learning_cursors SET assessed_seq=6 WHERE agent_id='partner' AND stream_id='partner-main'")
    assert streams.truncate(path, state_seq=12, max_records=2, keep_behind=0) == 6
    assert [row['seq'] for row in streams.iter_records(path)] == list(range(7, 13))
