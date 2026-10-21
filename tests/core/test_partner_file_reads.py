import json

import pytest

from hx import partner_context, partner_file_reads as reads
from hx.errors import ValidationError
from hx.file_read_delivery import segment_slots
from .test_partner_context_delivery import reconstruct


def boundary(instance):
    partner_context.invalidate(instance)
    for index in range(segment_slots(instance, 'partner')):
        partner_context.emit(instance, {'session_id': 'S'}, index,
                             build=lambda: 'Complete Partner instructions and current notes.', write=lambda value: None)


def observation(path, call='R', *, actor='', args=None, shell=False):
    return {'session_id': 'S', 'agent_id': actor, 'tool_use_id': call, 'cwd': str(path.parent),
            'tool_name': 'Bash' if shell else 'Read',
            'tool_input': {'command': f'hx file-read {path}'} if shell else {'file_path': str(path), **(args or {})}}


def deliver(instance, request):
    outputs, completed = [], []
    for index in reversed(range(segment_slots(instance, 'partner'))):
        answer = reads.emit(instance, {**request, 'tool_response': 'native preview was truncated'}, index, write=outputs.append)
        if answer:
            completed.append(answer)
    assert len(completed) == 1
    return reconstruct(outputs), completed[0]


@pytest.mark.parametrize('shell', [False, True])
def test_partner_and_child_read_complete_legacy_unicode_and_tail_once(instance, tmp_path, shell):
    boundary(instance)
    target = tmp_path / 'legacy.py'
    text = '# Existing code\n' + '😀' * 14_000 + '\ndef reusable_existing_tail(): return 42\n'
    target.write_text(text)
    request = observation(target, actor='child-1', shell=shell)
    admitted = reads.admit(instance, request)
    assert admitted['receipt']['bytes'] == len(text.encode())
    assert reads.require_admitted(instance, target)['identity'] == admitted['identity']
    with pytest.raises(ValidationError, match='pending'):
        reads.require_delivered(instance)
    delivered, complete = deliver(instance, request)
    assert delivered == text and complete['complete']
    from hx import streams
    from hx.continuity_store import ContinuityStore
    evidence = [row for row in streams.iter_records(streams.main_stream(instance, 'partner')) if row.get('event') == 'complete_file_read']
    assert len(evidence) == 1
    with ContinuityStore(instance) as ledger:
        with ledger.transaction() as tx:
            assert tx.read_artifact(evidence[0]['artifact_hash']).decode() == text
            assert json.loads(tx.read_artifact(evidence[0]['receipt_artifact_hash'])) == complete['receipt']
    reads.require_delivered(instance)
    assert reads.emit(instance, request, 0, write=lambda value: pytest.fail('No duplicate delivery.')) is None
    with pytest.raises(ValidationError, match='already delivered'):
        reads.admit(instance, observation(target, 'another', actor='child-1', shell=shell))


def test_partial_read_failure_blocks_other_work_but_allows_same_file_retry(instance, tmp_path):
    boundary(instance)
    target = tmp_path / 'source.py'
    target.write_text('x' * 20_000 + '\nORIGINAL TAIL\n')
    request = observation(target)
    reads.admit(instance, request)
    assert reads.emit(instance, request, 0, write=lambda value: None) is None
    with pytest.raises(ValidationError, match='pending'):
        reads.require_delivered(instance, {'tool_name': 'Bash', 'tool_input': {'command': 'hx plan ready P'}})
    retry = observation(target, 'retry')
    reads.require_delivered(instance, retry)
    reads.admit(instance, retry)
    delivered, _ = deliver(instance, retry)
    assert delivered == target.read_text()
    reads.require_delivered(instance)


def test_read_source_change_fails_without_acknowledging_preview_and_retry_delivers_new_file(instance, tmp_path):
    boundary(instance)
    target = tmp_path / 'source.py'
    target.write_text('first source\n')
    request = observation(target)
    reads.admit(instance, request)
    target.write_text('changed source with a new tail\n')
    with pytest.raises(ValidationError, match='changed after'):
        reads.emit(instance, request, 0, write=lambda value: pytest.fail('No unverified output.'))
    retry = observation(target, 'retry')
    reads.admit(instance, retry)
    assert deliver(instance, retry)[0] == target.read_text()
    reads.require_delivered(instance)


def test_partner_read_prohibits_slices_and_refuses_before_oversized_source_execution(instance, tmp_path):
    boundary(instance)
    target = tmp_path / 'source.py'
    target.write_text('a complete file\n')
    for args in ({'offset': 1}, {'limit': 10}, {'start_line': 1}):
        with pytest.raises(ValidationError, match='slices are prohibited'):
            reads.admit(instance, observation(target, args=args))
    target.write_text('x' * 220_000)
    with pytest.raises(ValidationError, match='exceeds the Partner context budget'):
        reads.admit(instance, observation(target))
    with pytest.raises(ValidationError, match='admitted native'):
        reads.require_admitted(instance, target)


