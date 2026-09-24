"""Episode memory: the queue, the flock-guarded index, recency-weighted search, injection.

`src/hx/memory.py` and `docs/memory.md`. Two halves are tested separately on purpose, because
they have opposite requirements: the write points must be fast and unfailable and never import
chromadb, and the read side must hold an exclusive lock around every chroma open.

The chroma-touching tests run in-process, so the default embedding function finds the ONNX
model already cached in the real `~/.cache/chroma` instead of downloading 80MB into a scratch
HOME. They skip when chromadb is not installed.
"""

from __future__ import annotations

import fcntl
import json
import os
import time

import pytest

from .test_streams import working  # noqa: F401 — a dispatched agent on the private tmux server
from .test_transitions import dispatch_working

try:  # the module-level skip the brief asks for
    import chromadb  # noqa: F401

    HAS_CHROMA = True
except Exception:  # noqa: BLE001
    HAS_CHROMA = False

needs_chroma = pytest.mark.skipif(not HAS_CHROMA, reason="chromadb is not installed")


# --- helpers ---------------------------------------------------------------------------------


def queue_items(instance) -> list[dict]:
    from hx import memory

    return [json.loads(path.read_text()) for path in memory.queued(instance)]


def state_with(goal: str, **extra) -> dict:
    state = {
        "seq": 7,
        "goal": goal,
        "prompt_version": {"base": "aaaa1111", "role": "bbbb2222"},
        "decisions": [{"d": "stream with csv.reader", "why": "read_csv held 2.1GB"}],
        "open_steps": [{"id": "st2", "intent": "port the transform", "next": "run pytest -k csv"}],
        "closed_steps": [
            {"id": "st1", "outcome": "peak RSS 2.1GB -> 180MB", "verified": True, "commit": "a1b2c3d"}
        ],
        "dead_ends": ["chunksize=10000: still 900MB"],
        "working_set": {
            "files": [{"path": "src/importer/csv_in.py", "note": "read_rows() is the entry point"}],
            "last_failure": "MemoryError at csv_in.py:88",
            "hypothesis": "the parse dominates, not the transform",
        },
        "blockers": [],
    }
    state.update(extra)
    return state


def seed(instance, item_id, stream, kind, state_or_text, *, age_h=0.0, **meta):
    """Enqueue one episode with an explicit age, so recency is testable without sleeping."""
    from hx import memory, timestamps

    when = time.time() - age_h * 3600.0
    return memory.enqueue(
        instance, item_id, stream, kind, state_or_text,
        t=when, ts=timestamps.from_unix(when), **meta,
    )


# --- the document ------------------------------------------------------------------------------


def test_render_episode_carries_what_a_later_search_is_looking_for():
    from hx.memory import render_episode

    text = render_episode(state_with("make the CSV importer stream"))
    for fragment in (
        "goal: make the CSV importer stream",
        "decision: stream with csv.reader why=read_csv held 2.1GB",
        "closed st1: peak RSS 2.1GB -> 180MB (verified) a1b2c3d",
        "dead end: chunksize=10000: still 900MB",
        "hypothesis: the parse dominates",
        "last failure: MemoryError at csv_in.py:88",
        "file src/importer/csv_in.py: read_rows() is the entry point",
    ):
        assert fragment in text, text


def test_an_unverified_closed_step_says_so():
    from hx.memory import render_episode

    text = render_episode({"seq": 1, "closed_steps": [{"id": "s", "outcome": "done"}]})
    assert "(unverified)" in text


# --- enqueue: the queue file contract ------------------------------------------------------------


