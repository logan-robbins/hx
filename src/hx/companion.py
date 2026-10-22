"""The Partner's Companion pass, and native ledger companion command routing.

A Partner pass is one Messages API exchange (`companion_api.exchange`): a
byte-stable system block (`companion/BASE.md`, the role file and this agent's
facts), the stream's prior step state and its new records in the user turn, and
one tool whose input is the new state. hx validates it (`stepstate.validate`),
evicts deterministically under budget (`stepstate.evict`), stamps the cursor and
installs it. The application loop runs due passes in its Companion slots;
`hx flush partner` runs them inline. Worker observations and semantic patches use
native companion jobs tied to their admitted run and frozen event range.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import companion_api, store, streams, timestamps
from .config_harness import HarnessConfig, load_harness
from .continuity_store import canonical, digest
from .errors import HxError, NotFound, Refused
from .ids import PARTNER
from .stepstate import InvalidState, empty, evict, load as load_state, validate

#: The whole first request: system, prior state, new records and the tool.
REQUEST_BYTES = 65536
#: A rejected state is retried once, inside the same exchange.
MAX_ATTEMPTS = 2

_STRINGS = {"type": "array", "items": {"type": "string"}}
_SEQS = {"type": "array", "items": {"type": "integer"}}
STATE_SCHEMA = {"type": "object", "properties": {
    "goal": {"type": "string"}, "constraints": _STRINGS,
    "decisions": {"type": "array", "items": {"type": "object", "properties": {
        "d": {"type": "string"}, "why": {"type": "string"}, "ev": _SEQS}, "required": ["d", "why", "ev"]}},
    "open_steps": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "intent": {"type": "string"}, "next": {"type": "string"}, "ev": _SEQS},
        "required": ["id", "intent", "next", "ev"]}},
    "closed_steps": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "outcome": {"type": "string"}, "verified": {"type": "boolean"},
        "commit": {"type": "string"}, "ev": _SEQS}, "required": ["id", "outcome", "verified", "commit", "ev"]}},
    "dead_ends": _STRINGS,
    "working_set": {"type": "object", "properties": {
        "commits": {"type": "array", "items": {"type": "object", "properties": {
            "sha": {"type": "string"}, "msg": {"type": "string"}}}},
        "dirty": _STRINGS,
        "files": {"type": "array", "items": {"type": "object", "properties": {
            "path": {"type": "string"}, "note": {"type": "string"}}, "required": ["path", "note"]}},
        "last_failure": {"type": "string"}, "hypothesis": {"type": "string"}}},
    "blockers": _STRINGS, "subagents_open": _STRINGS},
    "required": ["goal", "constraints", "decisions", "open_steps", "closed_steps", "dead_ends",
                 "working_set", "blockers", "subagents_open"]}
TOOLS = [{"name": "submit_state",
          "description": "Submit the new step state for this stream. hx validates it, stamps seq and "
                         "prompt_version, and installs it; a rejected state may be resubmitted once.",
          "input_schema": {"type": "object", "properties": {
              "state": STATE_SCHEMA,
              "digest": {"type": "string", "description": "Closed subagent streams only: what the subagent "
                         "did, what it committed, and what it left open or unproven."}},
              "required": ["state"], "additionalProperties": False}}]


def require_partner(item_id: str) -> None:
    """File passes maintain only the Partner's conversation state."""
    if item_id != PARTNER:
        raise Refused('worker companions require a ledger run and native companion job')


def state_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "state" / item_id / f"{stream}.json"


def digest_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "state" / item_id / f"{stream}.digest.md"


