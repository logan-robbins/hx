"""Whole-file size policy and exhaustive repository outlier discovery.

The 4 UTF-8 bytes per token estimate is a sizing convention, not a tokenizer.
It deliberately counts encoded bytes so non-ASCII text cannot evade the cap.
Existing outliers remain visible and indexable; changed files must fit the cap.
"""

from __future__ import annotations

import hashlib
import codecs
import os
import subprocess
from pathlib import Path

from .errors import ValidationError

MAX_FILE_TOKENS = 10_000
BYTES_PER_TOKEN = 4
MAX_FILE_BYTES = MAX_FILE_TOKENS * BYTES_PER_TOKEN


def file_record(data: bytes, *, path=""):
    """Describe all supplied bytes without slicing, decoding loss, or a cap."""
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "estimated_tokens": (len(data) + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN,
        "oversized": len(data) > MAX_FILE_BYTES,
    }


def validate_bytes(data: bytes, *, path=""):
    record = file_record(data, path=path)
    if record["oversized"]:
        raise ValidationError(
            f"{path}: whole file is {record['bytes']} UTF-8 bytes "
            f"({record['estimated_tokens']} estimated tokens); maximum is "
            f"{MAX_FILE_BYTES} bytes / {MAX_FILE_TOKENS} estimated tokens. "
            "Split maintained instructions into automatically delivered linked parts, "
            "or refactor source into smaller modules; partial reads are prohibited."
        )
    return record


def read_file(path):
    """Read one complete policy-sized file, or fail without returning a prefix."""
    target = Path(path)
    data = target.read_bytes()
    validate_bytes(data, path=target)
    return data


def validate_file(path):
    target = Path(path)
    return file_record(read_file(target), path=target)


def report(records, *, repository=None, binary_paths=(), deleted_paths=()):
    """A complete report: outliers have no limit, pagination, or summary slice."""
    ordered = sorted(records, key=lambda row: row["path"])
    identity = hashlib.sha256()
    for row in ordered:
        identity.update(os.fsencode(row["path"]) + b"\0" + row["sha256"].encode() + b"\n")
    outliers = [row for row in ordered if row["oversized"]]
    return {
        "repository": str(repository) if repository is not None else None,
        "complete": True,
        "clean": not outliers,
        "max_file_bytes": MAX_FILE_BYTES,
        "max_estimated_tokens": MAX_FILE_TOKENS,
        "estimate": "ceil(UTF-8 bytes / 4); heuristic, not model tokenization",
        "files_scanned": len(ordered),
        "source_bytes": sum(row["bytes"] for row in ordered),
        "source_digest": identity.hexdigest(),
        "outliers": outliers,
        "binary_paths": sorted(binary_paths),
        "deleted_paths": sorted(deleted_paths),
    }


def scan_repo(repo, paths=None):
    """Inspect tracked and nonignored source/config/docs in deterministic order.

    Use the indexer's discovery rules for generated directories. Never follow
    symlinks into external trees. Deleted tracked paths and binary files are
    reported explicitly; source bodies are always read in their entirety.
    """
    from .code_build import SKIP_DIRS, discover
    from .code_store import source_bytes

    repository = Path(repo).resolve()
    if not repository.is_dir():
        raise ValidationError(f"{repository}: repository does not exist")
    scopes = [str(path).rstrip("/") or "." for path in paths] if paths is not None else None
    if scopes == []:
        return report([], repository=repository)
    if scopes is not None and any(Path(path).is_absolute() or ".." in Path(path).parts for path in scopes):
        raise ValidationError("file audit paths must be repository relative")
    listed = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(repository), "ls-files", "-z",
         "--cached", "--others", "--exclude-standard", *(["--", *scopes] if scopes is not None else [])],
        capture_output=True, timeout=60,
    )
    if listed.returncode == 0:
        names = [os.fsdecode(name) for name in listed.stdout.split(b"\0") if name]
    else:
        names = discover(repository, scopes)
    if scopes is None:
        names = [name for name in names if not any(part in SKIP_DIRS for part in Path(name).parts)]
    records, binary, deleted = [], [], []
    for name in sorted(set(names)):
        target = repository / name
        if not target.parent.resolve().is_relative_to(repository):
            raise ValidationError(f"{name}: source path escapes repository")
        try:
            before = target.lstat()
        except FileNotFoundError:
            deleted.append(name)
            continue
        if not target.is_file() and not target.is_symlink():
            continue
        if target.is_symlink():
            data = source_bytes(target)
            record = file_record(data, path=name)
            try:
                data.decode('utf-8')
                text = b'\0' not in data
            except UnicodeDecodeError:
                text = False
        else:
            # Scanning is not model delivery. Hash and validate the entire
            # source with constant memory, including large imported outliers.
            hasher, size, text = hashlib.sha256(), 0, True
            decoder = codecs.getincrementaldecoder('utf-8')()
            with target.open('rb') as handle:
                for chunk in iter(lambda: handle.read(65536), b''):
                    hasher.update(chunk)
                    size += len(chunk)
                    if text:
                        try:
                            decoder.decode(chunk)
                            text = b'\0' not in chunk
                        except UnicodeDecodeError:
                            text = False
            if text:
                try:
                    decoder.decode(b'', final=True)
                except UnicodeDecodeError:
                    text = False
            record = {'path': name, 'bytes': size, 'sha256': hasher.hexdigest(),
                      'estimated_tokens': (size + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN,
                      'oversized': size > MAX_FILE_BYTES}
        after = target.lstat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise ValidationError(f"{name}: source changed during complete file audit")
        if text:
            records.append(record)
        else:
            binary.append(name)
    return report(records, repository=repository, binary_paths=binary, deleted_paths=deleted)


def require_changed(repo, paths):
    """Reject every oversized changed text file, including existing files edited now."""
    result = scan_repo(repo, paths)
    if result["outliers"]:
        details = "; ".join(
            f"{row['path']} ({row['bytes']} bytes, {row['estimated_tokens']} estimated tokens)"
            for row in result["outliers"]
        )
        raise ValidationError(
            f"changed files exceed {MAX_FILE_BYTES} bytes / {MAX_FILE_TOKENS} estimated tokens: "
            + details + "; refactor each file before completion; partial reads are prohibited"
        )
    return result
