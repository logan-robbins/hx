import os
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


def chunks(result):
    """The `[offset] text` chunks of a reduction, as (offset, text) pairs."""
    return [(int(part[:part.index(']')]), part[part.index(']') + 2:]) for part in ('\n' + result['text']).split('\n[')[1:]]


def lines_script(text, exit_code=0):
    return [sys.executable, '-c', f'import sys; sys.stdout.write({text!r}); raise SystemExit({exit_code})']


def test_jev_failure_falls_back_to_head_and_tail_and_never_reruns_the_command(planned, monkeypatch):
    store, run, _ = planned
    def fail(*args, **kwargs):
        raise jev.Unavailable('timeout')
    monkeypatch.setattr(jev, 'evaluate', fail)
    text = 'first line of output\n' + ''.join(f'ordinary output {index:05d}\n' for index in range(600)) + 'final line of output\n'
    result = tool_execution.execute(store, run, 'timeout', lines_script(text))
    assert result['fallback'] == 'timeout' and not result['complete'] and result['requires_read']
    assert result['text'].startswith('[0] first line of output')
    assert result['text'].endswith('final line of output\n')
    assert len(result['text'].encode()) <= output_reduction.OUTPUT_BYTES
    assert store.db.execute('SELECT status FROM tool_executions').fetchone()[0] == 'captured'
    count = store.db.execute('SELECT count(*) FROM events').fetchone()[0]
    assert tool_execution.execute(store, run, 'timeout', lines_script(text))['text'] == result['text']
    assert store.db.execute('SELECT count(*) FROM events').fetchone()[0] == count


def test_missing_jev_credentials_reduce_without_a_request(planned, monkeypatch):
    store, run, _ = planned
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: pytest.fail('no credential, no request'))
    env = {key: value for key, value in os.environ.items() if key != 'TYPESAFE_API_KEY'}
    result = tool_execution.execute(store, run, 'no-key', lines_script('ordinary output\n' * 600), env=env)
    assert result['fallback'] == 'missing_credentials'
    assert result['text'].startswith('[0] ordinary output') and len(result['text'].encode()) <= output_reduction.OUTPUT_BYTES


def test_over_budget_judgments_degrade_to_the_fallback_without_a_request(planned, monkeypatch):
    store, run, _ = planned
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: pytest.fail('over budget, no partial judgment'))
    monkeypatch.setattr(output_reduction, 'MAX_REQUESTS', 1)
    result = tool_execution.execute(store, run, 'budget', lines_script('ordinary output\n' * 600))
    assert result['fallback'] == 'request_budget' and result['requires_read']


def test_a_character_split_across_chunks_is_carried_into_the_next_chunk(planned, monkeypatch):
    store, run, _ = planned
    monkeypatch.setattr(jev, 'evaluate', lambda state, questions, **kwargs: {'model': jev.MODEL,
        'answers': {key: {'type': 'noul', 'noul': .95} for key in questions},
        'usage': {'input_tokens': 50, 'output_tokens': 20}, 'latency_ms': 1})
    # 768 = 1 + 3 * 255 + 2: every raw chunk boundary falls inside a three-byte character.
    text = 'x' + '€' * 2000
    result = tool_execution.execute(store, run, 'utf8', lines_script(text))
    raw = text.encode()
    kept = chunks(result)
    assert kept and kept[0][0] == 0
    for (offset, chunk), following in zip(kept, kept[1:] + [None]):
        assert raw[offset:offset + len(chunk.encode())] == chunk.encode()
        if following:
            assert following[0] == offset + len(chunk.encode())


def test_a_diagnostic_line_inside_a_chunk_is_preserved(planned, monkeypatch):
    store, run, _ = planned
    monkeypatch.setattr(jev, 'evaluate', lambda state, questions, **kwargs: {'model': jev.MODEL,
        'answers': {key: {'type': 'noul', 'noul': .01} for key in questions},
        'usage': {'input_tokens': 50, 'output_tokens': 20}, 'latency_ms': 1})
    middle = 'progress ok\n' * 7 + 'E       -  expected left\nE       +  actual right\n'
    text = 'progress ok\n' * 450 + middle + 'progress ok\n' * 450
    result = tool_execution.execute(store, run, 'diff', lines_script(text))
    assert 'E       -  expected left' in result['text'] and 'E       +  actual right' in result['text']


def test_excess_diagnostics_explicitly_require_recovery(planned):
    store, run, _ = planned
    result = tool_execution.execute(store, run, 'many-errors', [sys.executable, '-c', "print('ERROR: detailed failure\\n'*500)"])
    assert result['requires_read'] and not result['complete']
    assert len(result['text'].encode()) <= 4096
    assert result['summary'] is None
    offsets = [int(line[1:line.index(']')]) for line in result['text'].split('\n') if line.startswith('[')]
    assert offsets == sorted(offsets)


def test_failed_test_run_summary_is_recorded_and_its_failing_chunk_leads_the_reduction(planned, monkeypatch):
    import json
    store, run, _ = planned
    monkeypatch.setattr(jev, 'evaluate', lambda state, questions, **kwargs: {'model': jev.MODEL,
        'answers': {key: {'type': 'noul', 'noul': .01} for key in questions},
        'usage': {'input_tokens': 50, 'output_tokens': 20}, 'latency_ms': 1})
    # 480 lines of 32 bytes fill 20 complete 768-byte chunks, so the tail starts its own chunk.
    tail = ('_____ test_rejects_field _____\nE       AssertionError: expected accepted\n'
            '===== short test summary info =====\nFAILED tests/test_parser.py::test_rejects_field - AssertionError\n'
            '===== 1 failed, 3 passed in 0.50s =====\n')
    junit = ('<testsuites><testsuite><testcase classname="tests.test_parser" name="test_rejects_field">'
             '<failure message="AssertionError"/></testcase>' + '<testcase classname="tests.test_parser" name="test_ok"/>' * 3 +
             '</testsuite></testsuites>')
    script = (f"import sys\nopen(sys.argv[1], 'w').write({junit!r})\n"
              f"sys.stdout.write('warning: retry error budget 80%\\n' * 480 + {tail!r})\nraise SystemExit(1)")
    argv = [sys.executable, '-c', script, '{report}']
    result = tool_execution.execute(store, run, 'pytest', argv, report='junit-xml')
    assert result['summary'] == {'format': 'junit-xml', 'passed': 3, 'failed': 1, 'skipped': 0, 'errors': 0,
                                 'failing': ['tests.test_parser::test_rejects_field'], 'truncated': False}
    payload = json.loads(store.db.execute('SELECT payload FROM events WHERE event_id=?', (result['event_id'],)).fetchone()[0])
    assert payload['summary'] == result['summary'] and payload['command'] == argv
    assert not [path for path in (store.root / 'state/tool-output').iterdir()]
    # The head alone overflows the diagnostic selection; the failing chunk displaces head chunks.
    assert result['text'].startswith('[15360] _____ test_rejects_field')
    assert 'FAILED tests/test_parser.py::test_rejects_field' in result['text']
    assert 'retry error budget' in result['text'] and result['requires_read']
    assert len(result['text'].encode()) <= output_reduction.OUTPUT_BYTES