def usage_path(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / "companion-usage.jsonl"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else ""


def prompt_version(root: Path, role: str) -> dict:
    """The shas of `BASE.md` and the role file — what `hx metrics` compares across."""
    return {
        "base": _sha(root / "companion" / "BASE.md"),
        "role": _sha(root / "companion" / "roles" / f"{role}.md"),
    }


def config_for(root: Path, item_id: str) -> HarnessConfig:
    harness = root / "config" / item_id / "harness.json"
    if not harness.is_file():
        raise NotFound(f"{harness}: no config for {item_id}")
    return load_harness(harness, check_cross_file=False)


def is_disabled(root: Path, item_id: str) -> bool:
    """True when this agent runs without a Companion (`companion.disabled`).

    Never raises: an unreadable config means the caller is already failing
    elsewhere, and a disabled check must not be what takes it down.
    """
    try:
        return bool((config_for(root, item_id).companion or {}).get("disabled", False))
    except Exception:
        return False


def system_prompt(root: Path, config: HarnessConfig) -> str:
    """`BASE.md` + the role file + the agent's facts: identical on every pass, so it caches."""
    require_partner(config.id)
    base = root / "companion" / "BASE.md"
    role = root / "companion" / "roles" / f"{config.role}.md"
    for path in (base, role):
        if not path.is_file():
            raise NotFound(f"{path}: missing; the Companion's system prompt is composed from it")
    budget = (config.companion or {}).get("state_budget_tokens", 10000)
    facts = "\n".join([
        "## This agent",
        "",
        f"- id: `{config.id}`",
        f"- pod: `{config.pod}`",
        f"- role: `{config.role}`",
        f"- model the agent runs on: `{config.model}`, effort `{config.effort}`",
        f"- state budget: {budget} tokens (about {budget * 4} characters of JSON)",
        "",
    ])
    return f"{base.read_text()}\n\n{role.read_text()}\n\n{facts}"


# --- streams and cursors ----------------------------------------------------------------------


def open_streams(root: Path, item_id: str) -> list[str]:
    """Every stream this agent owns: main, plus one per subagent, open or closed."""
    found = [f"{item_id}-main"]
    for path in streams.subagent_streams(root, item_id):
        found.append(path.name.rsplit("-", 1)[0])
    return found


def head_seq(root: Path, item_id: str, stream: str) -> int:
    return streams.last_seq(streams.stream_path(root, item_id, stream))


def cursor(root: Path, item_id: str, stream: str) -> int:
    state = load_state(state_path(root, item_id, stream)) or empty()
    return int(state.get("seq") or 0)


def is_caught_up(root: Path, item_id: str) -> bool:
    """True when every stream's state `seq` equals its log head — what `hx flush` reports.

    Vacuously true with no Companion: there is no cursor that could be behind.
    """
    if is_disabled(root, item_id):
        return True
    return all(
        cursor(root, item_id, stream) >= head_seq(root, item_id, stream)
        for stream in open_streams(root, item_id)
    )


def open_subagent_handles(root: Path, item_id: str) -> list[str]:
    return [
        path.name.rsplit("-", 1)[0].removeprefix(f"{item_id}-")
        for path in streams.subagent_streams(root, item_id, state="open")
    ]


def _closed(root: Path, item_id: str, stream: str) -> bool:
    return stream != f"{item_id}-main" and streams.stream_path(root, item_id, stream).name.endswith("-closed.jsonl")


def due_streams(root: Path, item_id: str, *, force: bool = False) -> list[str]:
    """Streams with `batch_records` new records; any new record when forced or the stream closed."""
    require_partner(item_id)
    if is_disabled(root, item_id):
        return []
    try:
        batch = 1 if force else (config_for(root, item_id).companion or {}).get("batch_records", 20)
    except NotFound:
        return []
    return [stream for stream in open_streams(root, item_id)
            if head_seq(root, item_id, stream) - cursor(root, item_id, stream) >= (1 if _closed(root, item_id, stream) else batch)]


# --- one pass ----------------------------------------------------------------------------------


def _records(root: Path, item_id: str, stream: str, after: int, budget: int) -> list[dict]:
    """Whole records after the cursor, oldest first, within `budget` bytes; never a partial one."""
    taken, used = [], 0
    for record in streams.iter_records(streams.stream_path(root, item_id, stream)):
        seq = record.get("seq")
        if type(seq) is not int or seq <= after:
            continue
        size = len(canonical(record).encode()) + 1
        if taken and used + size > budget:
            break
        taken.append(record)
        used += size
    return taken


def user_turn(root: Path, item_id: str, stream: str, previous: dict | None, records: list[dict]) -> str:
    kind = "main" if stream == f"{item_id}-main" else "subagent, closed: also submit its digest" \
        if _closed(root, item_id, stream) else "subagent, open"
    lines = [
        "# Companion pass",
        f"stream: {stream} ({kind})",
        f"from_seq: {previous.get('seq', 0) if previous else 0}",
        f"through_seq: {records[-1]['seq']}",
        f"open_subagents: {','.join(open_subagent_handles(root, item_id)) if kind == 'main' else ''}",
        "",
        "## Current step state",
        canonical({key: value for key, value in previous.items() if key not in ("seq", "prompt_version", "ts")})
        if previous else "none: this is the first pass for this stream",
        "",
        "## New records",
        *(canonical(record) for record in records),
    ]
    return "\n".join(lines) + "\n"


def run_pass(root: Path, item_id: str, stream: str, *, env=None, timeout_s=60) -> dict:
    """One exchange for one stream: install the validated state, or keep the prior one."""
    require_partner(item_id)
    root = Path(root)
    config = config_for(root, item_id)
    if is_disabled(root, item_id):
        return {"status": "idle", "purpose": "partner", "stream": stream}
    payload = companion_api.runtime(root, config, purpose="partner")
    system = system_prompt(root, config)
    previous = load_state(state_path(root, item_id, stream))
    after = int((previous or {}).get("seq") or 0)
    fixed = len(system.encode()) + len(canonical(TOOLS).encode()) + len(canonical(previous or {}).encode()) + 1024
    records = _records(root, item_id, stream, after, max(1, REQUEST_BYTES - fixed))
    if not records:
        return {"status": "idle", "purpose": "partner", "stream": stream}
    user = user_turn(root, item_id, stream, previous, records)
    payload["request_bytes"] = REQUEST_BYTES
    transport = companion_api.preflight(root, payload, system=system, user=user, tools=TOOLS, timeout_s=timeout_s)
    through = records[-1]["seq"]
    budget = (config.companion or {}).get("state_budget_tokens", 10000)
    outcome = {"attempts": 0, "installed": None, "error": None}

    def handle(name, arguments):
        if name != "submit_state":
            return "unknown tool; use submit_state", True, False
        outcome["attempts"] += 1
        try:
            if not isinstance(arguments, dict) or not isinstance(arguments.get("state"), dict):
                raise InvalidState("submit_state needs a `state` object")
            note = arguments.get("digest")
            if note is not None and not isinstance(note, str):
                raise InvalidState("`digest` must be a string")
            if _closed(root, item_id, stream) and not (note or "").strip():
                raise InvalidState("a closed subagent stream needs its `digest`")
            candidate = validate({**arguments["state"], "seq": through}, previous_seq=after)
        except InvalidState as exc:
            outcome["error"] = str(exc)
            return str(exc), True, outcome["attempts"] >= MAX_ATTEMPTS
        candidate, evicted = evict(candidate, budget)
        if evicted:
            log_problem(root, item_id, f"{stream}: over budget, evicted {', '.join(evicted)}")
        # hx owns the cursor and the stamp, not the model.
        candidate.update(seq=through, prompt_version=prompt_version(root, config.role), ts=timestamps.now())
        store.atomic_write_json(state_path(root, item_id, stream), candidate)
        if note and _closed(root, item_id, stream):
            store.atomic_write_text(digest_path(root, item_id, stream), note.strip() + "\n")
        outcome["installed"] = candidate
        return "installed", False, True

    usage = []
    execution = companion_api.exchange(payload, credential=transport["credential"], system=system, user=user,
                                       tools=TOOLS, handle=handle, record=usage.append, timeout_s=timeout_s, env=env)
    usage_path(root, item_id).parent.mkdir(parents=True, exist_ok=True)
    with usage_path(root, item_id).open("a") as log:
        log.write(canonical({"ts": timestamps.now(), "stream": stream, "from_seq": after, "through_seq": through,
                             "outcome": execution["outcome"], "usage": usage}) + "\n")
    if outcome["installed"] is None:
        error = outcome["error"] or execution.get("error") or execution["outcome"]
        log_problem(root, item_id, f"{stream}: pass {after}..{through} not installed ({error}); the prior state stands")
        return {"status": "unresolved", "purpose": "partner", "stream": stream, "error": error, "execution": execution}
    return {"status": "committed", "purpose": "partner", "stream": stream, "seq": through, "execution": execution}


def run_due(root: Path, item_id: str, *, force: bool = False, env=None) -> list[dict]:
    """Every due stream, one pass each. `hx flush` calls this; the loop schedules its own."""
    return [run_pass(root, item_id, stream, env=env) for stream in due_streams(root, item_id, force=force)]


def select(ledger, *, exclude=frozenset()):
    """The loop's next Partner stream due a pass. A failed window waits for new records."""
    from .application_loop import _state
    if not (ledger.root / "config" / PARTNER / "harness.json").is_file():
        return None
    for stream in due_streams(ledger.root, PARTNER):
        if (PARTNER, stream) in exclude:
            continue
        window = digest([stream, cursor(ledger.root, PARTNER, stream), head_seq(ledger.root, PARTNER, stream)])
        if _state(ledger, "partner-pass:" + stream).get("window") == window:
            continue
        return {"agent_id": PARTNER, "key": stream, "stream_id": stream, "window": window}
    return None


def assess(ledger, candidate, *, env=None, own=None):
    """Run one selected Partner pass in a loop slot; its failure sticks to its window."""
    from .application_loop import _save
    try:
        result = run_pass(ledger.root, PARTNER, candidate["stream_id"], env=env)
    except (HxError, OSError) as exc:
        result = {"status": "unresolved", "purpose": "partner", "stream": candidate["stream_id"], "error": str(exc)}
    if result["status"] == "unresolved":
        _save(ledger, "partner-pass:" + candidate["stream_id"], {"window": candidate["window"], "error": result["error"]})
    return result


def reconcile(ledger, *, skip=lambda job_id: False):
    """Partner passes keep no durable running rows: nothing outlives its process."""
    return []


def log_problem(root: Path, item_id: str, message: str) -> None:
    from .hooks import log_error

    log_error(root, item_id, "companion", message)


def last_seam_ts(root: Path, item_id: str) -> float | None:
    newest = None
    for record in streams.iter_records(streams.main_stream(root, item_id)):
        if record.get("event") == "seam":
            parsed = timestamps.parse(str(record.get("ts", "")))
            if parsed is not None:
                newest = parsed.timestamp()
    return newest


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx companion", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--pass", dest="stream_pass", metavar="STREAM", default=None,
                        help="run one Partner pass for one stream now")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.add_argument("--run", help="prepare and execute a planned ledger companion pass")
    parser.add_argument("--stream", default="main")
    parser.add_argument("--request", help="stable idempotency identity for this pass")
    parser.add_argument("--record", action="append", default=[])
    parser.add_argument("--map-snapshot", help="worktree map snapshot for this pass")
    parser.add_argument("--map-record", action="append", default=[], help="selected map ID (at most eight)")
    parser.add_argument("--job", help="inspect a previously prepared native companion job")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--learning", action="store_true",
                        help="use the independent learning-assessment Companion")
    args = parser.parse_args(argv)

    if args.learning:
        from .continuity_store import ContinuityStore
        from . import learning_companion
        if args.stream_pass or args.record or args.map_snapshot or args.map_record or (args.job and args.run):
            parser.error("learning assessment cannot use context Companion options or combine job and run")
        with ContinuityStore(root) as ledger:
            if args.job:
                owner = ledger.db.execute('SELECT agent_id FROM learning_jobs WHERE job_id=?', (args.job,)).fetchone()
                if owner is None:
                    raise NotFound('unknown learning assessment job')
                if owner[0] != args.id:
                    raise Refused('learning assessment job belongs to another agent')
                result = learning_companion.status(ledger, args.job)
            else:
                if not args.request:
                    parser.error("learning assessment requires --request")
                stream = 'partner-main' if args.id == PARTNER and args.stream == 'main' else args.stream
                job = learning_companion.prepare(ledger, args.id, stream, args.request, run_id=args.run)
                result = ({"status": "no_work"} if job is None else learning_companion.status(ledger, job['job_id'])
                          if args.prepare_only else learning_companion.execute(ledger, job['job_id'], env=env))
            print(canonical(result))
        return 0

    if args.run or args.job:
        from .continuity_store import ContinuityStore
        from . import native_companion
        if args.stream_pass or (args.job and args.run):
            parser.error("native companion requests cannot use a Partner pass or combine job and run")
        with ContinuityStore(root) as ledger:
            if args.job:
                from .companion_protocol import row
                if row(ledger, args.job)["worker_id"] != args.id:
                    parser.error("job belongs to a different executor")
                result = native_companion.status(ledger, args.job)
            else:
                if not args.request:
                    parser.error("planned companion requires --request")
                from . import unit_execution
                with ledger.transaction() as tx:
                    run, _, _ = unit_execution._run(tx, args.run)
                    if run['worker_id'] != args.id:
                        raise Refused('native companion run belongs to another worker')
                job = native_companion.prepare(ledger, args.id, args.run, args.stream, args.request, record_ids=args.record,
                    map_snapshot=args.map_snapshot, map_record_ids=args.map_record, env=env)
                result = ({"status": "no_work"} if job is None else native_companion.status(ledger, job["job_id"])
                          if args.prepare_only else native_companion.execute(ledger, job["job_id"], env=env))
            print(canonical(result))
        return 0
    if args.request or args.record or args.prepare_only or args.map_snapshot or args.map_record:
        parser.error("planned companion options require --run")

    if args.id != PARTNER:
        parser.error("worker companions require a ledger run or job")
    results = [run_pass(root, args.id, args.stream_pass, env=env)] if args.stream_pass else run_due(root, args.id, force=True, env=env)
    print(canonical(results))
    return 0
