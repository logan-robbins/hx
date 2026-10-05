import sys

import pytest

from hx import jev, tool_execution, output_reduction
from hx.errors import ValidationError
from hx.continuity_store import Conflict
from .test_native_companion import planned


def test_small_command_is_bounded_and_replay_never_reexecutes(planned):
    store, run, _ = planned
    argv = [sys.executable, '-c', "from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); print('done')"]
    first = tool_execution.execute(store, run, 'one', argv)
    assert first['complete'] and first['text'] == 'done\n'
    assert tool_execution.execute(store, run, 'one', argv) == first
    with store.transaction() as tx:
        from pathlib import Path
        path = Path(tx.task('T')['payload']['workdir']) / 'count'
    assert path.read_text() == 'x'
    with pytest.raises(Conflict, match='different arguments'):
        tool_execution.execute(store, run, 'one', ['true'])


def test_middle_failure_is_preserved_while_surplus_is_scored(planned, monkeypatch):
    store, run, _ = planned
    calls = []
    def irrelevant(state, questions, **kwargs):
        assert not store.db.in_transaction
        calls.append(1)
        return {'model': jev.MODEL, 'answers': {key: {'type': 'noul', 'noul': .01} for key in questions},
                'usage': {'input_tokens': 50, 'output_tokens': 20}, 'latency_ms': 1}
    monkeypatch.setattr(jev, 'evaluate', irrelevant)
    text = 'progress ok\n' * 450 + 'ERROR: parser rejected a valid field\nAssertionError: expected accepted\n' + 'progress ok\n' * 450
    result = tool_execution.execute(store, run, 'failure', [sys.executable, '-c', 'print('+repr(text)+'); raise SystemExit(1)'])
    assert result['exit_code'] == 1 and 'ERROR: parser rejected' in result['text']
    assert 'AssertionError: expected accepted' in result['text']
    assert len(result['text'].encode()) <= output_reduction.OUTPUT_BYTES
    assert result['omitted_bytes'] > 0 and result['recovery'].startswith('hx evidence ')
    assert 1 <= len(calls) <= 4


def test_jev_failure_keeps_captured_command_and_does_not_substitute_output(planned, monkeypatch):
    store, run, _ = planned
    def fail(*args, **kwargs):
        raise jev.Unavailable('timeout')
    monkeypatch.setattr(jev, 'evaluate', fail)
    argv = [sys.executable, '-c', "print('ordinary output '*400)"]
    with pytest.raises(ValidationError, match='Jev decision stopped'):
        tool_execution.execute(store, run, 'timeout', argv)
    assert store.db.execute('SELECT status FROM tool_executions').fetchone()[0] == 'captured'
    count = store.db.execute('SELECT count(*) FROM events').fetchone()[0]
    with pytest.raises(ValidationError):
        tool_execution.execute(store, run, 'timeout', argv)
    assert store.db.execute('SELECT count(*) FROM events').fetchone()[0] == count


def test_excess_diagnostics_explicitly_require_recovery(planned):
    store, run, _ = planned
    result = tool_execution.execute(store, run, 'many-errors', [sys.executable, '-c', "print('ERROR: detailed failure\\n'*500)"])
    assert result['requires_read'] and not result['complete']
    assert len(result['text'].encode()) <= 4096
