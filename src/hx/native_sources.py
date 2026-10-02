"""Register only explicitly reported sources in the original private launch home."""

from __future__ import annotations

from pathlib import Path

from . import hook_contract, native_capture, native_launch, observer
from .continuity_store import Conflict, _id, digest
from .errors import ValidationError


def observe(store, *, run_id, launch_id, worker_id, adapter, event, payload):
    # Other adapters retain their verified hook/direct-event paths until a
    # native file decoder and source-location contract have been established.
    if adapter not in {"claude", "codex"} or event not in {"context", "request", "stop", "subagent-start", "subagent-stop"}:
        return []
    hook_contract.validate(store, run_id=run_id, launch_id=launch_id, adapter=adapter, worker_id=worker_id)
    launch = native_launch._row(store, run_id)
    if launch is None:
        return []  # Standalone capture contracts do not own a private home.
    if launch["launch_id"] != launch_id or launch["payload"]["worker_id"] != worker_id:
        raise Conflict("native source does not belong to the original launch")
    actor = payload.get("agent_id") or payload.get("agentId") or payload.get("subagent_id")
    reports = []
    if payload.get("transcript_path") and (not actor or event in {"subagent-start", "subagent-stop"}):
        reports.append((None, payload["transcript_path"]))
    if payload.get("agent_transcript_path"):
        if adapter == "codex":
            raise Conflict("Codex child ancestry requires a verified source contract")
        if not actor:
            raise ValidationError("native child source requires its stable actor identity")
        _id(actor)
        reports.append((actor, payload["agent_transcript_path"]))
    if not reports:
        return []
    session = payload.get("session_id") or payload.get("sessionId")
    _id(session)
    expected = launch["payload"].get("native_session")
    if expected and expected != session:
        raise Conflict("native source belongs to another session")
    # Validate the original task revision too; never register late evidence on
    # the worker slot's newer assignment.
    with store.transaction() as tx:
        native_capture._run(tx, run_id, worker_id)
    home = Path(launch["payload"]["capsule"]) / "run" / worker_id / "home"
    if home.resolve() != home:
        raise Conflict("original private home path was redirected")
    info = home.stat()
    sources = []
    decoder, policy = "claude-v1", "claude-linear-v1"
    if adapter == "codex":
        from .codex_rollout import DECODER, POLICY
        decoder, policy = DECODER, POLICY
    for actor, reported in reports:
        if not isinstance(reported, str) or len(reported.encode()) > 4096 or "\0" in reported or not Path(reported).is_absolute():
            raise ValidationError("native transcript requires a bounded absolute path")
        path = Path(reported).resolve()
        if not path.is_relative_to(home) or path.suffix != ".jsonl":
            raise Conflict("native transcript lies outside the original private home")
        stream = "child:" + digest([launch_id, actor]) if actor else "native:" + digest([launch_id, session])
        sources.append(observer.register(store, run_id=run_id, stream_id=stream, path=path,
            decoder=decoder, session_id=session, native_scope={"root": str(home),
                "root_identity": [info.st_dev, info.st_ino], "actor_id": actor,
                "launch_id": launch_id, "branch_policy": policy}))
    return sources
