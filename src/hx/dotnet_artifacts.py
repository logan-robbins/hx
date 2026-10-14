"""Immutable restore/build trees and safe private materialization."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tarfile

from . import checks, fingerprints
from .continuity_store import canonical, digest
from .errors import ValidationError

MAX_TREE_BYTES = 512 * 1024 * 1024
MAX_FILES = 50000


def files(tree):
    result = []
    total = 0
    for path in sorted(Path(tree).rglob('*')):
        if path.is_symlink():
            raise ValidationError('build artifact tree contains a symlink; immutable closure is unsupported')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValidationError('build artifact tree contains a non-regular output')
        size = path.stat().st_size
        total += size
        if total > MAX_TREE_BYTES or len(result) >= MAX_FILES:
            raise ValidationError('build artifact tree exceeds its byte/file bound')
        hasher = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(fingerprints.CHUNK_BYTES), b''):
                hasher.update(chunk)
        result.append({'path': path.relative_to(tree).as_posix(), 'hash': hasher.hexdigest(),
                       'size': size, 'mode': path.stat().st_mode & 0o777})
    return result


def capture(tx, tree, job_id, output):
    manifest = files(tree)
    if not manifest:
        raise ValidationError('successful build produced no isolated artifacts')
    with tarfile.open(output, 'w') as archive:
        for item in manifest:
            archive.add(Path(tree) / item['path'], arcname=item['path'], recursive=False)
    expected = {item['path']: item for item in manifest}
    seen = set()
    with tarfile.open(output, 'r') as archive:
        for member in archive:
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise ValidationError('captured build archive has an unexpected output')
            seen.add(member.name)
            hasher = hashlib.sha256()
            with archive.extractfile(member) as source:
                for chunk in iter(lambda: source.read(fingerprints.CHUNK_BYTES), b''):
                    hasher.update(chunk)
            if member.size != expected[member.name]['size'] or hasher.hexdigest() != expected[member.name]['hash']:
                raise ValidationError('build output changed while snapshotting its artifact')
    if seen != expected.keys() or files(tree) != manifest:
        raise ValidationError('build output changed while snapshotting its artifact')
    artifact = tx.put_artifact_file(output, owner_type='dotnet_build', owner_id=job_id,
                                    slot='tree', max_bytes=MAX_TREE_BYTES + 64 * 1024 * 1024)
    return artifact, manifest


def intact(store, build):
    if not isinstance(build, dict) or not build.get('artifact_hash') or not build.get('manifest'):
        return False
    return checks._artifact_intact(store, build['artifact_hash']) and digest(build['manifest']) == build.get('manifest_hash')


def materialize(store, build, destination):
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValidationError('artifact destination must be absent or empty')
    if not intact(store, build):
        raise ValidationError('build artifact is missing or corrupt')
    expected = {item['path']: item for item in build['manifest']}
    destination.mkdir(parents=True, exist_ok=True)
    seen = set()
    with tarfile.open(store.artifacts / build['artifact_hash'], 'r') as archive:
        for member in archive:
            path = Path(member.name)
            if member.name not in expected or path.is_absolute() or '..' in path.parts or not member.isfile() or member.name in seen:
                raise ValidationError('build archive has an unsafe or unexpected member')
            item = expected[member.name]
            if member.size != item['size']:
                raise ValidationError('build archive member size differs from its manifest')
            seen.add(member.name)
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            hasher = hashlib.sha256()
            with archive.extractfile(member) as source, target.open('xb') as output:
                for chunk in iter(lambda: source.read(fingerprints.CHUNK_BYTES), b''):
                    hasher.update(chunk)
                    output.write(chunk)
            if hasher.hexdigest() != item['hash']:
                raise ValidationError('build archive member differs from its manifest')
            os.chmod(target, item['mode'])
    if seen != expected.keys():
        raise ValidationError('build archive is missing declared outputs')
    return {'build_id': build['build_id'], 'artifact_hash': build['artifact_hash'],
            'destination': str(destination), 'files': len(seen), 'manifest_hash': build['manifest_hash']}