def test_enqueue_writes_one_queue_file_with_the_metadata_contract(instance):
    from hx import memory

    path = memory.enqueue(instance, "eng-001", "eng-001-main", "seam",
                          state_with("stream the importer"))
    assert path is not None and path.parent == instance / "state" / "memory" / "queue"

    item = json.loads(path.read_text())
    assert item["episode_id"] == "eng-001/eng-001-main/seam/7"
    assert "stream the importer" in item["document"]

    metadata = item["metadata"]
    assert set(metadata) == {
        "ts", "t", "id", "pod", "role", "stream", "kind", "seq", "outcome", "prompt_version",
    }
    assert metadata["id"] == "eng-001" and metadata["pod"] == "engineers"
    assert metadata["role"] == "engineer", "read from config/<id>/harness.json, not passed in"
    assert metadata["stream"] == "eng-001-main" and metadata["kind"] == "seam"
    assert metadata["seq"] == 7 and metadata["outcome"] == ""
    assert metadata["prompt_version"] == "aaaa1111/bbbb2222", "flattened: chroma takes scalars"
    assert metadata["ts"].endswith("Z") and isinstance(metadata["t"], float)
    # Every value is a chroma-legal scalar, or the upsert would fail at index time.
    assert all(isinstance(value, (str, int, float, bool)) for value in metadata.values())


def test_enqueue_imports_no_chromadb(instance, monkeypatch):
    """The write points run inside hooks. An import that costs a second is a second per turn."""
    import builtins

    real = builtins.__import__

    def refuse(name, *args, **kwargs):
        assert not name.startswith("chromadb"), "enqueue must not import chromadb"
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    from hx import memory

    assert memory.enqueue(instance, "eng-001", "eng-001-main", "pass", state_with("g")) is not None


def test_a_pass_identical_to_the_previous_one_is_skipped(instance):
    """A Companion woken every turn writes many states that differ by nothing searchable."""
    from hx import memory

    state = state_with("stream the importer")
    assert memory.enqueue(instance, "eng-001", "eng-001-main", "pass", state) is not None
    assert memory.enqueue(instance, "eng-001", "eng-001-main", "pass", dict(state, seq=8)) is None
    assert len(memory.queued(instance)) == 1

    moved = dict(state, seq=9, blockers=["the fixture data is missing"])
    assert memory.enqueue(instance, "eng-001", "eng-001-main", "pass", moved) is not None
    assert len(memory.queued(instance)) == 2


def test_the_skip_is_per_stream_and_only_for_passes(instance):
    from hx import memory

    state = state_with("stream the importer")
    memory.enqueue(instance, "eng-001", "eng-001-main", "pass", state)
    assert memory.enqueue(instance, "eng-001", "eng-001-s001", "pass", state) is not None
    assert memory.enqueue(instance, "eng-001", "eng-001-main", "seam", state) is not None


def test_an_empty_document_queues_nothing(instance):
    from hx import memory

    assert memory.enqueue(instance, "eng-001", "eng-001-main", "pass", {"seq": 1}) is None
    assert memory.enqueue(instance, "eng-001", "eng-001-main", "complete", "   ") is None
    assert memory.queued(instance) == []


def test_an_unknown_kind_is_refused(instance):
    from hx import memory

    with pytest.raises(ValueError):
        memory.enqueue(instance, "eng-001", "eng-001-main", "musings", state_with("g"))


def test_enqueue_quietly_logs_and_never_raises(instance, monkeypatch):
    """Memory is an accelerator, never a precondition: a hook must survive a broken queue."""
    from hx import memory, store

    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(store, "atomic_write_json", boom)
    memory.enqueue_quietly(instance, "eng-001", "eng-001-main", "seam", state_with("g"))

    errors = instance / "logs" / "eng-001" / "hook-errors.log"
    assert errors.is_file() and "read-only file system" in errors.read_text()


# --- the four write points --------------------------------------------------------------------


def test_companion_ingest_enqueues_the_installed_state(instance):
    from hx.companion import ingest, out_path

    out = out_path(instance, "eng-001", "eng-001-main")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state_with("stream the importer")))

    assert ingest(instance, "eng-001", "eng-001-main")["goal"] == "stream the importer"
    items = queue_items(instance)
    assert len(items) == 1
    assert items[0]["metadata"]["kind"] == "pass"
    assert items[0]["metadata"]["role"] == "engineer"
    # hx stamps the state before it is queued, so the episode carries the real prompt version.
    assert items[0]["metadata"]["prompt_version"].count("/") == 1
    assert "stream the importer" in items[0]["document"]


