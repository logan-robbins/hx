"""Route Partner observations and admitted native worker hooks.

Partner observation failures are logged; missing context/source delivery also
invalidates admission for the next operation. Its configured tool guard may
explicitly deny an operation. Worker startup,
request and tool admission errors deny execution; observation failures retain a
capture gap instead of inventing successful evidence. A planned hook attaches to
the ledger without its writer lock and reads through snapshots. When the writer
is unavailable within `LEDGER_BUDGET_S`, the hook passes the operation through
and defers its whole delivery to the application loop, which replays it in
order; contention never denies. Native host crashes and hook timeouts still
require process reconciliation before ownership is released.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from . import hook_compact, hook_context, hook_guard, hook_log, hook_stop, hook_subagent
from .errors import HxError
from .ids import is_id
from .root import resolve_root

#: The hx event vocabulary of spec 09.1, and the build goal that delivers each
#: (`handoff/orchestrator-to-build.md`, 2026-09-20 renumbering).
EVENTS = {
    "context": 3,
    "log": 5,
    "subagent-start": 5,
    "subagent-stop": 5,
    "subagent-result": 5,
    "precompact": 5,
    "postcompact": 5,
    # The turn marker ships with the M4 batch (spec 13).
    "stop": 5,
    # The Companion's own `Stop`, in its own home: it installs what the pass produced.
    "companion-stop": 6,
    # The Partner's `PreToolUse` guard over `config/partner/guard.json`. It shipped with
    # goal eng-008, not a build-lane goal; the number only feeds the never-shown
    # "not implemented" message.
    "guard": 9,
    "request": 9,
    "log-failure": 9,
    "tool-start": 9,
    "message": 9,
    "stop-failure": 9,
    "stop-cancelled": 9,
    "session-end": 9,
    "model-response": 9,
}

IMPLEMENTED = (
    "context", "log", "subagent-start", "subagent-stop", "subagent-result", "stop",
    "precompact", "postcompact", "companion-stop", "guard", "request", "log-failure", "tool-start", "message",
    "stop-failure", "stop-cancelled", "session-end", "model-response",
)

#: Context and child boundaries emit complete automatically segmented native context.
#: Worker continuation is submitted as verified full native input by the controller.
_HANDLERS = {
    "context": hook_context.handle,
    "log": hook_log.handle,
    "subagent-start": hook_subagent.start,
    "subagent-stop": hook_subagent.stop,
    "subagent-result": hook_subagent.result,
    "stop": hook_stop.handle,
    # Log-only, and `precompact` never blocks (spec 02, 09.1; live findings E4 and E8).
    "precompact": hook_compact.pre,
    "postcompact": hook_compact.post,
    "companion-stop": hook_stop.companion_handle,
    # Exit 2 blocks the call; its line goes to stderr, which is what the model is shown.
    "guard": hook_guard.handle,
}

ERROR_LOG = "hook-errors.log"

#: Seconds one planned hook commit may wait for the ledger writer. Well inside
#: the native hosts' hook timeouts; past it, the hook defers to the loop.
LEDGER_BUDGET_S = 5.0


def unavailable(exc: BaseException) -> bool:
    """The ledger was contended or out of budget, directly or as a refusal's cause."""
    from .continuity_store import LedgerBusy, busy
    from .native_tools import AdmissionDenied
    if isinstance(exc, AdmissionDenied):
        exc = exc.__cause__
    return isinstance(exc, LedgerBusy) or (exc is not None and busy(exc))


def error_log_path(root: Path, item_id: str) -> Path:
    return root / "logs" / item_id / ERROR_LOG


def log_error(root: Path, item_id: str, event: str, message: str) -> None:
    """Record why a hook could not do its job. Never raises: this is the last line of defence."""
    try:
        from . import timestamps

        path = error_log_path(root, item_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(f"{timestamps.now()} {event}: {message}\n")
            handle.flush()
    except Exception:
        pass


def read_payload(stream=None) -> dict:
    """The hook payload from stdin. An empty or malformed body is an error the caller logs."""
    from .observer import MAX_RECORD_BYTES
    source = stream or sys.stdin
    raw = getattr(source, "buffer", source).read(MAX_RECORD_BYTES + 1)
    if (len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))) > MAX_RECORD_BYTES:
        raise ValueError("hook payload exceeds 8 MiB; original source requires reconciliation")
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"hook payload is {type(data).__name__}, not an object")
    return data


