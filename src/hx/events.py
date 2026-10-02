"""Versioned public-event decoders. Private reasoning is never persisted.

Hook normalization is shared by all five adapters. Native decoders are separate
contracts: an unknown record is a visible capture gap, not a silently lost event.
"""

from __future__ import annotations

from dataclasses import dataclass

from .continuity_store import EVENT_KINDS, digest
from .errors import ValidationError


class DecodeGap(ValidationError):
    pass


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict
    native_id: str | None = None
    usage: dict | None = None


def public_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"] for block in content
        if isinstance(block, dict) and block.get("type") in {"text", "input_text", "output_text"}
        and isinstance(block.get("text"), str)
    )


def _usage(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    # Keep provider names; interpretation belongs to capability-specific accounting.
    allowed = {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
               "input", "output", "cacheRead", "cacheWrite", "cacheWrite1h", "reasoning", "total_tokens", "totalTokens",
               "cached_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens", "cached_tokens", "reasoning_tokens"}
    result = {key: item for key, item in value.items() if key in allowed and type(item) is int and item >= 0}
    return result or None


def hook_v1(body: dict) -> list[Event]:
    aliases = {"toolName": "tool_name", "toolInput": "tool_input", "toolResult": "tool_response",
               "tool_result": "tool_response", "toolUseId": "tool_use_id", "sessionId": "session_id",
               "hookEventName": "hook_event_name", "backgroundTasks": "background_tasks",
               "lastAssistantMessage": "last_assistant_message", "agentId": "agent_id", "turnId": "turn_id",
               "subagent_id": "agent_id", "childSessionId": "child_session_id",
               "promptId": "prompt_id", "errorDetails": "error_details", "stopHookActive": "stop_hook_active"}
    p = dict(body)
    for source, target in aliases.items():
        if source in p and target not in p:
            p[target] = p[source]
    event = p.get("hook_event_name") or p.get("event")
    if not isinstance(event, str):
        raise DecodeGap("hook event has no event name")
    key = event.replace("_", "").replace("-", "").lower()
    identifier = p.get("native_event_id") or p.get("event_id")
    identifier = identifier if isinstance(identifier, str) and identifier else None
    if key in {"postllmcall", "modelresponse"}:
        request = p.get("request_id")
        if not isinstance(request, str) or not request or p.get("status") not in {"success", "failed"}:
            raise DecodeGap("model response requires its request identity and known outcome")
        data = {name: p[name] for name in ("agent_id", "turn_id", "request_id", "response_id", "provider", "model",
            "attempt", "step", "status", "finish_reason", "error", "message_count", "tool_count", "tool_call_count") if name in p}
        data.update(source="model_response", usage_scope="provider_attempt", settled=False)
        # Request summaries/previews are truncated native diagnostics, not
        # complete messages, tool schemas, or evidence of prompt equivalence.
        return [Event("boundary", data, "model-response:" + request, _usage(p.get("usage")))]
    if key in {"stopfailure", "stopcancelled", "sessionend"}:
        # Turn-end notifications may arrive after a later request. Preserve the
        # vendor's correlation fields; arrival order is not task completion.
        data = {name: p[name] for name in ("agent_id", "agent_type", "child_session_id", "turn_id", "prompt_id",
            "error", "error_details", "reason", "last_assistant_message", "stop_hook_active") if name in p}
        data.update(source=key, outcome={"stopfailure": "failed", "stopcancelled": "cancelled",
                                        "sessionend": "session_ended"}[key], settled=False)
        # On failure, last_assistant_message can be the rendered API error.
        # It must not become a claimed assistant finding or settle a child.
        return [Event("boundary", data, identifier)]
    if key in {"pretooluse", "toolstart"}:
        call = p.get("tool_use_id")
        scoped_call = digest([p["agent_id"], call]) if p.get("agent_id") else call
        return [Event("boundary", {"source": "tool_admission", **{name: p[name] for name in
            ("tool_name", "tool_use_id", "agent_id") if name in p}},
            "admission:" + str(scoped_call) if call else identifier)]
    if key in {"posttooluse", "posttoolusefailure", "log", "toolresult"}:
        call = p.get("tool_use_id")
        scoped_call = digest([p["agent_id"], call]) if p.get("agent_id") else call
        native = f"tool:{scoped_call}" if isinstance(call, str) and call else identifier
        data = {name: p[name] for name in ("tool_name", "tool_use_id", "tool_input", "tool_response",
                                          "agent_id", "source_fingerprints", "cwd") if name in p}
        if key == "posttoolusefailure":
            data["is_error"] = True
            if "error" in p:
                data["error"] = p["error"]
        return [Event("tool_result", data, native, _usage(p.get("usage")))]
    if key in {"userpromptsubmit", "request", "correction"}:
        text = p.get("prompt", p.get("text"))
        if not isinstance(text, str):
            raise DecodeGap("request hook lacks prompt text")
        return [Event("correction" if key == "correction" else "request", {"text": text,
            **{name: p[name] for name in ("turn_id", "prompt_id", "agent_id") if name in p}}, identifier)]
    if key in {"stop", "subagentstop", "finish"}:
        result = []
        message = p.get("last_assistant_message")
        if isinstance(message, str) and message:
            # Stop has no guaranteed message ID; never merge by matching text.
            result.append(Event("assistant_message", {"text": message, "claim": True},
                                p.get("assistant_message_id")))
        result.append(Event("finish", {name: p[name] for name in ("agent_id", "child_session_id", "background_tasks", "turn_id",
            "prompt_id", "reason", "stop_hook_active") if name in p}, identifier))
        return result
    if key in {"sessionstart", "context", "precompact", "postcompact", "boundary"}:
        return [Event("boundary", {name: p[name] for name in ("source", "trigger", "turn_id", "compact_summary") if name in p}, identifier)]
    if key in {"subagentstart", "spawn"}:
        return [Event("spawn", {name: p[name] for name in ("agent_id", "child_session_id", "agent_type", "tool_input") if name in p}, identifier)]
    raise DecodeGap(f"unsupported hook event {event}")


def claude_v1(body: dict) -> list[Event]:
    kind = body.get("type")
    if kind == "attachment":
        attachment = body.get("attachment")
        if not isinstance(attachment, dict) or not isinstance(attachment.get("type"), str):
            raise DecodeGap("invalid Claude attachment")
        return [Event("request", {"attachment_type": attachment["type"], "content_available": False}, body.get("uuid"))]
    if kind in {"progress", "file-history-snapshot", "queue-operation", "summary", "system"}:
        # Lifecycle hooks retain the boundary; these rows are provider bookkeeping.
        return []
    if kind not in {"user", "assistant"} or not isinstance(body.get("message"), dict):
        raise DecodeGap(f"unsupported Claude transcript record {kind}")
    message = body["message"]
    content = message.get("content")
    native = body.get("uuid")
    native = native if isinstance(native, str) and native else None
    results = []
    text = public_text(content)
    usage = _usage(message.get("usage"))
    if text or usage:
        results.append(Event("assistant_message" if kind == "assistant" else "request",
                             {"text": text, "claim": kind == "assistant"}, native, usage))
    if isinstance(content, list):
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                raise DecodeGap("invalid Claude content block")
            block_type = block.get("type")
            if block_type == "tool_result":
                call = block.get("tool_use_id")
                results.append(Event("tool_result", {"tool_use_id": call,
                    "tool_response": block.get("content"), "is_error": bool(block.get("is_error"))},
                    f"tool:{call}" if isinstance(call, str) and call else f"{native}:result:{index}" if native else None))
            elif block_type == "tool_use":
                # Calls are observable execution requests, not successful outcomes.
                results.append(Event("assistant_message", {"tool_use_id": block.get("id"),
                    "tool_name": block.get("name"), "tool_input": block.get("input"), "claim": True},
                    f"call:{block['id']}" if isinstance(block.get("id"), str) else None))
            elif block_type not in {"text", "thinking", "redacted_thinking", "image", "document"}:
                raise DecodeGap(f"unsupported Claude content block {block_type}")
            elif block_type in {"image", "document"}:
                results.append(Event("request", {"attachment_type": block_type, "content_available": False},
                                     f"{native}:attachment:{index}" if native else None))
    return results


def pi_v1(body: dict) -> list[Event]:
    kind = body.get("type")
    if kind in {"session", "model_change", "thinking_level_change", "custom", "label", "session_info",
                "message_start", "message_update", "tool_execution_update"}:
        # Streaming updates can contain private thinking. message_end and
        # tool_execution_end supply the authoritative public content.
        return []
    if kind in {"agent_start", "turn_start", "compaction_start", "queue_update"}:
        return [Event("boundary", {"source": kind, "turn_complete": False})]
    if kind in {"agent_end", "turn_end", "agent_settled", "compaction_end"}:
        return [Event("finish" if kind != "compaction_end" else "boundary",
                      {"source": kind, "settled": kind == "agent_settled"})]
    if kind in {"compaction", "branch_summary"}:
        return [Event("boundary", {"source": kind, "summary": body.get("summary"), "claim": True}, body.get("id"))]
    if kind == "tool_execution_start":
        call = body.get("toolCallId")
        return [Event("assistant_message", {"tool_use_id": call, "tool_name": body.get("toolName"),
                     "tool_input": body.get("args"), "claim": True, "status": "started"},
                     f"call:{call}" if isinstance(call, str) and call else None)]
    if kind == "tool_execution_end":
        call = body.get("toolCallId")
        return [Event("tool_result", {"tool_use_id": call, "tool_name": body.get("toolName"),
                     "tool_input": body.get("args"), "tool_response": body.get("result"), "is_error": bool(body.get("isError"))},
                     f"tool:{call}" if isinstance(call, str) and call else None)]
    if kind not in {"message", "message_end"} or not isinstance(body.get("message"), dict):
        raise DecodeGap(f"unsupported Pi record {kind}")
    message = body["message"]
    role = message.get("role")
    native = body.get("id")
    if role == "toolResult":
        call = message.get("toolCallId")
        return [Event("tool_result", {"tool_use_id": call, "tool_name": message.get("toolName"),
                     "tool_response": message.get("content"), "is_error": bool(message.get("isError"))},
                     f"tool:{call}" if isinstance(call, str) and call else native)]
    if role not in {"user", "assistant"}:
        raise DecodeGap(f"unsupported Pi message role {role}")
    content = message.get("content")
    if not isinstance(content, (str, list)):
        raise DecodeGap("invalid Pi message content")
    data = {"text": public_text(content), "claim": role == "assistant"}
    if body.get("agent_id"):
        data["agent_id"] = body["agent_id"]
        if native:
            native = digest([body["agent_id"], native])
    for key in ("stopReason", "errorMessage"):
        if isinstance(message.get(key), str):
            data[key] = message[key]
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                raise DecodeGap("invalid Pi content block")
            kind = block.get("type")
            if kind == "toolCall":
                data.setdefault("tool_calls", []).append({"tool_use_id": block.get("id"),
                    "tool_name": block.get("name"), "tool_input": block.get("arguments")})
                if isinstance(block.get("namespace"), str):
                    data["tool_calls"][-1]["namespace"] = block["namespace"]
            elif kind == "image":
                data.setdefault("attachments", []).append({"attachment_type": "image", "content_available": False})
            elif kind not in {"text", "thinking", "redacted_thinking"}:
                raise DecodeGap(f"unsupported Pi content block {kind}")
            elif kind == "text" and not isinstance(block.get("text"), str):
                raise DecodeGap("invalid Pi text block")
    return [Event("assistant_message" if role == "assistant" else "request",
                  data, native, _usage(message.get("usage")))]


def normalized_v1(body: dict) -> list[Event]:
    if body.get("schema_version") != 1 or body.get("kind") not in EVENT_KINDS or not isinstance(body.get("data"), dict):
        raise DecodeGap("normalized event requires schema_version=1, a known kind, and data")
    return [Event(body["kind"], body["data"], body.get("native_event_id"), _usage(body.get("usage")))]


def public_batch_v1(body: dict) -> list[Event]:
    records = body.get("events")
    if body.get("schema_version") != 1 or not isinstance(records, list) or len(records) > 16:
        raise DecodeGap("public hook batch requires schema_version=1 and at most 16 observations")
    result = []
    for record in records:
        if not isinstance(record, dict):
            raise DecodeGap("public hook batch contains a non-object observation")
        result.extend(normalized_v1(record))
    return result


def codex_rollout(body):
    from .codex_rollout import decode
    return decode(body)


DECODERS = {"hook-v1": hook_v1, "claude-v1": claude_v1, "pi-v1": pi_v1, "codex-rollout-0.156.1": codex_rollout, "normalized-v1": normalized_v1,
            "public-batch-v1": public_batch_v1}


def decode(version: str, body: dict) -> list[Event]:
    if version not in DECODERS or not isinstance(body, dict):
        raise DecodeGap("unregistered decoder or non-object source record")
    try:
        events = DECODERS[version](body)
    except (KeyError, TypeError, ValueError) as exc:
        raise DecodeGap(f"invalid {version} record structure") from exc
    for event in events:
        if event.native_id is not None and (not isinstance(event.native_id, str) or not event.native_id or len(event.native_id) > 512):
            raise DecodeGap("invalid native event identity")
    return events
