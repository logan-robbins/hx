"""Recoverable portable map publication; validate every collision before writing."""
import hashlib
import json
import shutil
from pathlib import Path

from .continuity_store import Conflict, canonical
from .errors import ValidationError
from .store import atomic_write_text


def stamp(path):
    if path.is_symlink():
        raise Conflict('map export cannot replace a symlink')
    if not path.exists():
        return None
    if path.stat().st_size > 65536:
        raise ValidationError('map shard exceeds 64 KiB')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recover(repository, journal, snapshot=None):
    manifest = journal / 'journal.json'
    if not manifest.exists():
        return False
    if manifest.stat().st_size > 1048576:
        raise ValidationError('map export journal exceeds 1 MiB')
    state = json.loads(manifest.read_text())
    if snapshot is not None and state.get('snapshot') != snapshot:
        raise Conflict('recover the interrupted snapshot before exporting a different one')
    for item in state['files']:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[:2] != ('.hx', 'map'):
            raise Conflict('invalid map export target')
        target = repository / relative
        if not target.resolve().is_relative_to(repository.resolve()):
            raise Conflict('map export target escapes repository')
        if stamp(target) not in {item['before'], item['after']}:
            raise Conflict('map export recovery found a local edit: ' + item['path'])
        if item['after'] is not None:
            staged = journal / item['staged']
            if stamp(staged) != item['after']:
                raise Conflict('map export recovery payload changed')
            from .appmap import source_anchor
            try:
                record = json.loads(staged.read_text())
            except ValueError:
                raise Conflict('map export journal contains invalid JSON') from None
            if isinstance(record, dict):
                for anchor in record.get('anchors', []):
                    if source_anchor(repository, anchor['path'], anchor.get('symbol'))['sha256'] != anchor['sha256']:
                        raise Conflict('map export recovery source changed')
    for item in state['files']:
        target = repository / item['path']
        if stamp(target) == item['after']:
            continue
        if item['after'] is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write_text(target, (journal / item['staged']).read_text())
    # Remove the journal last. An interrupted replay sees only old/new hashes.
    manifest.unlink()
    shutil.rmtree(journal)
    return True


def publish(repository, journal, writes, deletes=(), *, snapshot=None):
    if (journal / 'journal.json').exists():
        raise Conflict('recover the previous map export before publishing another snapshot')
    if journal.exists():
        shutil.rmtree(journal)  # No committed manifest means no destination was touched.
    journal.mkdir(parents=True)
    files = []
    for index, (target, text) in enumerate(writes):
        if index >= 4096:
            raise ValidationError('full export exceeds 4096 shards; use scoped publication')
        staged = str(index) + '.json'
        atomic_write_text(journal / staged, text)
        files.append({'path': target.relative_to(repository).as_posix(), 'before': stamp(target),
                      'after': stamp(journal / staged), 'staged': staged})
    for target in deletes:
        files.append({'path': target.relative_to(repository).as_posix(), 'before': stamp(target), 'after': None})
    manifest = canonical({'files': files, 'snapshot': snapshot}) + '\n'
    if len(manifest.encode()) > 1048576:
        raise ValidationError('map export journal exceeds 1 MiB; use scoped publication')
    atomic_write_text(journal / 'journal.json', manifest)
    recover(repository, journal)
