"""Exact immutable material chains, read one complete part file at a time.

Each numbered physical file is at most MAX_FILE_BYTES and carries a deterministic footer
naming its place in the chain and the part to read next. A part read verifies the whole
chain (every part, payload and the bundle identity) and delivers that part's physical bytes;
the reader's chain is complete only when every part has its own complete-read receipt.
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


def _chunks(data, capacity):
    """`data` in pieces of at most `capacity` bytes, never splitting a UTF-8 character."""
    chunks = []
    while data:
        end = min(capacity, len(data))
        while end < len(data) and data[end] & 0xC0 == 0x80:
            end -= 1
        chunks.append(data[:end])
        data = data[end:]
    return chunks or [b'']


def prepare(text: str, directory: Path, **metadata):
    data = text.encode('utf-8')
    bundle_hash = _hash(data)
    directory = Path(directory) / bundle_hash
    # Part 1's footer lists every part path, so the reserve grows with the part count until
    # the chunking fits it; it never depends on content.
    count = 1
    while True:
        reserve = (len(str(directory).encode()) + 16) * (count + 1) + 512
        if MAX_FILE_BYTES - reserve < 4:
            raise ValidationError('material path leaves no room for a complete part')
        chunks = _chunks(data, MAX_FILE_BYTES - reserve)
        if len(chunks) <= count:
            break
        count = len(chunks)
    paths = [directory / f'{index:04d}.md' for index in range(1, len(chunks) + 1)]
    refs, files = [], {}
    for index, (path, payload) in enumerate(zip(paths, chunks), 1):
        next_path = str(paths[index]) if index < len(paths) else None
        continuation = (f'Read {next_path} next; part {index} of {len(paths)}' if next_path
                        else f'END OF COMPLETE MATERIAL; part {index} of {len(paths)}')
        if index == 1 and len(paths) > 1:
            continuation += '; the parts in order: ' + ', '.join(str(part) for part in paths)
        footer = (f'\n\n<!-- HX MATERIAL {bundle_hash} PART {index}/{len(paths)}; '
                  f'PAYLOAD BYTES {len(payload)}; {continuation}. -->\n').encode()
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


def read_part(references, path):
    """One part's physical bytes and reference, after verifying the whole chain it belongs to."""
    read_bundle(references)
    target = Path(path).absolute()
    reference = next((ref for ref in references if Path(ref['path']).absolute() == target), None)
    if reference is None:
        raise ValidationError('file is not a part of this required material chain')
    return verify_file(reference), reference


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
        body = json.dumps({"files": intrinsic}, ensure_ascii=False, separators=(",", ":"))
        if len(body.encode()) > MAX_FILE_BYTES:
            raise ValidationError("complete material manifest exceeds its file cap")
        manifest = Path(references[0]["path"]).parent / "bundle.json"
        if manifest.exists():
            if manifest.is_symlink() or json.loads(manifest.read_text()) != {"files": intrinsic}:
                raise ValidationError("required material chain manifest changed")
        else:
            store.atomic_write_text(manifest, body)


def discover_bundle(path):
    """The verified material chain holding `path`, so a read of one part is checked against all of it."""
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
        if body.keys() != {"files"} or not isinstance(references, list) or not references:
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