def check_id(item_id: str, env) -> None:
    """The baked-in `--id` must match `HARNESS_ID` when the session sets one (goal item 4).

    A mismatch means the settings file of one agent's home is being used by another.
    """
    env = os.environ if env is None else env
    running = env.get("HARNESS_ID")
    if running and running != item_id:
        raise HxError(f"--id {item_id} does not match HARNESS_ID {running}")


def planned(ledger, delivery, *, write, replay=False):
    """One planned hook's ledger work: admission, capture, sources, lifecycle, tokens.

    `write` carries output to the native host. The loop replays a deferred
    delivery with `replay`: its tool call already executed, so it is recorded
    rather than admitted, and a complete-file read it never delivered stays
    incomplete.
    """
    run_id, launch_id, adapter, item_id = (delivery[key] for key in ("run_id", "launch_id", "adapter", "worker_id"))
    event, payload, read_segment = delivery["event"], delivery["payload"], delivery["read_segment"]
    child = payload.get("agent_id") or payload.get("agentId") or payload.get("subagent_id")
    if replay and event in {'log', 'log-failure'} and read_segment:
        return 0  # Segment hooks deliver content only; the first one carries the observation.
    from .hook_contract import validate
    from .native_producer import hook
    validate(ledger, run_id=run_id, launch_id=launch_id, adapter=adapter, worker_id=item_id)
    from .native_launch import _row
    from . import token_controls
    launch = _row(ledger, run_id)
    if replay and event == "tool-start":
        # The deferred call already executed; record it so drain and settlement see it.
        from .native_tools import record_executed
        record_executed(ledger, run_id, launch_id, payload)
    elif not replay:
        if launch and event in {'request', 'tool-start'}:
            token_controls.guard(ledger, launch)
        if event == "tool-start":
            from .native_tools import admit
            admit(ledger, run_id, launch_id, payload, adapter=adapter)
    complete_read = None
    broker_error = None
    if launch and event in {'log', 'log-failure'}:
        from .file_reads import is_read, broker_result
        if is_read(payload) and replay:
            # A deferred hook delivered nothing; the read stays incomplete until repeated.
            broker_error = 'complete-file delivery was deferred under ledger contention and did not reach the agent'
            payload = {**payload, 'tool_response': {'error': broker_error},
                       'is_error': True, 'hook_event_name': 'PostToolUse'}
        elif is_read(payload):
            from .unit_execution import _run
            with ledger.snapshot() as tx:
                _, task, _ = _run(tx, run_id)
            from .file_read_delivery import SEGMENTED_ADAPTERS, emit, native_marker
            try:
                if adapter in SEGMENTED_ADAPTERS:
                    complete_read = emit(ledger, launch, task['payload'], payload,
                        read_segment or 0, write=write,
                        event='PostToolUseFailure' if event == 'log-failure' else 'PostToolUse')
                    if complete_read is None:
                        return 0
                else:
                    result = broker_result(ledger, launch, task['payload'], payload)
                    if adapter == 'claude' and result is not None:
                        # Claude's own Read result is the delivery; the hook adds only its marker.
                        result = {**result, 'marker': native_marker(result, payload)}
                    complete_read = result
            except (HxError, OSError) as exc:
                broker_error = str(exc)
                payload = {**payload, 'tool_response': {'error': broker_error},
                           'is_error': True, 'hook_event_name': 'PostToolUse'}
            # Capture exactly what the hook emits to the native host.
            # The native preview is never treated as the full file.
            if complete_read is not None:
                payload = {**payload, 'tool_response': complete_read['text'], 'is_error': False,
                           'hook_event_name': 'PostToolUse'}
    hook(ledger, run_id=run_id, worker_id=item_id,
         adapter=adapter, launch_id=launch_id,
         event='log' if complete_read is not None or broker_error else event, payload=payload,
         delivery_id=delivery["delivery_id"])
    from .native_sources import observe as observe_sources
    observe_sources(ledger, run_id=run_id, launch_id=launch_id,
                    worker_id=item_id, adapter=adapter, event=event, payload=payload)
    if event == 'precompact' and not child:
        from .native_launch import _row
        from .compaction_policy import prepare_native_compaction
        launch = _row(ledger, run_id)
        if launch and launch['status'] == 'submitted':
            write(prepare_native_compaction(ledger, launch, payload))
    if event in {"context", "request"}:
        from .native_controller import observe
        line = observe(ledger, run_id, launch_id, event, payload)
        if line:
            if event == 'context' and adapter in {'claude', 'codex', 'meta'}:
                write(json.dumps({'hookSpecificOutput': {
                    'hookEventName': 'SessionStart', 'additionalContext': line}}))
            elif adapter != 'grok':
                write(line)
    if launch:
        token_controls.observe(ledger, launch, event, payload)
    if complete_read is not None:
        if adapter == 'pi':
            write(json.dumps({'hx_complete_read': complete_read}))
        elif adapter == 'codex':
            write(json.dumps({'hookSpecificOutput': {
                'hookEventName': 'PostToolUse',
                'additionalContext': complete_read['text']}}))
        elif adapter == 'claude':
            write(json.dumps({'hookSpecificOutput': {
                'hookEventName': 'PostToolUseFailure' if event == 'log-failure' else 'PostToolUse',
                'additionalContext': complete_read['marker']}}))
    elif broker_error and not read_segment:
        write(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PostToolUseFailure' if event == 'log-failure' else 'PostToolUse',
            'additionalContext': 'Complete-file delivery failed: ' + broker_error
                + '. The file has not been delivered completely; retry the whole file before another operation.'}}))
    return 0


