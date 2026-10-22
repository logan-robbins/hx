"""Streaming source/environment identities for check receipts.

Contents establish reuse identity. Metadata also detects write-and-restore changes
within an execution interval. No file contents or environment values enter receipts.

Identities are portable: an absolute path inside the checkout or an hx root enters a
content hash relative to it (`{repository}/…`, `{root}/…`), and the checkout itself is
identified by its repository namespace and commit. Equal inputs in two worktrees at one
commit, or in two roots on one box, therefore have one identity.
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


def _hexadecimal(value):
    return value if len(value) in (40, 64) and all(c in "0123456789abcdef" for c in value) else None


def _resolve(head, gitdir, common):
    if not head.startswith("ref:"):
        return _hexadecimal(head)
    ref = head[4:].strip()
    for base in (gitdir, common):
        try:
            return _hexadecimal((base / ref).read_text().strip())
        except OSError:
            pass
    try:
        for line in (common / "packed-refs").read_text().splitlines():
            sha, _, name = line.partition(" ")
            if name == ref:
                return _hexadecimal(sha)
    except OSError:
        pass
    return None


def _namespace(top, common):
    """The graph namespace (`code_store.namespace`): manifest repo_id, else the origin URL,
    else the first remote's, else the shared Git directory every worktree of a clone has."""
    try:
        identity = json.loads((top / ".hx/map/manifest.json").read_text())["repo_id"]
        if isinstance(identity, str) and identity:
            return identity
    except (OSError, ValueError, KeyError, TypeError):
        pass
    urls, section = {}, None
    try:
        lines = (common / "config").read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    for raw in lines:
        line = raw.strip()
        if line.startswith("["):
            section = line[1:line.find("]")] if "]" in line else None
        elif section and section.startswith("remote ") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip().lower() == "url":
                urls.setdefault(section[7:].strip().strip('"'), value.strip().strip('"'))
    return urls.get("origin") or next(iter(urls.values()), None) or str(common)


def checkout(path) -> dict | None:
    """The Git checkout holding `path`, from its metadata files without a subprocess:
    `{top, commit, namespace}` (commit None while unborn), else None."""
    path = Path(path).resolve()
    for top in (path, *path.parents):
        marker = top / ".git"
        if marker.exists():
            break
    else:
        return None
    try:
        if marker.is_dir():
            gitdir = marker
        else:
            text = marker.read_text().strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text[7:].strip())
            gitdir = (gitdir if gitdir.is_absolute() else top / gitdir).resolve()
        common = gitdir
        if (gitdir / "commondir").is_file():
            shared = Path((gitdir / "commondir").read_text().strip())
            common = (shared if shared.is_absolute() else gitdir / shared).resolve()
        commit = _resolve((gitdir / "HEAD").read_text().strip(), gitdir, common)
    except OSError:
        return None
    if commit is None:
        # Unborn branches, and ref stores this reader does not parse, ask Git itself.
        found = subprocess.run(["git", "--no-optional-locks", "-C", str(top), "rev-parse", "--verify", "-q", "HEAD"],
                               capture_output=True, text=True, check=False)
        commit = _hexadecimal(found.stdout.strip()) if found.returncode == 0 else None
    return {"top": top, "commit": commit, "namespace": _namespace(top, common)}


def anchors_for(workdir, *roots) -> dict[str, str]:
    """Absolute prefixes identities replace, longest first: the checkout holding `workdir`
    becomes `{repository}` and each hx root `{root}`, in both spelled and resolved forms."""
    found = checkout(workdir)
    pairs = ([(found["top"], "{repository}")] if found else []) + [(Path(root), "{root}") for root in roots]
    return combine({form: token for path, token in pairs for form in (str(path), str(Path(path).resolve()))
                    if len(form) > 1})


def combine(*anchors) -> dict[str, str]:
    """Anchor sets merged; an earlier set wins a shared prefix."""
    result = {}
    for values in anchors:
        for prefix, token in values.items():
            result.setdefault(prefix, token)
    return result


def relocate(value, anchors):
    """`value` with every anchored absolute prefix replaced by its placeholder, longest first
    (persisted anchor sets come back in key order)."""
    return _relocate(value, sorted(anchors.items(), key=lambda item: -len(item[0])))