def test_seam_enqueues_the_main_state(working, tmux_server):  # noqa: F811
    from hx.hook_log import seam_marker
    from hx.seam import seam

    state = working / "state" / "eng-001"
    state.mkdir(parents=True, exist_ok=True)
    (state / "eng-001-main.json").write_text(json.dumps(state_with("stream the importer")))

    marker = seam_marker(working, "eng-001")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    assert seam(working, "eng-001", env={"HX_TMUX": " ".join(tmux_server)})["outcome"] == "taken"

    kinds = [item["metadata"]["kind"] for item in queue_items(working)]
    assert "seam" in kinds
    seamed = [i for i in queue_items(working) if i["metadata"]["kind"] == "seam"][0]
    assert "stream the importer" in seamed["document"]
    assert seamed["metadata"]["seq"] > 0, "the seq of the seam record itself"


def test_postcompact_enqueues_the_summary_it_kept(instance):
    from hx.hook_compact import post

    state = instance / "state" / "eng-001"
    state.mkdir(parents=True, exist_ok=True)
    (state / "eng-001-main.json").write_text(json.dumps(state_with("stream the importer")))

    assert post({"hook_event_name": "PostCompact", "trigger": "auto",
                 "compact_summary": "kept: the importer work, the failing pytest -k csv"},
                "eng-001", instance) == (0, "")

    items = [i for i in queue_items(instance) if i["metadata"]["kind"] == "compact"]
    assert len(items) == 1
    assert "kept: the importer work" in items[0]["document"]
    assert "stream the importer" in items[0]["document"], "the state goes in with the summary"


def test_postcompact_without_a_summary_queues_nothing(instance):
    from hx.hook_compact import post

    post({"hook_event_name": "PostCompact", "trigger": "auto"}, "eng-001", instance)
    assert queue_items(instance) == []


def test_complete_enqueues_the_digest_with_its_outcome(instance, hx, launched, goals):
    launched("eng-001")
    dispatch_working(instance, hx, goals)
    (instance / "state" / "eng-001").mkdir(parents=True, exist_ok=True)
    (instance / "state" / "eng-001" / "eng-001-main.json").write_text(
        json.dumps(state_with("stream the importer"))
    )

    result = hx("complete", "done", harness_id="eng-001")
    assert result.returncode == 0, result.stdout + result.stderr

    items = [i for i in queue_items(instance) if i["metadata"]["kind"] == "complete"]
    assert len(items) == 1
    assert items[0]["metadata"]["outcome"] == "done"
    assert items[0]["episode_id"] == "eng-001/eng-001-main/complete/0"
    assert "stream the importer" in items[0]["document"], "the Digest text"


# --- the lock ----------------------------------------------------------------------------------


