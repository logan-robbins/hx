"""Episode memory: every compaction output the Companion ever wrote, searchable (spec 07.2, 10).

Each ingested step state is one *episode chunk* — the Companion's own compaction of a slice of
a stream. hx already throws those away as soon as the next pass overwrites `state/<id>/<stream>.json`.
This module keeps them: one ChromaDB collection per instance, `state/memory/chroma`, collection
`episodes`, holding every agent's episodes together. Filtering by persona happens at query time,
not at write time, so one agent can deliberately read what another learned.

Two halves, because the write points are hooks and hooks must stay fast and must never fail:

  - **enqueue** (`enqueue`) appends one small JSON file to `state/memory/queue/<uuid>.json` with
    an atomic write. No chromadb import, no lock, no embedding, no network. Four call sites:
    `companion.ingest` (kind `pass`), `seam.seam` (`seam`), `hook_compact.post` (`compact`),
    `complete.complete` (`complete`). Each wraps the call so an exception is logged to the
    instance's problem log and never propagates.
  - **index** (`index`, and lazily inside `search` and the compose injection) drains that queue
    into chroma. Every chroma open — reads included — happens under an exclusive `fcntl.flock`
    on `state/memory/index.lock`, because several agents' processes share one PersistentClient
    directory and chroma is not multi-process safe. The import is lazy and its failure is a
    first-class outcome: `hx memory search` prints one line and exits 2, compose prints one
    line in the section and composes everything else as usual.

Ranking is similarity *and* recency, because a six-week-old episode about the same file is
usually worse advice than yesterday's:

    similarity      = 1 - cosine_distance
    recency_weight  = 0.5 + 0.5 * 0.5 ** (age_hours / half_life_hours)   # half life 24h
    score           = similarity * recency_weight

so recency can at most halve a score and never inverts a large similarity gap. Chroma does the
ANN fetch (`4*k`, at least 20, bounded by the collection count) with the metadata filter; the
re-rank is done here in Python, because chroma has no notion of the age of a thing.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import store, timestamps

#: The collection every episode of this instance lands in. One per instance, never per agent.
COLLECTION = "episodes"

#: Spec 07.2/08 boundaries, in the order they happen to a conversation.
KINDS = ("pass", "seam", "compact", "complete")

#: Default half life of the recency weight, in hours.
HALF_LIFE_H = 24.0

#: `hx memory search` default.
DEFAULT_K = 5

#: How many hits to pull out of chroma before the Python re-rank.
FETCH_FACTOR = 4
FETCH_FLOOR = 20

#: Compose injection defaults, overridable per agent in `harness.json.companion`.
DEFAULT_INJECT_K = 5
DEFAULT_EPISODE_CHARS = 700

#: Everything the CLI and the compose section print starts with this, like every other hx line.
PREFIX = "HX-MEMORY"


class MemoryUnavailable(RuntimeError):
    """chromadb could not be imported or opened. Never fatal to anything but `hx memory`."""


# --- where things live --------------------------------------------------------------------------


def memory_dir(root: Path) -> Path:
    return Path(root) / "state" / "memory"


def queue_dir(root: Path) -> Path:
    return memory_dir(root) / "queue"


def chroma_dir(root: Path) -> Path:
    return memory_dir(root) / "chroma"


def lock_path(root: Path) -> Path:
    return memory_dir(root) / "index.lock"


def last_dir(root: Path) -> Path:
    return memory_dir(root) / "last"


def episode_id(item_id: str, stream: str, kind: str, seq: int) -> str:
    """Stable and idempotent: re-indexing the same boundary upserts rather than duplicating."""
    return f"{item_id}/{stream}/{kind}/{seq}"


# --- the document -------------------------------------------------------------------------------


def _entries(state: dict, key: str) -> list:
    value = state.get(key)
    return value if isinstance(value, list) else []


def render_episode(state: dict) -> str:
    """The text an episode is embedded and read back as: dense, no labels the reader must skip.

    Deliberately not `compose.render_step_state`: that one is markdown for a model reading a
    context file, this one is a retrieval chunk. It carries the facts a later search is looking
    for — what the goal was, what was decided and why, what closed and whether it was verified,
    what was tried and failed, which files and what the failure said — and nothing decorative.
    """
    lines: list[str] = []
    if state.get("goal"):
        lines.append(f"goal: {state['goal']}")

    for item in _entries(state, "constraints"):
        lines.append(f"constraint: {item if isinstance(item, str) else json.dumps(item)}")

    for item in _entries(state, "decisions"):
        if isinstance(item, dict):
            why = f" why={item.get('why', '')}" if item.get("why") else ""
            lines.append(f"decision: {item.get('d', '')}{why}")
        else:
            lines.append(f"decision: {item}")

    for item in _entries(state, "open_steps"):
        if isinstance(item, dict):
            nxt = f" next={item['next']}" if item.get("next") else ""
            lines.append(f"open {item.get('id', '?')}: {item.get('intent', '')}{nxt}")

    for item in _entries(state, "closed_steps"):
        if isinstance(item, dict):
            verified = "verified" if item.get("verified") else "unverified"
            commit = f" {item['commit']}" if item.get("commit") else ""
            lines.append(f"closed {item.get('id', '?')}: {item.get('outcome', '')} "
                         f"({verified}){commit}")

    for item in _entries(state, "dead_ends"):
        lines.append(f"dead end: {item if isinstance(item, str) else json.dumps(item)}")

    working_set = state.get("working_set")
    if isinstance(working_set, dict):
        for commit in working_set.get("commits") or []:
            if isinstance(commit, dict):
                lines.append(f"commit {commit.get('sha', '')} {commit.get('msg', '')}".rstrip())
        if working_set.get("hypothesis"):
            lines.append(f"hypothesis: {working_set['hypothesis']}")
        if working_set.get("last_failure"):
            lines.append(f"last failure: {working_set['last_failure']}")
        for entry in working_set.get("files") or []:
            if isinstance(entry, dict):
                lines.append(f"file {entry.get('path', '')}: {entry.get('note', '')}".rstrip())

    for item in _entries(state, "blockers"):
        lines.append(f"blocker: {item if isinstance(item, str) else json.dumps(item)}")

    return "\n".join(line for line in lines if line.strip())


def _prompt_version(state: dict) -> str:
    """`{"base": sha, "role": sha}` flattened to `base/role`: chroma metadata is scalars only."""
    version = state.get("prompt_version")
    if not isinstance(version, dict):
        return ""
    base = str(version.get("base") or "")
    role = str(version.get("role") or "")
    return f"{base}/{role}" if (base or role) else ""


# --- enqueue (the write points) -------------------------------------------------------------------


def _config(root: Path, item_id: str) -> dict:
    """`config/<id>/harness.json` as a plain dict. No validation: this runs inside hooks."""
    path = Path(root) / "config" / item_id / "harness.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _fingerprint_path(root: Path, item_id: str, stream: str) -> Path:
    return last_dir(root) / f"{item_id}-{stream}.sha256"


def _unix(ts: str) -> float:
    """Unix seconds for an hx timestamp (`2026-09-21T06:45:00Z`); now when it does not parse."""
    try:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, AttributeError):
        return time.time()


def enqueue(
    root: Path,
    item_id: str,
    stream: str,
    kind: str,
    state_or_text: dict | str,
    **meta,
) -> Path | None:
    """Queue one episode for indexing. Returns the queue file, or `None` when it was skipped.

    Skipped means either an empty document or — for `kind="pass"` only — a document identical
    to the previous pass on the same stream. A Companion that is woken on every turn writes a
    great many states that differ by nothing the search can use; indexing each one would fill
    the collection with near-duplicate chunks that crowd out everything else.
    """
    root = Path(root)
    if kind not in KINDS:
        raise ValueError(f"{kind}: not one of {', '.join(KINDS)}")

    state = state_or_text if isinstance(state_or_text, dict) else {}
    if isinstance(state_or_text, str):
        document = state_or_text.strip()
    else:
        document = render_episode(state)
    extra = meta.pop("text", None)
    if extra:
        document = f"{document}\n{extra}".strip() if document else str(extra).strip()
    if not document:
        return None

    if kind == "pass":
        digest = hashlib.sha256(document.encode()).hexdigest()
        fingerprint = _fingerprint_path(root, item_id, stream)
        if fingerprint.is_file():
            try:
                if fingerprint.read_text().strip() == digest:
                    return None
            except OSError:
                pass
        store.atomic_write_text(fingerprint, digest + "\n")

    config = _config(root, item_id)
    seq = meta.pop("seq", None)
    if seq is None:
        seq = state.get("seq")
    try:
        seq = int(seq or 0)
    except (TypeError, ValueError):
        seq = 0

    # The episode's time is the state's own ingest stamp when it has one (a backfilled or
    # re-indexed state keeps its real age); a text episode is stamped now.
    ts = str(meta.pop("ts", None) or state.get("ts") or timestamps.now())
    t = meta.pop("t", None)
    if t is None:
        t = _unix(ts)
    metadata = {
        "ts": ts,
        "t": float(t),
        "id": item_id,
        "pod": str(meta.pop("pod", None) or config.get("pod") or ""),
        "role": str(meta.pop("role", None) or config.get("role") or ""),
        "stream": stream,
        "kind": kind,
        "seq": seq,
        "outcome": str(meta.pop("outcome", None) or ""),
        "prompt_version": str(meta.pop("prompt_version", None) or _prompt_version(state)),
    }
    # Anything else the caller passed is kept, as long as it is a chroma-legal scalar.
    for key, value in meta.items():
        if isinstance(value, (str, int, float, bool)):
            metadata[key] = value

    path = queue_dir(root) / f"{uuid.uuid4().hex}.json"
    store.atomic_write_json(path, {
        "episode_id": episode_id(item_id, stream, kind, seq),
        "document": document,
        "metadata": metadata,
    })
    return path


def enqueue_quietly(root: Path, item_id: str, stream: str, kind: str, state_or_text, **meta) -> None:
    """`enqueue` that cannot fail its caller. Every write point uses this one.

    Memory is an accelerator, never a precondition: a hook that dies because the queue
    directory is read-only would take the agent's turn down with it.
    """
    try:
        enqueue(root, item_id, stream, kind, state_or_text, **meta)
    except Exception as exc:  # noqa: BLE001 — the whole point is that nothing escapes
        try:
            from .hooks import log_error

            log_error(root, item_id, "memory", f"enqueue {kind} {stream} failed: {exc}")
        except Exception:  # noqa: BLE001
            pass


# --- the lock and the collection ---------------------------------------------------------------


@contextmanager
def locked(root: Path):
    """Exclusive `flock` around every chroma open, reads included (several agents share it)."""
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)


def _collection(root: Path):
    """Open (or create) the `episodes` collection. Call only inside `locked`."""
    try:
        import chromadb
        from chromadb.config import Settings
    except Exception as exc:  # noqa: BLE001 — an ImportError, or anything its import chain raises
        raise MemoryUnavailable(f"chromadb is not importable ({exc})") from exc

    directory = chroma_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        client = chromadb.PersistentClient(
            path=str(directory), settings=Settings(anonymized_telemetry=False)
        )
        return client.get_or_create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine"}
        )
    except MemoryUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MemoryUnavailable(f"{directory}: chroma would not open ({exc})") from exc


# --- indexing -----------------------------------------------------------------------------------


def store_exists(root: Path) -> bool:
    """Is there anything at all to search — an indexed collection, or something queued?

    Checked before every read so that an instance which has never produced an episode never
    pays for opening chroma at all. That matters more than it sounds: `hx compose` runs at
    every boundary, and the default embedding function downloads an ONNX model into
    `~/.cache/chroma` the first time it is actually asked to embed something.
    """
    root = Path(root)
    return bool(queued(root)) or (chroma_dir(root) / "chroma.sqlite3").is_file()


def queued(root: Path) -> list[Path]:
    directory = queue_dir(root)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def _index_locked(root: Path, collection) -> int:
    """Drain the queue into an already-open collection. Returns how many were indexed."""
    files = queued(root)
    if not files:
        return 0

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    consumed: list[Path] = []
    seen: dict[str, int] = {}
    for path in files:
        try:
            item = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            # A queue file hx cannot read is a queue file hx will never be able to read.
            path.unlink(missing_ok=True)
            continue
        consumed.append(path)
        if not isinstance(item, dict) or not item.get("document"):
            continue
        key = str(item.get("episode_id") or "")
        if not key:
            continue
        metadata = item.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if key in seen:
            # Same id twice in one drain: the later file wins, which is what upsert would do.
            index = seen[key]
            documents[index] = str(item["document"])
            metadatas[index] = metadata
            continue
        seen[key] = len(ids)
        ids.append(key)
        documents.append(str(item["document"]))
        metadatas.append(metadata)

    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    for path in consumed:
        path.unlink(missing_ok=True)
    return len(ids)


def stats_path(root: Path) -> Path:
    """`state/memory/stats.json`: the collection size as of the last index, for `hx board`."""
    return memory_dir(root) / "stats.json"


def index(root: Path) -> tuple[int, int]:
    """Drain the queue into chroma. Returns (indexed, total in the collection)."""
    root = Path(root)
    with locked(root):
        collection = _collection(root)
        indexed = _index_locked(root, collection)
        total = collection.count()
        store.atomic_write_json(stats_path(root), {"episodes": total, "ts": timestamps.now()})
        return indexed, total


def summary(root: Path) -> dict:
    """The board's `memory` block, without opening chroma: `{episodes, queued, indexed_ts}`.

    `episodes` is the count as of the last index (`stats.json`), null before the first one;
    `queued` is what the next index will add.
    """
    root = Path(root)
    episodes = None
    indexed_ts = None
    try:
        data = json.loads(stats_path(root).read_text())
        if isinstance(data, dict):
            episodes = data.get("episodes")
            indexed_ts = data.get("ts")
    except (OSError, json.JSONDecodeError):
        pass
    return {"episodes": episodes, "queued": len(queued(root)), "indexed_ts": indexed_ts}


# --- search --------------------------------------------------------------------------------------


def _where(filters: dict[str, object], *, exclude_id: str | None = None) -> dict | None:
    """Chroma's `where`: one clause bare, several under `$and` (it rejects a bare multi-key)."""
    clauses = [{key: {"$eq": value}} for key, value in filters.items() if value]
    if exclude_id:
        clauses.append({"id": {"$ne": exclude_id}})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def recency_weight(age_hours: float, half_life_h: float = HALF_LIFE_H) -> float:
    """1.0 for something written now, 0.75 after one half life, never below 0.5."""
    if half_life_h <= 0:
        return 1.0
    return 0.5 + 0.5 * (0.5 ** (max(0.0, age_hours) / half_life_h))