def test_partner_material_parts_are_each_a_complete_read_gated_until_all_are_read(instance):
    from pathlib import Path
    from hx import instruction_materials
    boundary(instance)
    text = 'Full required instruction.\n' + 'i' * 70_000 + '\nFINAL REQUIRED INSTRUCTION.\n'
    references, files = instruction_materials.prepare(text, instance / 'run/partner/source-material')
    instruction_materials.install(references, files)
    first, second = (Path(ref['path']) for ref in references)
    request = observation(second, 'P2')
    assert reads.admit(instance, request)['receipt']['bytes'] == references[1]['bytes']
    assert deliver(instance, request)[0] == second.read_text()
    shell = {'session_id': 'S', 'tool_use_id': 'B', 'tool_name': 'Bash', 'tool_input': {'command': 'true'}}
    with pytest.raises(ValidationError, match=f'read {first} next \\(part 1 of 2\\)'):
        reads.require_delivered(instance, shell)
    request = observation(first, 'P1')
    reads.admit(instance, request)
    assert deliver(instance, request)[0] == first.read_text()
    reads.require_delivered(instance, shell)


def test_partner_read_resolves_an_immutable_repository_file_without_local_path(instance, tmp_path):
    import subprocess
    from hx import repo_service
    repo = tmp_path / 'product'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True).stdout.decode().strip()
    git('init', '-q')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    text = 'immutable = True\n' + '#' * 20_000 + '\nREPOSITORY TAIL\n'
    (repo / 'source.py').write_text(text)
    git('add', '.')
    git('commit', '-qm', 'immutable complete file')
    namespace = repo_service.register(instance, repo)['namespace']
    boundary(instance)
    request = {'session_id': 'S', 'tool_use_id': 'G', 'tool_name': 'Bash', 'cwd': str(instance),
               'tool_input': {'command': f'hx repo file --namespace {namespace} --at HEAD --path source.py'}}
    state = reads.admit(instance, request)
    assert reads.require_admitted(instance, state['receipt']['path']) == state
    assert deliver(instance, request)[0] == text


def test_independent_whole_reads_reserve_capacity_in_parallel_and_only_retry_the_same_path(instance, tmp_path):
    from hx.continuity_store import digest
    boundary(instance)
    first, second = tmp_path / 'first.py', tmp_path / 'second.py'
    first.write_text('First complete source.\n')
    second.write_text('Second complete source.\n')
    a, b = observation(first, 'A'), observation(second, 'B')
    reads.admit(instance, a)
    reads.require_delivered(instance, b)
    reads.admit(instance, b)
    retry = observation(first, 'A-retry')
    reads.admit(instance, retry)
    second_state = json.loads((reads.directory(instance) / (digest(['S', '', 'B']) + '.json')).read_text())
    assert second_state['status'] == 'admitted'
    assert deliver(instance, retry)[0] == first.read_text()
    with pytest.raises(ValidationError, match='pending'):
        reads.require_delivered(instance)
    assert deliver(instance, b)[0] == second.read_text()
    reads.require_delivered(instance)


def test_unadmitted_native_preview_invalidates_context_and_blocks_the_next_tool(instance, tmp_path, capsys):
    import io
    from hx import hooks
    boundary(instance)
    target = tmp_path / 'source.py'
    target.write_text('whole source with a required tail\n')
    request = observation(target, 'unadmitted')
    env = {'HARNESS_ROOT': str(instance), 'HARNESS_ID': 'partner'}
    # Partner observation hooks retain their nonblocking error behavior. The
    # failed delivery still persists a gate for the next attempted action.
    hooks.main(['--id', 'partner', 'log'], stdin=io.StringIO(json.dumps({**request, 'tool_response': 'native prefix only'})), env=env)
    with pytest.raises(ValidationError, match='has not been delivered'):
        partner_context.require_delivered(instance)
    next_tool = {'tool_name': 'Bash', 'tool_input': {'command': 'hx loop --status'}}
    assert hooks.main(['--id', 'partner', 'guard'], stdin=io.StringIO(json.dumps(next_tool)), env=env) == 2
    assert 'complete Partner context has not been delivered' in capsys.readouterr().err


def test_missing_native_identity_cannot_escape_the_persistent_delivery_gate(instance, tmp_path):
    boundary(instance)
    target = tmp_path / 'source.py'
    target.write_text('complete source\n')
    request = observation(target)
    request.pop('tool_use_id')
    with pytest.raises(ValidationError, match='tool-call identity'):
        reads.emit(instance, request, 0, write=lambda value: pytest.fail('No unadmitted output.'))
    with pytest.raises(ValidationError, match='has not been delivered'):
        partner_context.require_delivered(instance)
