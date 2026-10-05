"""Incremental registered-source capture with durable offsets and bounded reads.

One resident process owns the notification socket per root. Notifications are
hints; the five-second reconciliation also recovers dropped notifications.
No model calls, tmux operations, global session search, or history scans occur here.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import select
import socket
import stat as stat_types
import time
import uuid
from pathlib import Path

from .continuity_store import ContinuityStore, Conflict, canonical, digest
from .errors import HxError, ValidationError
from .events import DECODERS, DecodeGap, Event, decode

MAX_RECORD_BYTES = 8 * 1024 * 1024
SPOOL_BYTES = 64 * 1024 * 1024
BATCH_BYTES = 256 * 1024  # Scheduling quantum; one complete record may exceed it.
BATCH_RECORDS = 128
MAX_SOURCES = 16


def _open_source(state):
    """Open a launch-owned regular file without following replacement symlinks."""
    scope = state.get("native_scope")
    if not scope:
        return Path(state["path"]).open("rb")
    root = Path(scope["root"])
    parts = Path(state["path"]).relative_to(root).parts
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    parent = os.open(root, flags | os.O_DIRECTORY)
    try:
        root_stat = os.fstat(parent)
        if [root_stat.st_dev, root_stat.st_ino] != scope["root_identity"]:
            raise OSError("original native source home was replaced")
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], flags, dir_fd=parent)
        try:
            if not stat_types.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("native source is not a regular file")
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(parent)


def _source_stat(state):
    if not state.get("native_scope"):
        return Path(state["path"]).stat()
    with _open_source(state) as handle:
        return os.fstat(handle.fileno())


def source_snapshot(tx, run_id):
    """Inspect registered source metadata only; never read transcript bodies.

    This is a current observation, not proof that a native writer has stopped.
    Completion, dependency admission, and context compilation share this gate.
    """
    rows = tx.db.execute("""SELECT source_id,revision,generation,committed_offset,payload
        FROM capture_sources WHERE run_id=? ORDER BY source_id LIMIT ?""",
        (run_id, MAX_SOURCES + 1)).fetchall()
    sources = []
    for row in rows[:MAX_SOURCES]:
        state = json.loads(row["payload"])
        lag = bool(state["pending_tail"] or state["gaps"])
        try:
            stat = _source_stat(state)
            lag |= (state["identity"] != [stat.st_dev, stat.st_ino]
                    or stat.st_size != row["committed_offset"]
                    or state.get("observed_times") != [stat.st_mtime_ns, stat.st_ctime_ns])
        except OSError:
            lag = True
        sources.append({"id": row["source_id"], "revision": row["revision"], "generation": row["generation"],
                        "offset": row["committed_offset"], "lag": lag, "gaps": state["gaps"]})
    return {"sources": sources, "overflow": len(rows) > MAX_SOURCES}


class SourceReadError(Exception):
    def __init__(self, error: OSError):
        self.error = error


def _source_io(operation, *args):
    try:
        return operation(*args)
    except OSError as exc:
        raise SourceReadError(exc) from exc


def register(store: ContinuityStore, *, run_id: str, stream_id: str, path: Path,
             decoder: str, session_id: str, branch_ids: list[str] | None = None,
             native_scope: dict | None = None) -> str:
    if stream_id in {"progress", "checks"}:
        raise ValidationError("progress/checks streams are reserved for validated hx commands")
    if decoder not in DECODERS or not isinstance(session_id, str) or not session_id or len(session_id) > 512 or not stream_id:
        raise ValidationError("source needs a registered decoder, session, and stream")
    path = Path(path).absolute()
    if branch_ids is not None and (not isinstance(branch_ids, list) or not branch_ids or any(not isinstance(x, str) or not x for x in branch_ids)):
        raise ValidationError("branch_ids must be the ordered active ancestry IDs")
    source_id = digest([run_id, stream_id, str(path), decoder, session_id, branch_ids])
    if native_scope is not None:
        if (not isinstance(native_scope, dict)
                or set(native_scope) != {"root", "root_identity", "actor_id", "launch_id", "branch_policy"}
                or not isinstance(native_scope["root"], str)
                or not isinstance(native_scope["root_identity"], list) or len(native_scope["root_identity"]) != 2
                or any(type(value) is not int or value < 0 for value in native_scope["root_identity"])
                or not isinstance(native_scope["launch_id"], str) or not 1 <= len(native_scope["launch_id"]) <= 512
                or (native_scope["actor_id"] is not None and
                    (not isinstance(native_scope["actor_id"], str) or not 1 <= len(native_scope["actor_id"]) <= 512))
                or (decoder, native_scope.get("branch_policy")) not in {
                    ("claude-v1", "claude-linear-v1"), ("codex-rollout-0.156.1", "codex-0.156.1-v1")}
                or branch_ids is not None
                or not Path(native_scope["root"]).is_absolute()
                or ".." in Path(native_scope["root"]).parts
                or not path.is_relative_to(Path(native_scope["root"]))
                or ".." in path.parts
                or path == Path(native_scope["root"])):
            raise ValidationError("invalid native source scope")
        source_id = digest([source_id, native_scope])
    with store.transaction() as tx:
        run = tx.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None or run["ended_at"] is not None:
            raise ValidationError("capture registration requires an active run")
        if native_scope and tx.task(run["task_id"])["revision"] != run["task_revision"]:
            raise Conflict("native source task changed; lifecycle rebind required")
        if tx.db.execute("SELECT 1 FROM capture_sources WHERE source_id=?", (source_id,)).fetchone():
            return source_id
        # A changed branch requires a fresh run. Fact invalidation/rebinding must
        # also happen at the lifecycle boundary before preparing new context.
        rows = tx.db.execute("SELECT stream_id,payload FROM capture_sources WHERE run_id=? LIMIT ?",
                             (run_id, MAX_SOURCES + 1 if native_scope else -1))
        if native_scope:
            rows = rows.fetchall()
            if len(rows) >= MAX_SOURCES:
                raise Conflict("native source scope exceeds 16 sources; reconcile before registering more")
        for row in rows:
            previous = json.loads(row["payload"])
            if native_scope and (previous["path"] == str(path) or row["stream_id"] == stream_id):
                raise Conflict("native source path or stream was rebound; reconcile its original registration")
            if previous["path"] == str(path) and previous.get("branch_ids") != branch_ids:
                raise Conflict("native branch changed; start a new run before registering its source")
        payload = {"schema_version": 1, "path": str(path), "session_id": session_id,
                   "branch_ids": branch_ids, "branch_live": False, "last_native_id": None, "identity": None,
                   "anchor": None, "gaps": [], "pending_tail": False, "latest_usage": None,
                   "decoded_bytes": 0, "last_observed_size": 0}
        if native_scope is not None:
            payload["native_scope"] = native_scope
        tx._change()
        tx.db.execute("INSERT OR IGNORE INTO cursors(run_id,stream_id) VALUES(?,?)", (run_id, stream_id))
        tx.db.execute("INSERT INTO capture_sources VALUES(?,?,?,?,?,?,?,?)",
                      (source_id, run_id, stream_id, 1, str(uuid.uuid4()), 0, decoder, canonical(payload)))
    notify(store.root, source_id)
    return source_id


def _gap(state: dict, reason: str, offset: int) -> None:
    entry = {"reason": reason, "offset": offset}
    if entry not in state["gaps"]:
        state["gaps"] = [*state["gaps"][-15:], entry]


def _anchor(handle, offset: int) -> str:
    handle.seek(max(0, offset - 128))
    return hashlib.sha256(handle.read(min(offset, 128))).hexdigest()


def _branch_active(body: dict, state: dict) -> bool:
    if state.get("native_scope", {}).get("branch_policy") == "codex-0.156.1-v1":
        from .codex_rollout import active
        return active(body, state)
    if state.get("native_scope", {}).get("branch_policy") == "claude-linear-v1":
        return _claude_branch_active(body, state)
    if "parentId" not in body:
        return True
    native = body.get("id")
    if not isinstance(native, str) or not native:
        raise DecodeGap("branched record lacks a native ID")
    ancestry = state["branch_ids"]
    if ancestry is None:
        raise DecodeGap("branched source requires explicit active ancestry")
    if native in ancestry:
        state["last_native_id"] = native
        if native == ancestry[-1]:
            state["branch_live"] = True
        return True
    if state["branch_live"] and body.get("parentId") == state["last_native_id"]:
        # After the registered leaf, a linear continuation is active. A fork of an
        # earlier node remains inactive until explicit registration in a fresh run.
        state["last_native_id"] = native
        return True
    return False


def _claude_branch_active(body, state):
    scope = state["native_scope"]
    actor = scope["actor_id"]
    if body.get("sessionId") not in (None, state["session_id"]):
        raise DecodeGap("Claude record belongs to another session")
    if not actor and (body.get("isSidechain") or body.get("teamName")):
        return False
    if body.get("agentId") not in (None, actor):
        raise DecodeGap("Claude record belongs to another child")
    native = body.get("uuid")
    if not native and body.get("type") not in {"user", "assistant"}:
        return True  # Non-chain bookkeeping still passes its decoder contract.
    if not isinstance(native, str) or not 1 <= len(native) <= 512 or "parentUuid" not in body:
        raise DecodeGap("Claude source lacks explicit native ancestry")
    parent = body["parentUuid"]
    if parent is not None and (not isinstance(parent, str) or not 1 <= len(parent) <= 512):
        raise DecodeGap("invalid Claude parent identity")
    if native == state["last_native_id"] and parent == state.get("last_native_parent"):
        return not body.get("isMeta")  # Complementary observation of the same node.
    if parent != state["last_native_id"]:
        raise DecodeGap("Claude branch changed; reconcile before continuing capture")
    state["last_native_id"] = native
    state["last_native_parent"] = parent
    return not body.get("isMeta")


def spool_size(store: ContinuityStore) -> int:
    inline = store.db.execute(
        "SELECT coalesce(sum(length(CAST(payload AS BLOB))),0) FROM events WHERE disposition IS NULL OR disposition='pending'"
    ).fetchone()[0]
    artifacts = store.db.execute(
        "SELECT coalesce(sum(size),0) FROM artifacts WHERE hash IN ("
        "SELECT a.hash FROM artifact_refs a JOIN events e ON a.owner_type='event' AND a.owner_id=e.event_id "
        "WHERE e.disposition IS NULL OR e.disposition='pending')"
    ).fetchone()[0]
    return inline + artifacts


def encode_observation(*, session_id: str, event: Event, fallback_identity: tuple) -> tuple[str, dict, bytes]:
    """Shared native/log identity and artifact representation for a public event."""
    identity = ("native", session_id, event.kind, event.native_id) if event.native_id else fallback_identity
    # The immutable observation includes the decoder's public data only. Hook and
    # transcript observations with complementary fields keep distinct payloads,
    # linked by logical_id; reducers merge their evidence without guessing equality.
    logical_id = digest(identity)
    observation = {"schema_version": 1, "kind": event.kind, "data": event.data, "usage": event.usage}
    # Keep exact inspected paths available even when bulky output becomes an
    # artifact. Map discovery must not decode the entire result to recover them.
    paths = []
    args = event.data.get('tool_input') if isinstance(event.data, dict) else None
    if isinstance(args, dict):
        paths = [args[key] for key in ('path', 'file_path', 'filePath', 'target_file')
                 if isinstance(args.get(key), str) and len(args[key].encode()) <= 1024]
        if paths:
            paths = list(dict.fromkeys(paths))
            observation['source_paths'] = paths
    encoded = canonical(observation).encode()
    observation_hash = hashlib.sha256(encoded).hexdigest()
    capture_key = f"{logical_id}:{observation_hash}"
    payload = {"schema_version": 1, "logical_id": logical_id, "observation_hash": observation_hash,
               "identity_certain": event.native_id is not None,
               "session_id": session_id, "native_id": event.native_id}
    if len(encoded) <= 4096:
        payload["observation"] = observation
    else:
        payload["artifact_hash"] = observation_hash
        payload["bytes"] = len(encoded)
    if paths:
        payload['source_paths'] = paths
    return capture_key, payload, encoded


def append_observation(tx, *, run_id: str, stream_id: str, session_id: str,
                       event: Event, fallback_identity: tuple) -> dict:
    capture_key, payload, encoded = encode_observation(session_id=session_id, event=event,
                                                      fallback_identity=fallback_identity)
    captured = tx.append_event(run_id, stream_id, capture_key, event.kind, payload)
    if "artifact_hash" in payload:
        tx.put_artifact(encoded, owner_type="event", owner_id=captured["event_id"], slot="observation")
    return captured


def _capture(tx, source: dict, state: dict, event: Event, offset: int, index: int) -> dict:
    actor = state.get("native_scope", {}).get("actor_id")
    if actor:
        native = event.native_id
        call = event.data.get("tool_use_id")
        if native and call and native.startswith(("tool:", "call:")):
            native = native.split(":", 1)[0] + ":" + digest([actor, call])
        elif native:
            native = digest([actor, native])
        event = Event(event.kind, {**event.data, "agent_id": actor}, native, event.usage)
    captured = append_observation(tx, run_id=source["run_id"], stream_id=source["stream_id"],
        session_id=state["session_id"], event=event,
        fallback_identity=("offset", source["source_id"], source["generation"], offset, index))
    tx._change()
    tx.db.execute("INSERT OR IGNORE INTO event_origins VALUES(?,?,?,?,?)",
                  (captured["event_id"], source["source_id"], source["generation"], offset, source["decoder_version"]))
    if event.usage is not None:
        state["latest_usage"] = event.usage
    return captured


def drain(store: ContinuityStore, source_id: str, *, byte_budget: int = BATCH_BYTES,
          max_records: int = BATCH_RECORDS, spool_limit: int = SPOOL_BYTES) -> dict:
    """Advance only complete decoded lines, in the transaction that stores events.

    Malformed/oversized input stops at the offending line. Earlier complete lines
    still commit. Replaced or truncated files get a new generation and a visible
    gap; unobserved old bytes are never silently declared captured.
    """
    if min(byte_budget, max_records, spool_limit) < 1:
        raise ValidationError("capture bounds must be positive")
    with store.transaction() as tx:
        row = tx.db.execute("SELECT * FROM capture_sources WHERE source_id=?", (source_id,)).fetchone()
        if row is None:
            raise ValidationError(f"unknown capture source {source_id}")
        source = dict(row)
        state = json.loads(source["payload"])
        run = tx.db.execute("SELECT * FROM runs WHERE run_id=?", (source["run_id"],)).fetchone()
        if run["ended_at"] is not None:
            return {"source_id": source_id, "status": "closed", "events": 0, "bytes": 0}
        if state.get("native_scope") and tx.task(run["task_id"])["revision"] != run["task_revision"]:
            return {"source_id": source_id, "status": "stale", "events": 0, "bytes": 0}
        offset = source["committed_offset"]
        consumed = count = lines = 0
        state["pending_tail"] = False
        used = spool_size(store)
        try:
            with _source_io(_open_source, state) as handle:
                stat = _source_io(os.fstat, handle.fileno())
                identity = [stat.st_dev, stat.st_ino]
                observed_times = [stat.st_mtime_ns, stat.st_ctime_ns]
                if (state.get("observed_times") is not None
                        and state["identity"] == identity
                        and stat.st_size <= state["last_observed_size"]
                        and state["observed_times"] != observed_times):
                    # A rewrite outside the fixed tail fingerprint may have
                    # changed already captured evidence. Do not reread history
                    # or quietly accept a new timestamp as reconciliation.
                    _gap(state, "source metadata changed without append; captured evidence requires reconciliation", offset)
                if state["identity"] is not None and (
                    state["identity"] != identity or stat.st_size < offset
                    or (offset and _source_io(_anchor, handle, offset) != state["anchor"])
                ):
                    _gap(state, "source replaced or truncated; prior tail may be missing", offset)
                    offset = 0
                    source["generation"] = str(uuid.uuid4())
                    state["last_native_id"] = None
                    state.pop("last_native_parent", None)
                    state.pop("native_meta_seen", None)
                    state["branch_live"] = False
                state["identity"] = identity
                state["last_observed_size"] = stat.st_size
                state["observed_times"] = observed_times
                _source_io(handle.seek, offset)
                while consumed < byte_budget and lines < max_records:
                    start = offset
                    line = _source_io(handle.readline, MAX_RECORD_BYTES + 1)
                    if not line:
                        break
                    if len(line) > MAX_RECORD_BYTES:
                        _gap(state, "record exceeds capture bound; reduction required", start)
                        state["pending_tail"] = True
                        break
                    if not line.endswith(b"\n"):
                        state["pending_tail"] = True
                        break
                    try:
                        line_size = len(line)
                        body = json.loads(line)
                        del line
                        if not isinstance(body, dict):
                            raise DecodeGap("source record is not an object")
                        # A failed decode must not advance branch selection either.
                        # Branch selection changes scalar fields only; ancestry is
                        # shared read-only instead of serializing the entire state.
                        branch_state = state.copy()
                        events = decode(source["decoder_version"], body) if _branch_active(body, branch_state) else []
                        del body
                    except (UnicodeDecodeError, json.JSONDecodeError, DecodeGap) as exc:
                        _gap(state, f"decode failed: {type(exc).__name__}", start)
                        state["pending_tail"] = True
                        break
                    charge = sum(len(canonical({"schema_version": 1, "kind": event.kind,
                                                "data": event.data, "usage": event.usage}).encode()) + 2048 for event in events)
                    if used + charge > spool_limit:
                        state["pending_tail"] = True
                        state["backpressure"] = "capture spool full"
                        break
                    for index, event in enumerate(events):
                        _capture(tx, source, state, event, start, index)
                        count += 1
                    state["branch_ids"] = branch_state["branch_ids"]
                    state["branch_live"] = branch_state["branch_live"]
                    state["last_native_id"] = branch_state["last_native_id"]
                    if "last_native_parent" in branch_state:
                        state["last_native_parent"] = branch_state["last_native_parent"]
                    if "native_meta_seen" in branch_state:
                        state["native_meta_seen"] = branch_state["native_meta_seen"]
                    offset += line_size
                    consumed += line_size
                    used += charge
                    lines += 1
                    # Release the decoded record before reading the next one.
                    events.clear()
                    event = None
                state["anchor"] = _source_io(_anchor, handle, offset)
                state["pending_tail"] = state["pending_tail"] or offset < stat.st_size
                if not state["pending_tail"]:
                    state.pop("backpressure", None)
        except SourceReadError as exc:
            if not isinstance(exc.error, FileNotFoundError) or state["identity"] is not None:
                _gap(state, f"source unavailable: {type(exc.error).__name__}", offset)
            state["pending_tail"] = True
        state["decoded_bytes"] += consumed
        body = canonical(state)
        if offset != source["committed_offset"] or body != source["payload"]:
            tx._change()
            tx.db.execute(
                "UPDATE capture_sources SET revision=revision+1,generation=?,committed_offset=?,payload=? WHERE source_id=?",
                (source["generation"], offset, body, source_id),
            )
        if count:
            tx.enqueue("capture_ready", f"{source_id}:{source['generation']}:{offset}",
                       {"run_id": source["run_id"], "stream_id": source["stream_id"], "source_id": source_id})
        return {"source_id": source_id, "events": count, "bytes": consumed, "offset": offset,
                "pending_tail": state["pending_tail"], "gaps": state["gaps"],
                "backpressure": state.get("backpressure")}


def socket_path(root: Path) -> Path:
    path = root / "run" / "observer.sock"
    if len(os.fsencode(path)) < 100:
        return path
    # macOS sockaddr_un allows only 104 bytes, often shorter than a worktree path.
    key = hashlib.sha256(os.fsencode(root.resolve())).hexdigest()[:32]
    return Path("/tmp") / f"hx-observer-{os.getuid()}" / f"{key}.sock"


def notify(root: Path, source_id: str = "*") -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
            sender.setblocking(False)
            sender.sendto(source_id.encode(), str(socket_path(root)))
        return True
    except OSError:
        return False  # Reconciliation uses registered offsets, not notification delivery.


def reconcile(store: ContinuityStore) -> list[dict]:
    from .native_producer import retry
    deliveries = retry(store)
    sources = store.db.execute(
        "SELECT source_id FROM capture_sources JOIN runs USING(run_id) WHERE runs.ended_at IS NULL ORDER BY source_id"
    ).fetchall()
    return [*deliveries, *(drain(store, row[0]) for row in sources)]


class FileNotifications:
    """Native vnode notifications on macOS/BSD; offset reconciliation elsewhere."""

    def __init__(self, receiver: socket.socket):
        self.receiver = receiver
        self.queue = select.kqueue() if hasattr(select, "kqueue") else None
        self.files: dict[str, tuple[int, tuple[int, int]]] = {}
        if self.queue:
            self.queue.control([select.kevent(receiver.fileno(), filter=select.KQ_FILTER_READ,
                                             flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR)], 0)

    def refresh(self, store: ContinuityStore) -> None:
        if not self.queue:
            return
        rows = store.db.execute("SELECT source_id,payload FROM capture_sources JOIN runs USING(run_id) WHERE ended_at IS NULL")
        wanted = {row["source_id"]: json.loads(row["payload"])["path"] for row in rows}
        for source, (fd, identity) in list(self.files.items()):
            try:
                stat = os.stat(wanted[source])
                same = identity == (stat.st_dev, stat.st_ino)
            except (KeyError, OSError):
                same = False
            if not same:
                os.close(fd)  # Closing a descriptor unregisters its vnode filter.
                del self.files[source]
        for source, path in wanted.items():
            if source in self.files:
                continue
            fd = None
            try:
                fd = os.open(path, getattr(os, "O_EVTONLY", os.O_RDONLY))
                stat = os.fstat(fd)
                self.queue.control([select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                    fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE)], 0)
                self.files[source] = (fd, (stat.st_dev, stat.st_ino))
            except OSError:
                if fd is not None and source not in self.files:
                    os.close(fd)
                # A missing file is caught by reconciliation, including files
                # registered before native session creation.

    def wait(self, timeout: float) -> set[str]:
        if self.queue:
            changed = self.queue.control(None, 128, timeout)
            ids = {fd: source for source, (fd, _) in self.files.items()}
            ready = {ids[event.ident] for event in changed if event.ident in ids}
            if not any(event.ident == self.receiver.fileno() for event in changed):
                return ready
        else:
            ready = set()
            self.receiver.settimeout(timeout)
        try:
            ready.add(self.receiver.recv(1024).decode("utf-8"))
        except (socket.timeout, BlockingIOError, UnicodeDecodeError):
            pass
        return ready

    def close(self) -> None:
        for fd, _ in self.files.values():
            os.close(fd)
        if self.queue:
            self.queue.close()


def serve(root: Path, *, interval: float = 5.0, stop=None) -> None:
    if not math.isfinite(interval) or interval <= 0:
        raise ValidationError("reconciliation interval must be positive")
    directory = root / "run"
    directory.mkdir(parents=True, exist_ok=True)
    path = socket_path(root)
    if path.parent != directory:
        path.parent.mkdir(mode=0o700, exist_ok=True)
        stat = path.parent.lstat()
        if path.parent.is_symlink() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            raise HxError("observer socket directory must be owned by this user and mode 0700")
    with (directory / "observer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HxError("observer already running for this root") from exc
        path.unlink(missing_ok=True)
        with ContinuityStore(root) as store, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            receiver.bind(str(path))
            os.chmod(path, 0o600)
            receiver.setblocking(False)
            notifications = FileNotifications(receiver)
            deadline = 0.0
            try:
                while stop is None or not stop.is_set():
                    if time.monotonic() >= deadline:
                        reconcile(store)
                        notifications.refresh(store)
                        deadline = time.monotonic() + interval
                    for source_id in notifications.wait(max(0.001, min(deadline - time.monotonic(), 1.0))):
                        if source_id == "*":
                            reconcile(store)
                        elif store.db.execute("SELECT 1 FROM capture_sources WHERE source_id=?", (source_id,)).fetchone():
                            drain(store, source_id)
            finally:
                notifications.close()
                path.unlink(missing_ok=True)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx observe")
    parser.add_argument("--root")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("register")
    for field in ("run", "stream", "path", "session"):
        add.add_argument(f"--{field}", required=True)
    add.add_argument("--decoder", choices=sorted(DECODERS), required=True)
    add.add_argument("--branch-ids", help="JSON array of ordered active native ancestry IDs")
    for name in ("drain", "serve", "status"):
        commands.add_parser(name)
    for child in commands.choices.values():
        child.add_argument("--root", default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(root)
        return 0
    with ContinuityStore(root) as store:
        if args.command == "register":
            try:
                branch_ids = json.loads(args.branch_ids) if args.branch_ids else None
            except json.JSONDecodeError as exc:
                raise ValidationError("--branch-ids must be JSON") from exc
            print(register(store, run_id=args.run, stream_id=args.stream, path=Path(args.path),
                           decoder=args.decoder, session_id=args.session, branch_ids=branch_ids))
        elif args.command == "drain":
            print(canonical(reconcile(store)))
        else:
            print(canonical([{**dict(row), "payload": json.loads(row["payload"])}
                             for row in store.db.execute("SELECT * FROM capture_sources ORDER BY source_id")]))
    return 0
