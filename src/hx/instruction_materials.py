"""Exact immutable material chains consumed as one logical complete read.

Each numbered physical file carries a deterministic continuation footer. The
broker verifies every complete physical file, removes only the verified footer,
and returns the original UTF-8 payload without model-controlled pagination.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import store
from .errors import ValidationError
from .file_limits import MAX_FILE_BYTES


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def prepare(text: str, directory: Path, **metadata):
    data = text.encode('utf-8')
    bundle_hash = _hash(data)
    directory = Path(directory) / bundle_hash
    # Reserve footer space independently of content or the caller's path length.
    reserve = len(str(directory).encode()) + 512
    capacity = MAX_FILE_BYTES - reserve
    if capacity < 4:
        raise ValidationError('material path leaves no room for a complete part')
    chunks = []
    remaining = data
    while remaining:
        end = min(capacity, len(remaining))
        while end < len(remaining) and remaining[end] & 0xC0 == 0x80:
            end -= 1
        chunks.append(remaining[:end])
        remaining = remaining[end:]
    chunks = chunks or [b'']
    paths = [directory / f'{index:04d}.md' for index in range(1, len(chunks) + 1)]
    refs, files = [], {}
    for index, (path, payload) in enumerate(zip(paths, chunks), 1):
        next_path = str(paths[index]) if index < len(paths) else None
        continuation = f'NEXT {next_path}' if next_path else 'END OF COMPLETE MATERIAL'
        footer = (f'\n\n<!-- HX MATERIAL {bundle_hash} PART {index}/{len(paths)}; '
                  f'PAYLOAD BYTES {len(payload)}; {continuation}. '
                  'The runtime resolves the complete chain in the same logical read. -->\n').encode()
        physical = payload + footer
        if len(physical) > MAX_FILE_BYTES:
            raise ValidationError('complete material part exceeds the file token cap')
        refs.append({**metadata, 'path': str(path), 'sha256': _hash(physical), 'bytes': len(physical),
                     'payload_bytes': len(payload), 'payload_sha256': _hash(payload),
                     'part': index, 'parts': len(paths), 'next_path': next_path,
                     'bundle_sha256': bundle_hash, 'bundle_bytes': len(data)})
        files[str(path)] = physical
    return refs, files


def verify_file(reference):
    path = Path(reference['path'])
    if path.is_symlink() or not path.is_file():
        raise ValidationError('required full instruction file is missing or not immutable')
    expected = reference['bytes']
    if type(expected) is not int or not 0 <= expected <= MAX_FILE_BYTES:
        raise ValidationError('required material exceeds the file token cap')
    if path.stat().st_size != expected:
        raise ValidationError('required full instruction file is missing or altered')
    with path.open('rb') as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) != expected or _hash(data) != reference['sha256']:
        raise ValidationError('required full instruction file is missing or altered')
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError('required material exceeds the file token cap')
    return data


def read_bundle(references):
    if not references:
        return b''
    first = references[0]
    if len(references) != first['parts']:
        raise ValidationError('required material chain is incomplete')
    payloads = []
    for index, reference in enumerate(references, 1):
        following = references[index]['path'] if index < len(references) else None
        if (reference['part'] != index or reference['parts'] != len(references)
                or reference['next_path'] != following
                or reference['bundle_sha256'] != first['bundle_sha256']
                or reference['bundle_bytes'] != first['bundle_bytes']):
            raise ValidationError('required material chain identity or continuation changed')
        data = verify_file(reference)
        payload = data[:reference['payload_bytes']]
        if _hash(payload) != reference['payload_sha256']:
            raise ValidationError('required material payload identity changed')
        payloads.append(payload)
    complete = b''.join(payloads)
    if len(complete) != first['bundle_bytes'] or _hash(complete) != first['bundle_sha256']:
        raise ValidationError('required material chain is missing content')
    return complete


def install(references, files):
    for reference in references:
        path = Path(reference['path'])
        if path.exists() or path.is_symlink():
            verify_file(reference)
        else:
            store.atomic_write_text(path, files[str(path)].decode('utf-8'))
    read_bundle(references)
    if references:
        fields = {"path", "sha256", "bytes", "payload_bytes", "payload_sha256", "part", "parts", "next_path", "bundle_sha256", "bundle_bytes"}
        intrinsic = [{key: value for key, value in reference.items() if key in fields} for reference in references]
        body = json.dumps({"schema_version": 1, "files": intrinsic}, ensure_ascii=False, separators=(",", ":"))
        if len(body.encode()) > MAX_FILE_BYTES:
            raise ValidationError("complete material manifest exceeds its file cap")
        manifest = Path(references[0]["path"]).parent / "bundle.json"
        if manifest.exists():
            if manifest.is_symlink() or json.loads(manifest.read_text()) != {"schema_version": 1, "files": intrinsic}:
                raise ValidationError("required material chain manifest changed")
        else:
            store.atomic_write_text(manifest, body)


def discover_bundle(path):
    """Discover and verify managed continuations without a model following pointers."""
    target = Path(path).absolute()
    manifest = target.parent / "bundle.json"
    if target.suffix != ".md" or not manifest.is_file() or manifest.is_symlink():
        return []
    if manifest.stat().st_size > MAX_FILE_BYTES:
        raise ValidationError('complete material manifest exceeds its file cap')
    with manifest.open('rb') as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValidationError("complete material manifest exceeds its file cap")
    try:
        body = json.loads(raw)
        references = body["files"]
        if body["schema_version"] != 1 or not isinstance(references, list) or not references:
            raise ValueError("invalid material chain")
        for index, reference in enumerate(references, 1):
            expected = target.parent / f"{index:04d}.md"
            if Path(reference["path"]).absolute() != expected or reference["bundle_sha256"] != target.parent.name:
                raise ValueError("material chain placement changed")
        if not any(Path(reference["path"]).absolute() == target for reference in references):
            return []
        read_bundle(references)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("required material chain manifest is invalid") from exc
    return references
