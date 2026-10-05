from pathlib import Path
import json
import tomllib

import pytest

from hx import compaction_policy, native_controller, native_launch
from .test_appmap import mapped
from .test_map_updates import active
from .test_native_tools import runtime, configured, assignment, fleet, submitted, payload, fire
from .test_native_controller import flavor
from .test_context_packets import fact


def test_native_reset_rebuilds_current_packet_without_semantic_call(runtime, monkeypatch):
    from hx import jev
    store, repo, run, _ = runtime
    row = submitted(runtime)
    old = row['payload']['checkpoint_id']
    with store.transaction() as tx:
        event = tx.append_event(run, 'main', 'latest-correction', 'correction', {'text': 'Preserve the public signature.'})
        run_row = tx.db.execute('SELECT task_id FROM runs WHERE run_id=?', (run,)).fetchone()
        tx.put_record('now', expected_version=0, task_id=run_row[0], kind='finding',
            payload={'schema_version': 1, 'text': 'The parser now preserves the public signature.'},
            evidence=[event['event_id']], inputs={}, reason='Current implementation.', expires_when='task ends')
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: pytest.fail('forced compaction cannot await a model'))
    line = native_controller.observe(store, run, row['launch_id'], 'context',
        {'source': 'compact', 'session_id': row['payload']['native_session']})
    current = native_launch._row(store, run)
    assert current['status'] == 'submitted' and current['payload']['checkpoint_id'] != old
    text = Path(current['payload']['packet_path']).read_text()
    assert 'parser now preserves' in text and 'Preserve the public signature.' in text
    assert len(text.encode()) <= 8192
    attached = json.loads(line)['hookSpecificOutput']
    assert attached['hookEventName'] == 'SessionStart'
    assert text in attached['additionalContext']
    assert store.db.execute('SELECT disposition FROM events WHERE event_id=?', (event['event_id'],)).fetchone()[0] is None
    assert fire(store, run, current, 'tool-start', payload('after-compact')) == 0


@pytest.mark.parametrize('adapter', ['claude', 'codex'])
def test_actual_installation_contains_compaction_contract(runtime, adapter):
    flavor(runtime, adapter)
    store, _, run, _ = runtime
    row = submitted(runtime)
    home = Path(row['payload']['capsule']) / 'run/eng-001/home'
    if adapter == 'claude':
        assert compaction_policy.PROMPT in (home / 'CLAUDE.md').read_text()
    else:
        assert tomllib.loads((home / 'config.toml').read_text())['compact_prompt'] == compaction_policy.PROMPT
    assert not (home / 'skills/hx-memory').exists()


def test_precompact_prepares_pointer_without_acknowledging_tail(runtime):
    store, _, run, _ = runtime
    row = submitted(runtime)
    before = [tuple(r) for r in store.db.execute('SELECT stream_id,classified_seq FROM cursors WHERE run_id=? ORDER BY stream_id', (run,))]
    line = compaction_policy.prepare_native_compaction(store, row, {'trigger': 'auto'})
    assert 'Read the task context' in line and len(line.encode()) < 512
    assert before == [tuple(r) for r in store.db.execute('SELECT stream_id,classified_seq FROM cursors WHERE run_id=? ORDER BY stream_id', (run,))]


def test_inline_boundary_limit_counts_utf8_bytes_without_truncation():
    assert compaction_policy.claude_boundary_context('界' * 3000) is None
    state = 'Do not publish until QA passes; next run `pytest tests/parser -q`.'
    result = json.loads(compaction_policy.claude_boundary_context(state))
    assert state in result['hookSpecificOutput']['additionalContext']
