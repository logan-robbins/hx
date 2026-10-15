import hashlib
from pathlib import Path

import pytest

from hx import file_reads, instruction_materials
from hx.errors import ValidationError


def setup(tmp_path, text):
    path = tmp_path / 'source.py'
    path.write_text(text)
    row = {'payload': {'required_files': []}}
    task = {'workdir': str(tmp_path)}
    call = {'tool_name': 'Read', 'tool_input': {'file_path': str(path)}}
    return path, row, task, call


@pytest.mark.parametrize('text', ['', 'one\nsecond\n', 'unicode 🦉\r\nlast', 'x' * 90000])
def test_complete_source_snapshot_retains_every_byte(tmp_path, text):
    path, row, task, call = setup(tmp_path, text)
    actual, receipt = file_reads.prepare(row, task, call)
    assert actual == text
    assert receipt['bytes'] == len(path.read_bytes())
    assert receipt['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert file_reads.delivered(receipt, {'tool_response': actual})


@pytest.mark.parametrize('args', [{'offset': 1}, {'limit': 999999}, {'start_line': 1}, {'endLine': 8}, {'range': [1, 2]}])
def test_every_explicit_slice_is_rejected(tmp_path, args):
    _, row, task, call = setup(tmp_path, 'complete\n')
    call['tool_input'].update(args)
    with pytest.raises(ValidationError, match='bounded file slices are prohibited'):
        file_reads.prepare(row, task, call)


@pytest.mark.parametrize('response', [
    'first', {'text': 'first'}, {'text': 'first\nlast\n', 'truncated': True},
    {'content': 'first\nlast\n', 'complete': False}, {'content': [{'type': 'text', 'text': 'first'}]},
    {'text': 'first\nlast\n', 'has_more': True},
])
def test_partial_or_contradictory_native_result_never_certifies_delivery(tmp_path, response):
    _, row, task, call = setup(tmp_path, 'first\nlast\n')
    _, receipt = file_reads.prepare(row, task, call)
    assert not file_reads.delivered(receipt, {'tool_response': response})


@pytest.mark.parametrize('response', [
    {'text': 'first\nlast\n'}, {'file': {'content': 'first\nlast\n'}},
    [{'type': 'text', 'text': 'first\nlast\n'}], '     1→first\n     2→last\n',
])
def test_complete_known_native_shapes_are_verified(tmp_path, response):
    _, row, task, call = setup(tmp_path, 'first\nlast\n')
    _, receipt = file_reads.prepare(row, task, call)
    assert file_reads.delivered(receipt, {'tool_response': response})


def test_instruction_chain_is_one_logical_exact_read_from_any_part(tmp_path):
    text = 'Must retain this exact instruction 🦉.\n' * 2500
    refs, files = instruction_materials.prepare(text, tmp_path / 'instructions', channel='rules', sections=['rules'])
    instruction_materials.install(refs, files)
    assert len(refs) > 1
    row = {'payload': {'required_files': refs}}
    for ref in [refs[0], refs[-1]]:
        actual, receipt = file_reads.prepare(row, {'workdir': str(tmp_path)},
            {'tool_name': 'Read', 'tool_input': {'path': ref['path']}})
        assert actual == text
        assert len(receipt['files']) == len(refs)
        assert receipt['sha256'] == hashlib.sha256(text.encode()).hexdigest()


def test_changed_continuation_is_an_explicit_failure(tmp_path):
    refs, files = instruction_materials.prepare('exact\n' * 15000, tmp_path / 'instructions', channel='rules', sections=['rules'])
    instruction_materials.install(refs, files)
    Path(refs[-1]['path']).write_text('damaged tail')
    with pytest.raises(ValidationError):
        file_reads.prepare({'payload': {'required_files': refs}}, {'workdir': str(tmp_path)},
                          {'tool_name': 'Read', 'tool_input': {'path': refs[0]['path']}})


def test_partial_read_blocks_actions_and_completion_until_verified_retry(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from hx import runtime_policy, native_tools
    db = sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE native_reads (launch_id,session_id,actor_id,call_id,checkpoint_id,read_key,source_stamp,success);
        CREATE TABLE native_tool_calls (launch_id,session_id,actor_id,call_id,status);''')
    tx = SimpleNamespace(db=db)
    path, row, task, call = setup(tmp_path, 'first\nlast\n')
    _, receipt = file_reads.prepare(row, task, call)
    key, stamp = file_reads.identity(receipt)
    row.update(launch_id='launch')
    row['payload']['checkpoint_id'] = 'checkpoint'
    db.execute('INSERT INTO native_reads VALUES(?,?,?,?,?,?,?,?)', ('launch', 'session', '', 'partial', 'checkpoint', key, stamp, 0))
    db.execute('INSERT INTO native_tool_calls VALUES(?,?,?,?,?)', ('launch', 'session', '', 'partial', 'settled'))
    runtime_policy.settled(tx, 'launch', 'session', '', 'partial', {'tool_response': 'first'})
    assert native_tools.pending(tx, 'launch') == 1
    with pytest.raises(native_tools.AdmissionDenied, match='retry that whole file'):
        runtime_policy.require_complete_reads(tx, row, 'session', '', None)
    runtime_policy.require_complete_reads(tx, row, 'session', '', (key, stamp))
    db.execute('INSERT INTO native_reads VALUES(?,?,?,?,?,?,?,?)', ('launch', 'session', '', 'retry', 'checkpoint', key, stamp, 0))
    db.execute('INSERT INTO native_tool_calls VALUES(?,?,?,?,?)', ('launch', 'session', '', 'retry', 'settled'))
    runtime_policy.settled(tx, 'launch', 'session', '', 'retry', {'tool_response': 'first\nlast\n'})
    assert native_tools.pending(tx, 'launch') == 0
    runtime_policy.require_complete_reads(tx, row, 'session', '', None)


@pytest.mark.parametrize('name,key', [('exec_command', 'cmd'), ('Bash', 'command'), ('bash', 'command'),
                                     ('run_terminal_command', 'command')])
def test_actual_shell_broker_is_one_complete_file_operation(tmp_path, name, key):
    path, row, task, _ = setup(tmp_path, 'prefix\nexisting tail\n')
    call = {'tool_name': name, 'tool_input': {key: 'hx file-read ' + str(path)}}
    text, receipt = file_reads.prepare(row, task, call)
    assert text == path.read_text()
    assert receipt['bytes'] == path.stat().st_size


@pytest.mark.parametrize('command', ['hx file-read x | head', 'cd directory && hx file-read x',
                                     'hx file-read x y', 'hx file-read x --limit 10'])
def test_shell_broker_composition_fails_before_execution(command):
    with pytest.raises(ValidationError, match='file-read'):
        file_reads.read_input({'tool_name': 'exec_command', 'tool_input': {'cmd': command}})


def test_shell_broker_honors_actual_exec_workdir(tmp_path):
    directory = tmp_path / 'nested'
    directory.mkdir()
    (directory / 'file.py').write_text('exact nested source\n')
    text, receipt = file_reads.prepare({'payload': {'required_files': []}}, {'workdir': str(tmp_path)},
        {'tool_name': 'exec_command', 'tool_input': {'cmd': 'hx file-read file.py', 'workdir': str(directory)}})
    assert text == 'exact nested source\n'
    assert receipt['path'] == str(directory / 'file.py')


def test_sparse_huge_file_is_refused_before_any_content_is_loaded(tmp_path, monkeypatch):
    path, row, task, call = setup(tmp_path, '')
    with path.open('wb') as handle:
        handle.truncate(8 * 1024 * 1024 * 1024)
    def forbidden(*args, **kwargs):
        raise AssertionError('oversized file must be refused before opening content')
    monkeypatch.setattr(Path, 'open', forbidden)
    with pytest.raises(ValidationError, match='no content was loaded or delivered'):
        file_reads.prepare(row, task, call)


def test_non_file_stream_is_refused_before_open(tmp_path, monkeypatch):
    import os
    path = tmp_path / 'named-pipe'
    os.mkfifo(path)
    def forbidden(*args, **kwargs):
        raise AssertionError('stream must be refused before opening')
    monkeypatch.setattr(Path, 'open', forbidden)
    with pytest.raises(ValidationError, match='regular files'):
        file_reads.prepare({'payload': {'required_files': []}}, {'workdir': str(tmp_path)},
            {'tool_name': 'Read', 'tool_input': {'path': str(path)}})


def test_multiple_failed_whole_reads_can_each_retry_without_deadlock(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from hx import runtime_policy
    db = sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE native_reads (launch_id,session_id,actor_id,call_id,checkpoint_id,read_key,source_stamp,success);
        CREATE TABLE native_tool_calls (launch_id,session_id,actor_id,call_id,status);''')
    tx = SimpleNamespace(db=db)
    row = {'launch_id': 'launch', 'payload': {'checkpoint_id': 'checkpoint'}}
    for key in ['file-a', 'file-b']:
        db.execute('INSERT INTO native_reads VALUES(?,?,?,?,?,?,?,?)', ('launch', 'session', '', key, 'checkpoint', key, '{}', -1))
        db.execute('INSERT INTO native_tool_calls VALUES(?,?,?,?,?)', ('launch', 'session', '', key, 'settled'))
    runtime_policy.require_complete_reads(tx, row, 'session', '', ('file-a', '{}'))
    runtime_policy.require_complete_reads(tx, row, 'session', '', ('file-b', '{}'))
