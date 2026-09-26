"""Streaming source/environment identities for check receipts.

Contents establish reuse identity. Metadata also detects write-and-restore changes
within an execution interval. No file contents or environment values enter receipts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

CHUNK_BYTES = 65536


def _feed(hasher, value) -> None:
    data = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    hasher.update(len(data).to_bytes(8, "big"))
    hasher.update(data)


def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


class Fingerprint:
    def __init__(self):
        self.content = hashlib.sha256()
        self.observation = hashlib.sha256()
        self.files = 0
        self.complete = True
        self.reasons: set[str] = set()

    def unknown(self, reason: str) -> None:
        self.complete = False
        self.reasons.add(reason)

    def path(self, path: Path, label: str, *, missing_ok: bool = False) -> None:
        before = None
        try:
            before = path.lstat()
            if stat.S_ISDIR(before.st_mode):
                # Only one directory listing is resident at a time. Directory
                # metadata is excluded: ignored build caches can change it.
                with os.scandir(path) as scan:
                    children = sorted(entry.name for entry in scan if entry.name != ".git")
                _feed(self.content, [label, "directory"])
                for name in children:
                    self.path(path / name, label + "/" + name)
                return
            link = os.readlink(path) if stat.S_ISLNK(before.st_mode) else None
            if link is not None and not path.is_file():
                self.unknown("symlink target is not a regular file")
                _feed(self.content, [label, "symlink", link])
                _feed(self.observation, [label, _stamp(before)])
                return
            if not (stat.S_ISREG(before.st_mode) or link is not None):
                self.unknown("non-regular input")
                return
            hasher = hashlib.sha256()
            # A file replaced by a pipe must not hang the fingerprinting pass.
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise OSError("input ceased to be a regular file")
                remaining = opened.st_size
                while remaining:
                    chunk = handle.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        self.unknown("input shortened while fingerprinting")
                        break
                    hasher.update(chunk)
                    remaining -= len(chunk)
                closed = os.fstat(handle.fileno())
            after = path.lstat()
            if _stamp(before) != _stamp(after) or _stamp(opened) != _stamp(closed) or _stamp(path.stat()) != _stamp(closed):
                self.unknown("input changed while fingerprinting")
            _feed(self.content, [label, link, stat.S_IMODE(before.st_mode), hasher.hexdigest()])
            _feed(self.observation, [label, _stamp(before), _stamp(opened), _stamp(after)])
            self.files += 1
        except OSError as exc:
            if before is None and missing_ok and isinstance(exc, FileNotFoundError):
                # A tracked deletion is an observable source state; an absent
                # declared fixture still makes the check identity incomplete.
                _feed(self.content, [label, "absent"])
                _feed(self.observation, [label, "absent"])
                return
            self.unknown(f"input unavailable: {type(exc).__name__}")
            _feed(self.content, [label, "unavailable", type(exc).__name__])

    def result(self) -> dict:
        return {"hash": self.content.hexdigest(), "observation_hash": self.observation.hexdigest(),
                "files": self.files, "complete": self.complete, "reasons": sorted(self.reasons)}


def _git_paths(root: Path, env: dict):
    with subprocess.Popen(["git", "--no-optional-locks", "-C", str(root), "ls-files", "-z",
                           "--cached", "--others", "--exclude-standard"],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as process:
        tail = b""
        while chunk := process.stdout.read(CHUNK_BYTES):
            parts = (tail + chunk).split(b"\0")
            tail = parts.pop()
            if len(tail) > CHUNK_BYTES:
                process.kill()
                raise OSError("git path exceeds filesystem bound")
            for name in parts:
                yield os.fsdecode(name)
        if process.wait() != 0 or tail:
            raise OSError("git file enumeration failed")


def source(workdir: Path, extra_inputs: list[str], env: dict) -> dict:
    fingerprint = Fingerprint()
    try:
        found = subprocess.run(["git", "--no-optional-locks", "-C", str(workdir), "rev-parse", "--show-toplevel"],
                               env=env, capture_output=True, check=False)
        repository = Path(os.fsdecode(found.stdout).strip()) if found.returncode == 0 else None
    except OSError:
        repository = None
    if repository:
        _feed(fingerprint.content, ["repository", str(repository.resolve())])
        try:
            for name in _git_paths(repository, env):
                fingerprint.path(repository / name, "repo:" + name, missing_ok=True)
        except OSError:
            fingerprint.unknown("repository enumeration failed")
    elif not extra_inputs:
        fingerprint.unknown("non-repository check has no declared inputs")
    for name in sorted(extra_inputs):
        path = Path(name)
        fingerprint.path(path if path.is_absolute() else workdir / path, "declared:" + name)
    result = fingerprint.result()
    result["repository"] = str(repository) if repository else None
    return result


def environment(workdir: Path, argv: list[str], recipe: dict, env: dict) -> tuple[dict, list[str]]:
    fingerprint = Fingerprint()
    # Hash all values without serializing their cleartext into logs or receipts.
    _feed(fingerprint.content, {key: value for key, value in env.items()})
    _feed(fingerprint.content, {"platform": os.uname().sysname, "release": os.uname().release,
                                "machine": os.uname().machine, "cwd": str(workdir.resolve())})
    resolved = []
    for name in [argv[0], *recipe["executables"]]:
        if "/" in name:
            candidate = Path(name)
            candidate = candidate if candidate.is_absolute() else workdir / candidate
            path = str(candidate.absolute()) if candidate.is_file() and os.access(candidate, os.X_OK) else None
        else:
            search = os.pathsep.join(str(Path(entry) if Path(entry).is_absolute() else workdir / entry)
                                     for entry in env.get("PATH", os.defpath).split(os.pathsep))
            path = shutil.which(name, path=search)
        if path is None:
            fingerprint.unknown("executable unavailable")
            resolved.append(name)
        else:
            # Preserve the invocation symlink: resolving venv/bin/python changes
            # Python's environment even though the underlying executable matches.
            path = str(Path(path).absolute())
            resolved.append(path)
            fingerprint.path(Path(path), "executable:" + name + ":" + path)
    for name in sorted(recipe["inputs"]):
        path = Path(name)
        fingerprint.path(path if path.is_absolute() else workdir / path, "environment:" + name)
    for name, version_file in sorted(recipe["external_versions"].items()):
        if version_file is None:
            fingerprint.unknown("external state has no version source")
        else:
            path = Path(version_file)
            fingerprint.path(path if path.is_absolute() else workdir / path, "external:" + name)
    if not recipe["complete"]:
        fingerprint.unknown("environment recipe is incomplete")
    return fingerprint.result(), [resolved[0], *argv[1:]]
