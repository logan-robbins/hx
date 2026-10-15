import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx import hook_context, hook_subagent, partner_context, persona, streams
from hx.errors import ValidationError
from hx.file_read_delivery import segment_slots


def reconstruct(outputs):
    parts = []
    for value in outputs:
        context = json.loads(value)['hookSpecificOutput']['additionalContext']
        # Exercise the native per-string ceiling, including the receipt header.
        assert len(context) <= 10_000
        assert len(context.encode('utf-16-le')) // 2 <= 10_000
        assert len(context.encode('utf-8')) <= 10_000
        header, body = context.split('\n', 1)
        number = int(header.split('segment ', 1)[1].split('/', 1)[0])
        parts.append((number, body))
    return ''.join(body for _, body in sorted(parts))


def test_parallel_handlers_freeze_one_complete_body_and_use_bounded_files(instance):
    text = 'Full instruction one.\n' + '😀' * 30_000 + '\nFINAL INSTRUCTION MUST SURVIVE.\n'
    outputs, builds = [], []
    slots = segment_slots(instance, 'partner')
    def build():
        builds.append(1)
        return text
    def deliver(index):
        return partner_context.emit(instance, {'source': 'startup', 'session_id': 'S'}, index,
                                    build=build, write=outputs.append)
    with ThreadPoolExecutor(max_workers=slots) as workers:
        list(workers.map(deliver, reversed(range(slots))))
    assert builds == [1]
    assert reconstruct(outputs) == text
    receipt = partner_context.require_delivered(instance)
    assert receipt['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    state_path, _ = partner_context.paths(instance)
    state = json.loads(state_path.read_text())
    assert len(state['seen']) == slots
    assert all(item['bytes'] <= 40_000 for item in state['files'])


def test_emission_failure_never_acknowledges_a_prefix_and_new_round_refreshes(instance):
    partner_context.invalidate(instance)
    def failed(value):
        raise BrokenPipeError('Host pipe closed.')
    with pytest.raises(BrokenPipeError):
        partner_context.emit(instance, {'session_id': 'S'}, 0, build=lambda: 'complete body', write=failed)
    with pytest.raises(ValidationError, match='has not been delivered'):
        partner_context.require_delivered(instance)
    outputs = []
    for index in range(segment_slots(instance, 'partner')):
        partner_context.emit(instance, {'session_id': 'S'}, index, build=lambda: pytest.fail('Frozen body must remain exact.'), write=outputs.append)
    assert reconstruct(outputs) == 'complete body'
    before = partner_context.require_delivered(instance)
    outputs.clear()
    for index in range(segment_slots(instance, 'partner')):
        partner_context.emit(instance, {'session_id': 'S'}, index, build=lambda: 'updated current notes', write=outputs.append)
    assert reconstruct(outputs) == 'updated current notes'
    assert before['generation'] != partner_context.require_delivered(instance)['generation']
    def broken_source():
        raise ValidationError('Required instruction source is missing.')
    with pytest.raises(ValidationError, match='missing'):
        partner_context.emit(instance, {'session_id': 'S'}, 0, build=broken_source, write=outputs.append)
    with pytest.raises(ValidationError, match='has not been delivered'):
        partner_context.require_delivered(instance)


def test_partner_boundary_contains_full_instructions_and_current_notes_in_one_native_round(instance, capsys):
    (instance / 'personas/partner.md').write_text('Partner instruction.\n' + 'x' * 24_000 + '\nPERSONA TAIL.\n')
    (instance / 'PARTNER.md').write_text('# Current notes\n' + 'n' * 24_000 + '\nCURRENT NOTES TAIL.\n')
    payload = {'source': 'clear', 'session_id': 'S'}
    outputs = []
    for index in reversed(range(segment_slots(instance, 'partner'))):
        assert hook_context.handle({**payload, '_hx_context_segment': index}, 'partner', instance, env={}) == (0, '')
        outputs.extend(capsys.readouterr().out.splitlines())
    text = reconstruct(outputs)
    assert text == persona.boundary(instance, 'partner', env={})['text']
    assert 'PERSONA TAIL.' in text and 'CURRENT NOTES TAIL.' in text
    assert sum(row['event'] == 'boundary' for row in streams.iter_records(streams.main_stream(instance, 'partner'))) == 1
    assert partner_context.require_delivered(instance)['bytes'] == len(text.encode())


def test_child_context_and_digest_are_delivered_whole_without_duplicate_side_effects(instance, capsys, monkeypatch):
    from hx import companion
    monkeypatch.setattr(companion, 'wake_due', lambda *a, **kw: [])
    (instance / 'config/partner/SUBAGENTS.md').write_text('Child rule.\n' + 'c' * 20_000 + '\nCHILD RULE TAIL.\n')
    start = {'session_id': 'S', 'agent_id': 'child-1', 'tool_input': {'prompt': 'Complete child task.'}}
    outputs = []
    slots = segment_slots(instance, 'partner')
    for index in reversed(range(slots)):
        assert hook_subagent.start({**start, '_hx_context_segment': index}, 'partner', instance, env={}) == (0, '')
        outputs.extend(capsys.readouterr().out.splitlines())
    text = reconstruct(outputs)
    assert 'CHILD RULE TAIL.' in text and 'Complete child task.' in text
    assert sum(row['event'] == 'spawned' for row in streams.iter_records(streams.main_stream(instance, 'partner'))) == 1
    assert sum(row['event'] == 'open' for row in streams.iter_records(streams.stream_path(instance, 'partner', 'partner-s001'))) == 1
    hook_subagent.stop({'agent_id': 'child-1'}, 'partner', instance, env={})
    digest = hook_subagent.digest_path(instance, 'partner', 'partner-s001')
    digest.write_text('Complete result.\n' + 'r' * 20_000 + '\nRESULT TAIL.\n')
    outputs.clear()
    result = {'session_id': 'S', 'tool_use_id': 'result-1', 'tool_response': {'agentId': 'child-1', 'status': 'completed'}}
    for index in reversed(range(slots)):
        hook_subagent.result({**result, '_hx_context_segment': index}, 'partner', instance, env={})
        outputs.extend(capsys.readouterr().out.splitlines())
    assert reconstruct(outputs).endswith(digest.read_text())
    assert sum(row['event'] == 'subagent_result' for row in streams.iter_records(streams.main_stream(instance, 'partner'))) == 1