def replay(store, delivery):
    """Apply a deferred planned hook inside the loop; its output reaches no one."""
    return planned(store, delivery, write=lambda value: None, replay=True)


def _defer(root, delivery, reason):
    from . import hook_spool
    hook_spool.record(root, delivery, reason)
    print(f"hx-hook: {delivery['event']}: {reason}; passed through and deferred to the application loop", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None, *, stdin=None, env=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    env = os.environ if env is None else env

    parser = argparse.ArgumentParser(prog="hx-hook", add_help=True)
    parser.add_argument("--id", required=True, dest="item_id", help="the id, baked in by install.sh")
    parser.add_argument("event", choices=sorted(EVENTS), help="the hx hook event (spec 09.1)")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.add_argument('--read-segment', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--context-segment', type=int, help=argparse.SUPPRESS)
    for field in ("run", "launch", "adapter"):
        parser.add_argument("--continuity-" + field, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    explicit = [getattr(args, "continuity_" + field) for field in ("run", "launch", "adapter")]
    if any(explicit):
        if not all(explicit):
            parser.error("planned hooks require run, launch, and adapter together")
        env = {**env, **{"HX_CONTINUITY_" + field.upper(): value
                        for field, value in zip(("run", "launch", "adapter"), explicit)}}

    event, item_id = args.event, args.item_id
    root = None

    try:
        # The root first, so everything after it has somewhere to log to, and so the standing
        # `~/.claude` refusal applies before the hook touches anything.
        root = resolve_root(args.root, env)
        if not is_id(item_id):
            raise HxError(f"--id {item_id} is not an id (`partner` or `<pod>-NNN`)")
        check_id(item_id, env)
        payload = read_payload(stdin)
        if args.read_segment is not None:
            from .file_reads import is_read
            if not is_read(payload):
                return 0
        if item_id == 'partner' and event in {'context', 'subagent-start', 'subagent-result'}:
            payload = {**payload, '_hx_context_segment': args.context_segment or 0}

        if env.get("HX_CONTINUITY_RUN") and event != "guard":
            launch_id = env.get("HX_CONTINUITY_LAUNCH")
            adapter = env.get("HX_CONTINUITY_ADAPTER")
            if not launch_id or not adapter:
                raise HxError("planned hooks require the original launch identity and adapter")
            if event == "companion-stop":
                raise HxError("native executor capture cannot run the Partner orchestration companion handler")
            child = payload.get("agent_id") or payload.get("agentId") or payload.get("subagent_id")
            if child and not (payload.get("session_id") or payload.get("sessionId") or payload.get("transcript_path")):
                from .continuity_store import digest
                payload = {**payload, "session_id": "launch:" + digest([launch_id, child])}
            import uuid
            delivery = {"run_id": env["HX_CONTINUITY_RUN"], "launch_id": launch_id, "adapter": adapter,
                        "worker_id": item_id, "event": event, "payload": payload, "read_segment": args.read_segment,
                        "delivery_id": str(uuid.uuid4())}
            from . import hook_spool
            if hook_spool.pending(root, delivery["run_id"]):
                # Keep this run's observations in order behind its deferred ones.
                return _defer(root, delivery, "an earlier observation of this run is deferred")
            from .continuity_store import ContinuityStore
            try:
                with ContinuityStore.attach(root, budget=LEDGER_BUDGET_S) as ledger:
                    return planned(ledger, delivery, write=lambda value: print(value, flush=True))
            except BaseException as exc:
                if unavailable(exc):
                    return _defer(root, delivery, "ledger unavailable within the hook budget")
                raise

        if item_id != "partner":
            raise HxError("worker hooks require an admitted run and native launch identity")
        if event == 'guard':
            from . import partner_context
            from . import partner_file_reads
            try:
                if partner_context.paths(root)[0].exists():
                    partner_context.require_delivered(root)
                partner_file_reads.require_delivered(root, payload)
                code, line = hook_guard.handle(payload, item_id, root, env=env)
                if code == 0:
                    partner_file_reads.admit(root, payload)
                if line:
                    print(line, file=sys.stderr if code == hook_guard.DENY else sys.stdout)
                return code
            except (HxError, OSError) as exc:
                from .native_tools import AdmissionDenied
                raise AdmissionDenied(str(exc)) from exc
        if event in {'log', 'log-failure'}:
            from .file_reads import is_read
            if is_read(payload):
                from . import partner_file_reads
                complete_read = partner_file_reads.emit(root, payload, args.read_segment or 0,
                    write=lambda value: print(value, flush=True),
                    event='PostToolUseFailure' if event == 'log-failure' else 'PostToolUse')
                if complete_read is None:
                    return 0
                payload = {**payload, 'tool_response': complete_read['text'], 'is_error': False}
                event = 'log'

        # Partner observations maintain its orchestration context.
        if event in {"request", "tool-start", "message", "stop-failure", "stop-cancelled", "session-end", "model-response"}:
            return 0
        if event == "log-failure":
            event = "log"
        if event in _HANDLERS:
            code, line = _HANDLERS[event](payload, item_id, root, env=env)
            if line:
                print(line, file=sys.stderr if code == hook_guard.DENY else sys.stdout)
            return code

        print(
            f"hx-hook: {event}: not implemented (build-{EVENTS[event]}) for --id {item_id}",
            file=sys.stderr,
        )
        return 0

    except SystemExit:
        raise
    except BaseException as exc:
        from .native_tools import AdmissionDenied
        if isinstance(exc, AdmissionDenied):
            print(f"hx-hook: {event}: {exc}", file=sys.stderr)
            return 2
        detail = f"{type(exc).__name__}: {exc}"
        if root is not None:
            log_error(root, item_id, event, detail + "\n" + traceback.format_exc())
            if env.get("HX_CONTINUITY_RUN"):
                try:
                    from .continuity_store import ContinuityStore
                    from .native_producer import gap
                    with ContinuityStore.attach(root, budget=LEDGER_BUDGET_S) as ledger:
                        gap(ledger, env["HX_CONTINUITY_RUN"], item_id, "planned hook observation failed: " + event)
                except Exception:
                    pass  # Storage failure is still reported to the native hook.
        print(f"hx-hook: {event}: {detail}", file=sys.stderr)
        return 2 if (event in {"request", "tool-start", "context"} and item_id != "partner"
                     or args.context_segment is not None) else 0


if __name__ == "__main__":
    raise SystemExit(main())
