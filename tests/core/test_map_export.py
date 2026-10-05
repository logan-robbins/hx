import pytest
from hx import map_export
from hx.continuity_store import Conflict


def test_interrupted_export_replays_only_owned_writes(tmp_path, monkeypatch):
    repository = tmp_path / 'repo'
    first = repository / '.hx/map/components/a.json'
    second = repository / '.hx/map/components/b.json'
    first.parent.mkdir(parents=True)
    first.write_text('old-a')
    second.write_text('old-b')
    journal = tmp_path / 'journal'
    original = map_export.atomic_write_text
    def interrupt(path, text):
        if path == second:
            raise OSError('interrupted')
        original(path, text)
    monkeypatch.setattr(map_export, 'atomic_write_text', interrupt)
    with pytest.raises(OSError):
        map_export.publish(repository, journal, [(first, '"new-a"'), (second, '"new-b"')], snapshot='S')
    assert first.read_text() == '"new-a"' and second.read_text() == 'old-b'
    monkeypatch.setattr(map_export, 'atomic_write_text', original)
    second.write_text('operator-edit')
    with pytest.raises(Conflict, match='local edit'):
        map_export.recover(repository, journal, 'S')
    assert second.read_text() == 'operator-edit'
    second.write_text('old-b')
    with pytest.raises(Conflict, match='different'):
        map_export.recover(repository, journal, 'different')
    assert map_export.recover(repository, journal, 'S')
    assert second.read_text() == '"new-b"' and not journal.exists()