def test_the_lock_is_exclusive(instance):
    from hx import memory

    with memory.locked(instance):
        handle = os.open(memory.lock_path(instance), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(handle)

    # And it is released afterwards, or the next agent's search would block forever.
    handle = os.open(memory.lock_path(instance), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


@needs_chroma
def test_every_chroma_open_happens_under_the_lock(instance, monkeypatch):
    """Several agents' processes share one PersistentClient dir; chroma is not safe without it."""
    from hx import memory

    held: list[bool] = []
    real = memory._collection

    def checked(root):
        handle = os.open(memory.lock_path(root), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
                held.append(False)
            except BlockingIOError:
                held.append(True)
        finally:
            os.close(handle)
        return real(root)

    monkeypatch.setattr(memory, "_collection", checked)
    seed(instance, "eng-001", "eng-001-main", "seam", state_with("stream the importer"))
    memory.index(instance)
    memory.search(instance, "importer", k=1)
    memory.stats(instance)
    assert held and all(held), held


# --- indexing ------------------------------------------------------------------------------------


@needs_chroma
def test_index_drains_the_queue_and_is_idempotent(instance):
    from hx import memory

    seed(instance, "eng-001", "eng-001-main", "seam", state_with("stream the importer"))
    seed(instance, "eng-002", "eng-002-main", "pass", state_with("cut import memory"))

    assert memory.index(instance) == (2, 2)
    assert memory.queued(instance) == []
    assert memory.index(instance) == (0, 2)

    # The same episode id upserts rather than duplicating: re-running a boundary is free.
    seed(instance, "eng-001", "eng-001-main", "seam", state_with("stream the importer, again"))
    assert memory.index(instance) == (1, 2)


@needs_chroma
def test_an_unreadable_queue_file_is_dropped_not_retried_forever(instance):
    from hx import memory

    (memory.queue_dir(instance)).mkdir(parents=True, exist_ok=True)
    (memory.queue_dir(instance) / "broken.json").write_text("{not json")
    seed(instance, "eng-001", "eng-001-main", "seam", state_with("stream the importer"))

    assert memory.index(instance) == (1, 1)
    assert memory.queued(instance) == []


# --- search ---------------------------------------------------------------------------------------


@needs_chroma
def test_search_filters_by_role(instance):
    from hx import memory

    seed(instance, "eng-001", "eng-001-main", "seam",
         state_with("make the CSV importer stream instead of loading the whole file"))
    seed(instance, "fe-001", "fe-001-main", "complete",
         "dark mode for the upload page: tokens moved to :root", role="frontend-engineer")

    backend = memory.search(instance, "CSV importer memory", role="engineer", k=5)
    assert [hit["item"] for hit in backend] == ["eng-001"]

    everything = memory.search(instance, "CSV importer memory", k=5)
    assert {hit["item"] for hit in everything} == {"eng-001", "fe-001"}


@needs_chroma
def test_search_prefers_recency_between_two_similar_episodes(instance):
    """Same words, different ages: the older one loses. `score = similarity * recency_weight`."""
    from hx import memory

    text = "the nightly CSV import runs out of memory on the 400MB feed"
    seed(instance, "eng-001", "eng-001-main", "seam", {"seq": 1, "goal": text}, age_h=0.0)
    seed(instance, "eng-002", "eng-002-main", "seam", {"seq": 1, "goal": text}, age_h=24 * 30)

    hits = memory.search(instance, "CSV import out of memory", k=2)
    assert [hit["item"] for hit in hits] == ["eng-001", "eng-002"]
    assert hits[0]["score"] > hits[1]["score"]
    # Similarity alone does not separate them; recency does.
    assert abs(hits[0]["similarity"] - hits[1]["similarity"]) < 1e-6
    assert hits[0]["similarity"] == pytest.approx(hits[0]["score"], rel=0.05)


def test_the_recency_weight_halves_at_most():
    from hx.memory import recency_weight

    assert recency_weight(0.0) == pytest.approx(1.0)
    assert recency_weight(24.0) == pytest.approx(0.75)
    assert recency_weight(24.0 * 365) == pytest.approx(0.5, abs=1e-6)
    assert recency_weight(10.0, half_life_h=0) == 1.0


@needs_chroma
def test_search_excludes_one_id_and_narrows_by_kind(instance):
    from hx import memory

    seed(instance, "eng-001", "eng-001-main", "seam", state_with("stream the importer"))
    seed(instance, "eng-002", "eng-002-main", "pass", state_with("stream the importer too"))

    assert [h["item"] for h in memory.search(instance, "importer", k=5, exclude_id="eng-001")] == [
        "eng-002"
    ]
    assert [h["kind"] for h in memory.search(instance, "importer", k=5, kind="seam")] == ["seam"]


@needs_chroma
def test_search_returns_the_documented_json_shape(instance):
    from hx import memory

    seed(instance, "eng-001", "eng-001-main", "seam", state_with("stream the importer"))
    hit = memory.search(instance, "importer", k=1)[0]
    assert set(hit) == {
        "id", "ts", "t", "role", "pod", "item", "stream", "kind", "seq", "outcome",
        "score", "similarity", "document",
    }
    assert hit["id"] == "eng-001/eng-001-main/seam/7"


def test_search_on_an_instance_with_no_episodes_opens_nothing(instance, monkeypatch):
    """`hx compose` runs at every boundary; an empty store must not cost a chroma open."""
    from hx import memory

    def refuse(root):
        raise AssertionError("chroma was opened for an empty store")

    monkeypatch.setattr(memory, "_collection", refuse)
    assert memory.search(instance, "anything", k=5) == []


@needs_chroma
def test_list_and_stats(instance):
    from hx import memory

    seed(instance, "eng-001", "eng-001-main", "seam", state_with("stream the importer"), age_h=1)
    seed(instance, "fe-001", "fe-001-main", "complete", "dark mode", role="frontend-engineer",
         outcome="done", age_h=5)

    rows = memory.episodes(instance, limit=10)
    assert [row["item"] for row in rows] == ["eng-001", "fe-001"], "newest first"
    assert rows[1]["outcome"] == "done"
    assert [row["item"] for row in memory.episodes(instance, role="frontend-engineer")] == ["fe-001"]

    summary = memory.stats(instance)
    assert summary["total"] == 2 and summary["queued"] == 0
    assert summary["by_kind"] == {"complete": 1, "seam": 1}
    assert summary["by_role"] == {"engineer": 1, "frontend-engineer": 1}


# --- chromadb missing ------------------------------------------------------------------------------


def unavailable(monkeypatch):
    from hx import memory

    def raise_it(root):
        raise memory.MemoryUnavailable("chromadb is not importable (no module named chromadb)")

    monkeypatch.setattr(memory, "_collection", raise_it)


def test_search_without_chromadb_prints_one_line_and_exits_2(instance, monkeypatch, capsys):
    from hx import memory

    seed(instance, "eng-001", "eng-001-main", "seam", state_with("stream the importer"))
    unavailable(monkeypatch)

    assert memory.main(["search", "importer"], instance, env={}) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == (
        "hx: memory: chromadb is not importable (no module named chromadb)"
    )


def test_compose_without_chromadb_says_so_in_one_line(instance, monkeypatch):
    from hx import compose as compose_mod

    seed(instance, "eng-002", "eng-002-main", "seam", state_with("stream the importer"))
    (instance / "state" / "eng-001").mkdir(parents=True, exist_ok=True)
    (instance / "state" / "eng-001" / "eng-001-main.json").write_text(
        json.dumps(state_with("stream the importer"))
    )
    unavailable(monkeypatch)

    text = compose_mod.compose_text(instance, "eng-001", "eng-001-main")
    assert "## Memory episodes" in text
    assert "_memory unavailable: chromadb is not importable" in text
    assert "## Step state" in text and "## Open subagent handles" in text


# --- the CLI ---------------------------------------------------------------------------------------


@needs_chroma
def test_the_cli_prints_the_documented_lines(instance, capsys):
    from hx import memory

    seed(instance, "eng-001", "eng-001-main", "seam",
         state_with("make the CSV importer stream"))

    assert memory.main(["index"], instance, env={}) == 0
    assert capsys.readouterr().out.strip() == "HX-MEMORY indexed=1 total=1"

    assert memory.main(["search", "CSV importer", "--all-roles"], instance, env={}) == 0
    out = capsys.readouterr().out
    assert out.startswith("## ") and " eng-001 engineer seam seq=7 score=0." in out

    assert memory.main(["search", "CSV importer", "--all-roles", "--json"], instance, env={}) == 0
    assert json.loads(capsys.readouterr().out)[0]["item"] == "eng-001"

    assert memory.main(["stats"], instance, env={}) == 0
    assert "HX-MEMORY total=1 queued=0 path=" in capsys.readouterr().out


@needs_chroma
def test_the_default_role_filter_is_the_callers_own(instance, capsys):
    from hx import memory

    seed(instance, "fe-001", "fe-001-main", "complete", "dark mode for the upload page",
         role="frontend-engineer")

    assert memory.main(["search", "dark mode"], instance, env={"HX_ROLE": "engineer"}) == 0
    assert capsys.readouterr().out.strip() == "HX-MEMORY no episodes matched"

    assert memory.main(["search", "dark mode", "--all-roles"], instance,
                       env={"HX_ROLE": "engineer"}) == 0
    assert "fe-001" in capsys.readouterr().out

    # With no HX_ROLE the caller's id still names a role, via config/<id>/harness.json.
    assert memory.main(["search", "dark mode"], instance, env={"HARNESS_ID": "eng-001"}) == 0
    assert capsys.readouterr().out.strip() == "HX-MEMORY no episodes matched"


def test_a_human_shell_with_no_harness_env_filters_nothing(instance):
    from hx.memory import caller_role

    assert caller_role(instance, {}) is None
    assert caller_role(instance, {"HX_ROLE": "release-engineer"}) == "release-engineer"
    assert caller_role(instance, {"HARNESS_ID": "eng-001"}) == "engineer"


# --- compose injection --------------------------------------------------------------------------


@needs_chroma
def test_the_memory_section_carries_other_agents_episodes_and_not_its_own(instance, agent):
    from hx import compose as compose_mod

    agent("eng-002")
    seed(instance, "eng-002", "eng-002-main", "seam",
         state_with("make the CSV importer stream instead of loading the whole file"))
    seed(instance, "eng-001", "eng-001-main", "seam",
         state_with("make the CSV importer stream, my own state"))

    (instance / "state" / "eng-001").mkdir(parents=True, exist_ok=True)
    (instance / "state" / "eng-001" / "eng-001-main.json").write_text(
        json.dumps(state_with("make the CSV importer stream, my own state"))
    )

    text = compose_mod.compose_text(instance, "eng-001", "eng-001-main")
    section = text[text.index("## Memory episodes"):text.index("## Open subagent handles")]
    assert "_source: `state/memory/chroma`_" in section
    assert 'Search more: `hx memory search "' in section and "--all-roles`" in section
    assert "eng-002/seam" in section
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert bullets and all("my own state" not in line for line in bullets), (
        "its own step state is the section above; only the query line repeats it"
    )
    assert text.index("## Step state") < text.index("## Memory episodes")
    assert text.index("## Memory episodes") < text.index("## Open subagent handles")


@needs_chroma
def test_an_injected_episode_is_one_truncated_line(instance, agent):
    from hx import memory

    agent("eng-002")
    (instance / "config" / "eng-001" / "harness.json").write_text(json.dumps({
        "id": "eng-001", "pod": "engineers", "role": "engineer", "model": "claude-opus-5",
        "effort": "xhigh", "workdir": "wt/eng-001",
        "companion": {"memory_episode_chars": 60},
    }))
    seed(instance, "eng-002", "eng-002-main", "seam",
         state_with("make the CSV importer stream instead of loading the whole file"))

    body = memory.section_text(instance, "eng-001", "eng-001-main", state_with("CSV importer"))
    lines = [line for line in body.splitlines() if line.startswith("- ")]
    assert lines, body
    for line in lines:
        assert "\n" not in line and len(line) < 160
        assert line.endswith("…"), "truncated to memory_episode_chars"


def test_memory_inject_k_zero_drops_the_section_entirely(instance):
    from hx import compose as compose_mod

    (instance / "config" / "eng-001" / "harness.json").write_text(json.dumps({
        "id": "eng-001", "pod": "engineers", "role": "engineer", "model": "claude-opus-5",
        "effort": "xhigh", "workdir": "wt/eng-001", "companion": {"memory_inject_k": 0},
    }))
    text = compose_mod.compose_text(instance, "eng-001", "eng-001-main")
    assert "## Memory episodes" not in text
    assert "## Step state" in text


def test_the_inject_query_is_the_goal_the_next_step_and_the_hypothesis():
    from hx.memory import inject_query

    query = inject_query(state_with("stream the importer"))
    assert "stream the importer" in query
    assert "port the transform" in query and "run pytest -k csv" in query
    assert "the parse dominates" in query
    assert inject_query({}, "the order text") == "the order text"


def test_the_new_companion_config_fields_validate(instance):
    from hx.config_harness import validate_harness
    from hx.errors import ValidationError

    base = {
        "id": "eng-001", "pod": "engineers", "role": "engineer",
        "model": "claude-opus-5", "effort": "xhigh", "workdir": "wt/eng-001",
    }
    good = validate_harness(
        dict(base, companion={
            "memory_inject_k": 0, "memory_episode_chars": 400, "memory_half_life_h": 72,
        }),
        "harness.json",
    )
    assert good.companion["memory_inject_k"] == 0

    for companion in (
        {"memory_inject_k": -1},
        {"memory_episode_chars": 0},
        {"memory_half_life_h": 0},
        {"memory_inject_k": "five"},
    ):
        with pytest.raises(ValidationError):
            validate_harness(dict(base, companion=companion), "harness.json")
