"""Fingerprints for files whose bodies are outside the text-index budget."""

import hashlib


def fingerprint(path):
    hasher = hashlib.sha256()
    before = path.stat()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            hasher.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("source changed while fingerprinting")
    return hasher.hexdigest()


def record(conn, repo, relative, path, reason):
    sha = fingerprint(path)
    previous = conn.execute(
        "SELECT sha256 FROM exclusions WHERE repo=? AND path=?", (repo, relative)
    ).fetchone()
    conn.execute(
        "INSERT OR REPLACE INTO exclusions VALUES(?,?,?,?,?)",
        (repo, relative, sha, path.stat().st_size, reason),
    )
    return previous is None or previous[0] != sha
