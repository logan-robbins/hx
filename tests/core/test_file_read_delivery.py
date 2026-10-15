import hashlib
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from hx import file_read_delivery
from hx.errors import ValidationError


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE runtime_cycles(scope PRIMARY KEY,payload)')

    @contextmanager
    def transaction(self):
        with self.db:
            yield SimpleNamespace(db=self.db, _change=lambda: None)


def setup(monkeypatch, text):
    store = Store()
    receipt = {'path': '/source.py', 'bytes': len(text.encode()), 'sha256': hashlib.sha256(text.encode()).hexdigest(),
               'files': [{'path': '/source.py', 'stamp': 'immutable-stamp'}]}
    row = {'launch_id': 'launch', 'payload': {'capsule': '/capsule', 'worker_id': 'eng-001', 'native_session': 'session'}}
    observation = {'session_id': 'session', 'tool_use_id': 'call', 'tool_name': 'Read', 'tool_input': {'path': '/source.py'}}
    result = {'text': text, 'receipt': receipt, 'complete': True}
    monkeypatch.setattr(file_read_delivery, 'broker_result', lambda *args: result)
    monkeypatch.setattr(file_read_delivery, 'segment_slots', lambda *args: 10)
    return store, row, observation, result


def test_host_capped_transport_automatically_emits_every_character_before_success(monkeypatch):
    text = 'source 🦉\r\n' * 3500 + 'def existing_tail():\n    pass\n'
    store, row, observation, expected = setup(monkeypatch, text)
    out = []
    # Hosts can schedule independent handlers concurrently; delivery is indexed.
    chunks = file_read_delivery.split_delivery(text)
    for index in reversed(range(1, len(chunks))):
        assert file_read_delivery.emit(store, row, {}, observation, index, write=out.append) is None
    assert file_read_delivery.emit(store, row, {}, observation, 0, write=out.append) == expected
    segments = [json.loads(value)['hookSpecificOutput']['additionalContext'] for value in out]
    assert all(len(value.encode('utf-8')) <= 10000 for value in segments)
    ordered = sorted(segments, key=lambda value: int(value.split('segment ', 1)[1].split('/')[0]))
    assert ''.join(value.split('\n', 1)[1] for value in ordered) == text
    assert file_read_delivery.emit(store, row, {}, observation, 9, write=out.append) is None
    assert len(out) == len(chunks)



def test_missing_segment_never_produces_success(monkeypatch):
    store, row, observation, _ = setup(monkeypatch, 'x' * 20000)
    assert file_read_delivery.emit(store, row, {}, observation, 0, write=lambda value: None) is None
    assert file_read_delivery.emit(store, row, {}, observation, 2, write=lambda value: None) is None
    state = json.loads(store.db.execute('SELECT payload FROM runtime_cycles').fetchone()[0])
    assert state['emitted'] == [0, 2]


def test_changed_segment_identity_is_explicit_failure(monkeypatch):
    store, row, observation, result = setup(monkeypatch, 'x' * 20000)
    file_read_delivery.emit(store, row, {}, observation, 0, write=lambda value: None)
    result['receipt']['sha256'] = 'changed'
    with pytest.raises(ValidationError, match='identity changed'):
        file_read_delivery.emit(store, row, {}, observation, 1, write=lambda value: None)


def test_failed_write_never_acknowledges_segment(monkeypatch):
    store, row, observation, _ = setup(monkeypatch, 'small source')
    def failed(value):
        raise BrokenPipeError('native host disconnected')
    with pytest.raises(BrokenPipeError):
        file_read_delivery.emit(store, row, {}, observation, 0, write=failed)
    assert store.db.execute('SELECT count(*) FROM runtime_cycles').fetchone()[0] == 0
