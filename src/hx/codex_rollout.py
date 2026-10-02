"""Public rollout decoder verified against installed Codex CLI 0.156.1.

The hook transcript is not a stable vendor API. Unknown versions/records require
reconciliation; encrypted items and private reasoning never enter the ledger.
"""

from __future__ import annotations

import json

from .continuity_store import digest
from .events import DecodeGap, Event, _usage, public_text

VERSION = "0.156.1"
POLICY = "codex-0.156.1-v1"
DECODER = "codex-rollout-0.156.1"


def active(body, state):
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise DecodeGap("invalid Codex rollout envelope")
    scope = state["native_scope"]
    if scope["actor_id"] is not None:
        raise DecodeGap("Codex child ancestry requires a verified source contract")
    session = state["session_id"]
    for key in ("session_id", "thread_id"):
        if payload.get(key) not in (None, session):
            raise DecodeGap("Codex record belongs to another native session")
    if body.get("type") == "session_meta":
        if (state.get("native_meta_seen") or payload.get("id") != session
                or payload.get("cli_version") != VERSION or payload.get("forked_from_id")):
            raise DecodeGap("Codex source identity, version, or ancestry is unverified")
        state["native_meta_seen"] = True
    elif not state.get("native_meta_seen"):
        raise DecodeGap("Codex source must begin with its verified session metadata")
    if (body.get("type") == "compacted"
            or (body.get("type") == "event_msg" and payload.get("type") == "thread_rolled_back")
            or (body.get("type") == "response_item" and payload.get("type") in {"compaction", "context_compaction", "compaction_trigger"})):
        raise DecodeGap("Codex continuation boundary requires source reconciliation")
    return True


def _text_content(content):
    if not isinstance(content, (str, list)):
        raise DecodeGap("invalid Codex public content")
    data = {"text": public_text(content)}
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                raise DecodeGap("invalid Codex content block")
            kind = part.get("type")
            if kind in {"input_text", "output_text"}:
                if not isinstance(part.get("text"), str):
                    raise DecodeGap("invalid Codex text block")
            elif kind in {"input_image", "input_audio"}:
                data.setdefault("attachments", []).append({"attachment_type": kind, "content_available": False})
            elif kind == "encrypted_content":
                data["content_available"] = False
            else:
                raise DecodeGap("unknown Codex public content block")
    return data


def _tool_definition(tool, depth=0):
    if not isinstance(tool, dict) or depth > 8:
        raise DecodeGap("Codex discovered tool exceeds its structural bound")
    if tool.get("type") not in {"function", "custom", "namespace"} or not isinstance(tool.get("name"), str):
        raise DecodeGap("unknown Codex discovered tool shape")
    public = {key: tool[key] for key in ("type", "name", "namespace", "description", "parameters", "format", "strict") if key in tool}
    if "tools" in tool:
        if not isinstance(tool["tools"], list):
            raise DecodeGap("invalid Codex tool namespace")
        public["tools"] = [_tool_definition(child, depth + 1) for child in tool["tools"]]
    return public