def _relocate(value, ordered):
    if isinstance(value, str):
        for prefix, token in ordered:
            value = token if value == prefix else value.replace(prefix + "/", token + "/")
        return value
    if isinstance(value, dict):
        return {_relocate(key, ordered): _relocate(item, ordered) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_relocate(item, ordered) for item in value]
    return value


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
                # Declared directories include their listing observations. Git
                # scope fingerprints files individually, avoiding ignored caches.
                with os.scandir(path) as scan:
                    children = sorted(entry.name for entry in scan if entry.name != ".git")
                _feed(self.content, [label, "directory"])
                for name in children:
                    self.path(path / name, label + "/" + name)
                after = path.lstat()
                _feed(self.observation, [label, _stamp(before), _stamp(after)])
                if _stamp(before) != _stamp(after):
                    self.unknown("directory changed while fingerprinting")
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


def source(workdir: Path, extra_inputs: list[str], env: dict, *, contract: dict | None = None,
           anchors: dict | None = None) -> dict:
    """Identify sources, using a declared dependency closure only when complete.

    The caller asserts that inputs plus dependencies include every source the
    command can observe. Incomplete source closures retain repository scope.
    The workspace enters as (namespace, commit, path relative to the checkout), never
    as its absolute location; `anchors` (default: the checkout) relocates paths.
    """
    fingerprint = Fingerprint()
    anchors = anchors_for(workdir) if anchors is None else anchors
    scoped = contract is not None and contract.get("complete") is True
    names = set(extra_inputs)
    if contract is not None:
        names.update(contract.get("inputs", []))
        names.update(contract.get("dependencies", []))
    _feed(fingerprint.content, ["source_contract", "declared" if scoped else "repository",
                                relocate(str(workdir.resolve()), anchors)])
    found = checkout(workdir)
    repository = found["top"] if found else None
    if found:
        _feed(fingerprint.content, ["repository", found["namespace"], found["commit"]])
    if repository and not scoped:
        try:
            for name in _git_paths(repository, env):
                fingerprint.path(repository / name, "repo:" + name, missing_ok=True)
        except OSError:
            fingerprint.unknown("repository enumeration failed")
    elif not repository and not scoped:
        fingerprint.unknown("non-repository check needs a complete source closure")
    # Relocated names order the feed, so the same inputs feed alike from any checkout.
    for name in sorted(names, key=lambda name: relocate(name, anchors)):
        path = Path(name)
        fingerprint.path(path if path.is_absolute() else workdir / path, "declared:" + relocate(name, anchors))
    result = fingerprint.result()
    result["repository"] = str(repository) if repository else None
    result["namespace"] = found["namespace"] if found else None
    result["commit"] = found["commit"] if found else None
    result["scope"] = "declared" if scoped else "repository"
    result["declared_inputs"] = sorted(names)
    result["closure_complete"] = scoped
    return result


def environment(workdir: Path, argv: list[str], recipe: dict, env: dict, *,
                anchors: dict | None = None) -> tuple[dict, list[str]]:
    fingerprint = Fingerprint()
    anchors = anchors_for(workdir) if anchors is None else anchors
    # Hash all values without serializing their cleartext into logs or receipts.
    _feed(fingerprint.content, relocate({key: value for key, value in env.items()}, anchors))
    _feed(fingerprint.content, {"platform": os.uname().sysname, "release": os.uname().release,
                                "machine": os.uname().machine, "cwd": relocate(str(workdir.resolve()), anchors)})
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
            fingerprint.path(Path(path), "executable:" + relocate(name, anchors) + ":" + relocate(path, anchors))
    for name in sorted(recipe["inputs"], key=lambda name: relocate(name, anchors)):
        path = Path(name)
        fingerprint.path(path if path.is_absolute() else workdir / path, "environment:" + relocate(name, anchors))
    for name, version_file in sorted(recipe["external_versions"].items()):
        if version_file is None:
            fingerprint.unknown("external state has no version source")
        else:
            path = Path(version_file)
            fingerprint.path(path if path.is_absolute() else workdir / path, "external:" + name)
    if not recipe["complete"]:
        fingerprint.unknown("environment recipe is incomplete")
    return fingerprint.result(), [resolved[0], *argv[1:]]
