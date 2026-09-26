from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from hx.continuity_store import ARTIFACT_CHUNK_BYTES, ContinuityStore, canonical
from hx.errors import ValidationError
from hx.evidence import read


@pytest.fixture
def evidence(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "read bounded evidence"}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
            data = b"A" * (3 * ARTIFACT_CHUNK_BYTES) + "\n错误\n".encode() + b"Z" * ARTIFACT_CHUNK_BYTES
            import hashlib
            sha = hashlib.sha256(data).hexdigest()
            event = tx.append_event(run, "main", "E1", "tool_result", {"artifact_hash": sha})
            tx.put_artifact(data, owner_type="event", owner_id=event["event_id"], slot="observation")
        yield store, event["event_id"], sha, data


def test_only_requested_artifact_chunks_are_read_and_verified(evidence, monkeypatch):
    store, event, sha, data = evidence
    original = Path.open
    reads = []

    class Reader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.handle.close()
        def __getattr__(self, name):
            return getattr(self.handle, name)
        def read(self, size=-1):
            assert 0 <= size <= ARTIFACT_CHUNK_BYTES
            result = self.handle.read(size)
            reads.append(len(result))
            return result

    def open_file(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        return Reader(handle) if path == store.artifacts / sha else handle

    monkeypatch.setattr(Path, "open", open_file)
    result = read(store, event, offset=ARTIFACT_CHUNK_BYTES + 10, limit=100)
    assert result["content"] == "A" * 100
    assert result["next_offset"] == ARTIFACT_CHUNK_BYTES + 110
    assert sum(reads) == ARTIFACT_CHUNK_BYTES < len(data)


def test_slice_across_chunks_and_utf8_split_are_lossless(evidence):
    store, event, _, data = evidence
    offset = 3 * ARTIFACT_CHUNK_BYTES - 5
    result = read(store, event, offset=offset, limit=20)
    assert result["content"].encode() == data[offset:offset + 20]
    split = read(store, event, offset=3 * ARTIFACT_CHUNK_BYTES + 2, limit=1)
    assert split["encoding"] == "base64"
    assert base64.b64decode(split["content"]) == data[3 * ARTIFACT_CHUNK_BYTES + 2:3 * ARTIFACT_CHUNK_BYTES + 3]


def test_corrupt_requested_chunk_rejects_instead_of_serving_false_evidence(evidence):
    store, event, sha, _ = evidence
    with (store.artifacts / sha).open("r+b") as handle:
        handle.seek(ARTIFACT_CHUNK_BYTES)
        handle.write(b"!")
    with pytest.raises(ValidationError, match="corrupt"):
        read(store, event, offset=ARTIFACT_CHUNK_BYTES, limit=1)


def test_evidence_cli_returns_only_the_requested_page(evidence, run_hx):
    store, event, _, _ = evidence
    result = run_hx("evidence", event, "--root", str(store.root), "--offset", "0", "--limit", "32")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["bytes"] == 32 and body["content"] == "A" * 32
    assert body["next_offset"] == 32


def test_slice_limits_and_retired_evidence_are_explicit(evidence):
    store, event, _, _ = evidence
    with pytest.raises(ValidationError, match="limit"):
        read(store, event, limit=999999999)
    with pytest.raises(ValidationError, match="past the end"):
        read(store, event, offset=999999999)
    with pytest.raises(ValidationError, match="retired"):
        read(store, "missing")


def test_existing_artifact_verification_streams_bounded_chunks(evidence, monkeypatch):
    store, _, sha, data = evidence
    original = Path.open
    reads = []

    class Reader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.handle.close()
        def read(self, size=-1):
            assert 0 < size <= ARTIFACT_CHUNK_BYTES
            result = self.handle.read(size)
            reads.append(len(result))
            return result

    def open_file(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        return Reader(handle) if path == store.artifacts / sha else handle

    monkeypatch.setattr(Path, "open", open_file)
    with store.transaction() as tx:
        assert tx.put_artifact(data, owner_type="test", owner_id="second-owner", slot="proof") == sha
    assert sum(reads) == len(data)
    assert max(reads) == ARTIFACT_CHUNK_BYTES


def test_file_artifact_install_and_slice_do_not_load_whole_output(evidence, monkeypatch):
    store, _, _, data = evidence
    source = store.root / "check-output.txt"
    source.write_bytes(data)
    original = Path.open
    reads = []

    class Reader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.handle.close()
        def __getattr__(self, name):
            return getattr(self.handle, name)
        def read(self, size=-1):
            assert 0 < size <= ARTIFACT_CHUNK_BYTES
            result = self.handle.read(size)
            reads.append(len(result))
            return result

    def open_file(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        return Reader(handle) if args and args[0] == "rb" else handle

    monkeypatch.setattr(Path, "open", open_file)
    with store.transaction() as tx:
        sha = tx.put_artifact_file(source, owner_type="check", owner_id="receipt", slot="output")
        part, size = tx.read_artifact_slice(sha, offset=10, limit=20)
    assert part == data[10:30] and size == len(data)
    assert max(reads) == ARTIFACT_CHUNK_BYTES


def test_file_artifact_overflow_leaves_no_pending_file(evidence):
    store, _, _, _ = evidence
    source = store.root / "too-large.txt"
    source.write_bytes(b"x" * 100)
    before = set(store.artifacts.iterdir())
    with pytest.raises(ValidationError, match="byte bound"), store.transaction() as tx:
        tx.put_artifact_file(source, owner_type="check", owner_id="overflow", slot="output", max_bytes=10)
    assert set(store.artifacts.iterdir()) == before
    assert not store.db.execute("SELECT 1 FROM artifact_refs WHERE owner_id='overflow'").fetchone()