def _response(item):
    kind = item.get("type")
    native = item.get("id")
    if kind == "reasoning":
        return []
    if kind in {"compaction", "context_compaction", "compaction_trigger"}:
        return [Event("boundary", {"source": kind, "continuation_required": True}, native)]
    if kind == "configuration_update":
        return [Event("boundary", {"source": kind})]
    if kind == "agent_message":
        return [Event("assistant_message", {**_text_content(item.get("content")), "source": kind,
            "author": item.get("author"), "recipient": item.get("recipient"), "claim": True}, native)]
    if kind == "message":
        role = item.get("role")
        if role in {"system", "developer"}:
            # The launch manifest owns instructions. Retain a change fingerprint,
            # not a second copy of every native instruction expansion.
            return [Event("boundary", {"source": "native_instructions", "role": role,
                          "content_hash": digest(item.get("content"))}, native)]
        if role not in {"user", "assistant"}:
            raise DecodeGap("unknown Codex message role")
        return [Event("assistant_message" if role == "assistant" else "request",
            {**_text_content(item.get("content")), "claim": role == "assistant"}, native)]
    if kind in {"function_call", "custom_tool_call"}:
        call = item.get("call_id")
        arguments = item.get("arguments") if kind == "function_call" else item.get("input")
        if not isinstance(call, str) or not call or not isinstance(item.get("name"), str) or not isinstance(arguments, str):
            raise DecodeGap("invalid Codex tool call")
        if kind == "function_call":
            try:
                arguments = json.loads(arguments)
            except ValueError as exc:
                raise DecodeGap("invalid Codex tool arguments") from exc
        data = {"tool_use_id": call, "tool_name": item["name"], "tool_input": arguments, "claim": True}
        if isinstance(item.get("namespace"), str):
            data["namespace"] = item["namespace"]
        return [Event("assistant_message", data, "call:" + call)]
    if kind == "local_shell_call":
        action = item.get("action")
        if not isinstance(action, dict) or action.get("type") != "exec" or not isinstance(action.get("command"), list):
            raise DecodeGap("invalid Codex local shell action")
        call = item.get("call_id")
        return [Event("assistant_message", {"tool_name": "local_shell", "tool_use_id": call,
            "tool_input": {key: action[key] for key in ("command", "env", "timeout_ms", "user", "working_directory") if key in action},
            "status": item.get("status"), "claim": True}, "call:" + call if isinstance(call, str) and call else native)]
    if kind in {"function_call_output", "custom_tool_call_output"}:
        call = item.get("call_id")
        if not isinstance(call, str) or not call:
            raise DecodeGap("Codex tool output lacks a call identity")
        output = item.get("output")
        data = {"tool_use_id": call, "tool_response": output if isinstance(output, str) else _text_content(output)}
        return [Event("tool_result", data, "tool:" + call)]
    if kind in {"tool_search_call", "tool_search_output"}:
        call = item.get("call_id")
        data = {"source": kind, "tool_name": "tool_search", "tool_use_id": call,
                "execution": item.get("execution"), "status": item.get("status")}
        if kind == "tool_search_call":
            data.update(tool_input=item.get("arguments"), claim=True)
            return [Event("assistant_message", data, "call:" + call if isinstance(call, str) and call else native)]
        definitions = item.get("tools")
        if not isinstance(definitions, list) or any(not isinstance(tool, dict) for tool in definitions):
            raise DecodeGap("invalid Codex discovered tools")
        data["tool_response"] = {"tools": [_tool_definition(tool) for tool in definitions]}
        return [Event("tool_result", data, "tool:" + call if isinstance(call, str) and call else native)]
    if kind == "web_search_call":
        action = item.get("action")
        if not isinstance(action, dict) or action.get("type") not in {"search", "open_page", "find_in_page"}:
            raise DecodeGap("Codex web search lacks its public action")
        return [Event("assistant_message", {"source": kind, "action": {key: action[key] for key in
            ("type", "queries", "query", "url", "pattern") if key in action},
            "status": item.get("status"), "claim": True}, native)]
    if kind == "image_generation_call":
        return [Event("assistant_message", {"source": kind, "status": item.get("status"),
            "attachment_type": "image", "content_available": False, "claim": True}, native)]
    raise DecodeGap("unsupported Codex response item")


def decode(body):
    kind, payload = body.get("type"), body.get("payload")
    if not isinstance(payload, dict):
        raise DecodeGap("invalid Codex rollout envelope")
    if kind == "session_meta":
        if payload.get("cli_version") != VERSION:
            raise DecodeGap("unverified Codex rollout version")
        return [Event("boundary", {"source": kind, "native_session_id": payload.get("id"), "cli_version": VERSION})]
    if kind == "response_item":
        return _response(payload)
    if kind in {"world_state", "turn_context"}:
        return [Event("boundary", {"source": kind, "turn_id": payload.get("turn_id"), "state_hash": digest(payload)})]
    if kind == "token_usage_record":
        usage = _usage(payload.get("usage"))
        if usage is None:
            raise DecodeGap("Codex usage record lacks valid counts")
        native = payload.get("response_id")
        return [Event("boundary", {"source": kind, "usage_scope": "response", "turn_id": payload.get("turn_id"),
            "turn_usage": _usage(payload.get("turn_token_usage")), "session_usage": _usage(payload.get("thread_token_usage"))},
            "usage:" + native if isinstance(native, str) and native else None, usage)]
    if kind != "event_msg":
        raise DecodeGap("unsupported Codex rollout record")
    event = payload.get("type")
    if event == "token_count":
        info = payload.get("info")
        if info is None:
            return []
        if not isinstance(info, dict):
            raise DecodeGap("invalid Codex token count")
        return [Event("boundary", {"source": event, "usage_scope": "session_total",
            "last_usage": _usage(info.get("last_token_usage")), "context_window": info.get("model_context_window")},
            usage=_usage(info.get("total_token_usage")))]
    if event in {"task_started", "task_complete", "turn_aborted"}:
        return [Event("boundary" if event == "task_started" else "finish", {"source": event,
            "turn_id": payload.get("turn_id"), "turn_complete": event == "task_complete", "settled": False})]
    if event == "item_completed":
        item = payload.get("item")
        if not isinstance(item, dict):
            raise DecodeGap("invalid Codex completed item")
        if item.get("type") in {"UserMessage", "AgentMessage", "Reasoning"}:
            return []  # Canonical public response_item records carry messages.
        if item.get("type") == "CommandExecution":
            call = item.get("id")
            if not isinstance(call, str) or not call:
                raise DecodeGap("Codex command result lacks an identity")
            result = {key: item[key] for key in ("status", "stdout", "stderr", "aggregated_output", "exit_code", "process_id") if key in item}
            data = {"tool_use_id": call, "tool_name": "Bash", "tool_response": result}
            if type(item.get("exit_code")) is int:
                data["is_error"] = item["exit_code"] != 0
            elif item.get("status") in {"failed", "declined"}:
                data["is_error"] = True
            return [Event("tool_result", data, "tool:" + call)]
    raise DecodeGap("unsupported Codex native event")