def search(
    root: Path,
    query: str,
    *,
    role: str | None = None,
    pod: str | None = None,
    item: str | None = None,
    kind: str | None = None,
    k: int = DEFAULT_K,
    half_life_h: float = HALF_LIFE_H,
    exclude_id: str | None = None,
    now: float | None = None,
) -> list[dict]:
    """Recency-weighted semantic search. Indexes whatever is queued first, under the same lock."""
    root = Path(root)
    if k <= 0 or not query.strip() or not store_exists(root):
        return []
    now = time.time() if now is None else now

    with locked(root):
        collection = _collection(root)
        _index_locked(root, collection)
        total = collection.count()
        if not total:
            return []
        wanted = min(max(FETCH_FACTOR * k, FETCH_FLOOR), total)
        where = _where(
            {"role": role, "pod": pod, "id": item, "kind": kind}, exclude_id=exclude_id
        )
        result = collection.query(
            query_texts=[query],
            n_results=wanted,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

    hits = []
    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    for position, key in enumerate(ids):
        metadata = metadatas[position] if position < len(metadatas) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        distance = float(distances[position]) if position < len(distances) else 1.0
        similarity = 1.0 - distance
        try:
            written = float(metadata.get("t") or 0.0)
        except (TypeError, ValueError):
            written = 0.0
        age_hours = max(0.0, (now - written) / 3600.0) if written else 0.0
        weight = recency_weight(age_hours, half_life_h)
        hits.append({
            "id": key,
            "ts": str(metadata.get("ts") or ""),
            "t": written,
            "role": str(metadata.get("role") or ""),
            "pod": str(metadata.get("pod") or ""),
            "item": str(metadata.get("id") or ""),
            "stream": str(metadata.get("stream") or ""),
            "kind": str(metadata.get("kind") or ""),
            "seq": int(metadata.get("seq") or 0),
            "outcome": str(metadata.get("outcome") or ""),
            "similarity": similarity,
            "score": similarity * weight,
            "document": documents[position] if position < len(documents) else "",
        })

    hits.sort(key=lambda hit: (-hit["score"], -hit["t"], hit["id"]))
    return hits[:k]


def episodes(
    root: Path,
    *,
    item: str | None = None,
    role: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Metadata only, newest first. What `hx memory list` prints."""
    root = Path(root)
    with locked(root):
        collection = _collection(root)
        _index_locked(root, collection)
        if not collection.count():
            return []
        found = collection.get(
            where=_where({"id": item, "role": role, "kind": kind}),
            include=["metadatas"],
        )
    rows = []
    for position, key in enumerate(found.get("ids") or []):
        metadata = (found.get("metadatas") or [])[position]
        metadata = metadata if isinstance(metadata, dict) else {}
        rows.append({
            "id": key,
            "ts": str(metadata.get("ts") or ""),
            "t": float(metadata.get("t") or 0.0),
            "item": str(metadata.get("id") or ""),
            "pod": str(metadata.get("pod") or ""),
            "role": str(metadata.get("role") or ""),
            "stream": str(metadata.get("stream") or ""),
            "kind": str(metadata.get("kind") or ""),
            "seq": int(metadata.get("seq") or 0),
            "outcome": str(metadata.get("outcome") or ""),
        })
    rows.sort(key=lambda row: (-row["t"], row["id"]))
    return rows[: max(0, limit)]


def stats(root: Path) -> dict:
    """Counts by role and by kind, plus how many episodes are still waiting to be indexed."""
    root = Path(root)
    pending = len(queued(root))
    with locked(root):
        collection = _collection(root)
        indexed = _index_locked(root, collection)
        total = collection.count()
        metadatas = []
        if total:
            found = collection.get(include=["metadatas"])
            metadatas = [m for m in (found.get("metadatas") or []) if isinstance(m, dict)]

    by_role: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_id: dict[str, int] = {}
    for metadata in metadatas:
        by_role[str(metadata.get("role") or "")] = by_role.get(str(metadata.get("role") or ""), 0) + 1
        by_kind[str(metadata.get("kind") or "")] = by_kind.get(str(metadata.get("kind") or ""), 0) + 1
        by_id[str(metadata.get("id") or "")] = by_id.get(str(metadata.get("id") or ""), 0) + 1
    return {
        "total": total,
        "indexed_now": indexed,
        "queued": max(0, pending - indexed),
        "by_role": dict(sorted(by_role.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "by_id": dict(sorted(by_id.items())),
        "path": str(chroma_dir(root)),
    }


# --- compose injection ------------------------------------------------------------------------


def companion_settings(root: Path, item_id: str) -> tuple[int, int, float]:
    """`memory_inject_k`, `memory_episode_chars`, `memory_half_life_h` for one agent."""
    companion = _config(root, item_id).get("companion")
    companion = companion if isinstance(companion, dict) else {}

    def integer(key: str, default: int) -> int:
        value = companion.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return value

    return (
        integer("memory_inject_k", DEFAULT_INJECT_K),
        integer("memory_episode_chars", DEFAULT_EPISODE_CHARS),
        float(integer("memory_half_life_h", int(HALF_LIFE_H))),
    )


def inject_query(state: dict, fallback: str = "") -> str:
    """What the context file asks memory about: the goal, what is next, and the hypothesis."""
    parts: list[str] = []
    if state.get("goal"):
        parts.append(str(state["goal"]))
    for item in _entries(state, "open_steps"):
        if isinstance(item, dict):
            for key in ("intent", "next"):
                if item.get(key):
                    parts.append(str(item[key]))
    working_set = state.get("working_set")
    if isinstance(working_set, dict) and working_set.get("hypothesis"):
        parts.append(str(working_set["hypothesis"]))
    text = " ".join(part.strip() for part in parts if str(part).strip())
    return text or fallback.strip()


def truncate(text: str, limit: int) -> str:
    """One hit, one line: newlines become `; ` so the section stays a readable bullet list."""
    flat = "; ".join(line.strip() for line in text.splitlines() if line.strip())
    return flat if len(flat) <= limit else flat[: max(0, limit - 1)].rstrip() + "…"


def section_text(root: Path, item_id: str, stream: str, state: dict, *, fallback: str = "") -> str:
    """The body of the context file's "Memory episodes" section. Never raises.

    The own id is excluded: this agent's own step state is already the section above, and
    seeing it again as a "memory" would be noise at best and a loop at worst. With fewer than
    two hits under the role filter the search is repeated across all roles, because a thin
    own-role result is exactly when another persona's episode is worth reading.
    """
    root = Path(root)
    k, chars, half_life = companion_settings(root, item_id)
    if k <= 0:
        return ""
    is_main = stream == f"{item_id}-main"
    if not is_main:
        k = max(1, k // 2)

    query = inject_query(state, fallback)
    if not query:
        return "_no query: the step state has no goal yet_"

    role = str(_config(root, item_id).get("role") or "") or None
    try:
        hits = search(
            root, query, role=role, k=k, half_life_h=half_life, exclude_id=item_id
        )
        if len(hits) < 2 and role:
            widened = search(
                root, query, k=k, half_life_h=half_life, exclude_id=item_id
            )
            if len(widened) > len(hits):
                hits = widened
    except MemoryUnavailable as exc:
        return f"_memory unavailable: {exc}_"
    except Exception as exc:  # noqa: BLE001 — the context file is composed whatever chroma does
        return f"_memory unavailable: {exc}_"

    if not hits:
        return ""
    lines = [f'Search more: `hx memory search "{query[:120]}" --all-roles`', ""]
    for hit in hits:
        lines.append(
            f"- {hit['ts']} {hit['item']}/{hit['kind']} s={hit['score']:.2f}: "
            f"{truncate(hit['document'], chars)}"
        )
    return "\n".join(lines)


# --- the CLI ---------------------------------------------------------------------------------------


def caller_role(root: Path, env) -> str | None:
    """The persona of whoever is running `hx memory search`, for the default filter.

    `start.sh` exports both `HARNESS_ID` and `HX_ROLE` onto an agent's session, so an agent
    searches its own persona's episodes by default and a human at a shell searches all of them.
    """
    if env is None:
        return None
    role = env.get("HX_ROLE")
    if role:
        return role
    item_id = env.get("HARNESS_ID")
    if item_id:
        return str(_config(root, item_id).get("role") or "") or None
    return None


def _print_hits(hits: list[dict], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(hits, indent=2))
        return
    if not hits:
        print(f"{PREFIX} no episodes matched")
        return
    for hit in hits:
        print(f"## {hit['ts']} {hit['item']} {hit['role']} {hit['kind']} "
              f"seq={hit['seq']} score={hit['score']:.2f}")
        print(hit["document"])
        print()


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx memory", add_help=True)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    find = sub.add_parser("search", help="recency-weighted semantic search over episodes")
    find.add_argument("query")
    find.add_argument("--role", default=None, help="persona filter (default: your own)")
    find.add_argument("--pod", default=None)
    find.add_argument("--id", dest="item", default=None, help="one agent's episodes only")
    find.add_argument("--kind", default=None, choices=list(KINDS))
    find.add_argument("--k", type=int, default=DEFAULT_K)
    find.add_argument("--all-roles", action="store_true", help="drop the persona filter")
    find.add_argument("--half-life-h", type=float, default=HALF_LIFE_H)
    find.add_argument("--json", action="store_true")
    find.add_argument("--root", help=argparse.SUPPRESS)

    drain = sub.add_parser("index", help="drain the queue into chroma")
    drain.add_argument("--root", help=argparse.SUPPRESS)

    listing = sub.add_parser("list", help="episode metadata, newest first")
    listing.add_argument("--id", dest="item", default=None)
    listing.add_argument("--role", default=None)
    listing.add_argument("--kind", default=None, choices=list(KINDS))
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--root", help=argparse.SUPPRESS)

    counts = sub.add_parser("stats", help="counts by role and kind, and the queue length")
    counts.add_argument("--json", action="store_true")
    counts.add_argument("--root", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    root = Path(root)

    try:
        if args.command == "search":
            role = None if args.all_roles else (args.role or caller_role(root, env))
            hits = search(
                root, args.query,
                role=role, pod=args.pod, item=args.item, kind=args.kind,
                k=args.k, half_life_h=args.half_life_h,
            )
            _print_hits(hits, as_json=args.json)
            return 0

        if args.command == "index":
            indexed, total = index(root)
            print(f"{PREFIX} indexed={indexed} total={total}")
            return 0

        if args.command == "list":
            rows = episodes(root, item=args.item, role=args.role, kind=args.kind, limit=args.limit)
            if args.json:
                print(json.dumps(rows, indent=2))
                return 0
            if not rows:
                print(f"{PREFIX} no episodes")
                return 0
            for row in rows:
                outcome = f" {row['outcome']}" if row["outcome"] else ""
                print(f"{row['ts']} {row['item']} {row['role']} {row['kind']} "
                      f"seq={row['seq']} {row['stream']}{outcome}")
            return 0

        summary = stats(root)
        if args.json:
            print(json.dumps(summary, indent=2))
            return 0
        print(f"{PREFIX} total={summary['total']} queued={summary['queued']} "
              f"path={summary['path']}")
        for label, counted in (("role", summary["by_role"]), ("kind", summary["by_kind"]),
                               ("id", summary["by_id"])):
            for name, count in counted.items():
                print(f"  {label} {name or '(none)'}: {count}")
        return 0
    except MemoryUnavailable as exc:
        print(f"hx: memory: {exc}", file=sys.stderr)
        return 2
