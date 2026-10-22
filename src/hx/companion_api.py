"""One bounded Messages API exchange per Companion pass.

Every Companion — the worker context pass, the learning assessment and the
Partner's step state — is a pure function of a frozen slice: a byte-stable
system block (cached), the slice in the user turn, and its protocol's tools.
The model answers through tool use; evidence reads add at most two rounds.
There is no session, no temporary HOME and no subprocess. A request dies with
the process that sent it, and that process's identity owns the running job.

The credential is an Anthropic API key: an `sk-ant-api` key in seed/token, or
ANTHROPIC_API_KEY in the instance env file. It is read here, sent in one
header, and never logged, echoed or written anywhere else.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from .config_models import load_models
from .continuity_store import Conflict, canonical, digest
from .errors import ValidationError

API_VERSION = "2023-06-01"
BASE_URL = "https://api.anthropic.com"
#: One exchange plus at most two evidence-read rounds.
MAX_ROUNDS = 3
HOST_RESERVE = 4096
CREDENTIAL_BYTES = 8192
RESPONSE_BYTES = 4 << 20
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
LAST_ROUND = "This is the last round: submit now and leave anything you have not read pending."
#: Tests replay recorded responses through an `httpx.MockTransport` here; production sends.
TRANSPORT = None


def runtime(root, config, *, purpose):
    """Freeze the model, its effort and the output reserve for one Companion kind."""
    options = config.learning if purpose == "learning" else config.companion
    if purpose != "learning":
        provider = options.get("provider", "anthropic")
        if provider != "anthropic":
            raise ValidationError(f"Companion provider {provider} has no transport; only anthropic is built")
    selected = options.get("model") or config.model
    models_path = Path(root) / "config/models.json"
    model = load_models(models_path).get(selected)
    if model is None:
        raise ValidationError("the Companion model needs an explicit models.json context limit")
    output = model.max_output_tokens or min(8192, max(1, (model.window - model.threshold) // (MAX_ROUNDS + 1)))
    input_limit = min(model.threshold, model.window) - MAX_ROUNDS * output - min(HOST_RESERVE, model.window // 10)
    if input_limit <= 0:
        raise ValidationError("the Companion output reserve exhausts its model context window")
    return {"models_path": str(models_path.resolve()), "model_snapshot": model.__dict__,
            "model_input_limit": input_limit, "max_output_tokens": output,
            "model": selected, "effort": options.get("effort") or config.effort}


def verify_runtime(payload):
    model = load_models(payload["models_path"]).get(payload["model"])
    if model is None or model.__dict__ != payload["model_snapshot"]:
        raise Conflict("Companion model context limits changed after preparation")


def credential(root):
    """The API key, from seed/token when it holds one, else the env file. Never logged."""
    token = Path(root) / "seed/token"
    if token.is_file():
        info = token.stat()
        if info.st_mode & 0o77:
            raise ValidationError("seed/token must be private")
        if info.st_size > CREDENTIAL_BYTES:
            raise ValidationError("seed/token exceeds its supported bound")
        value = token.read_text(encoding="utf-8").strip()
        if value.startswith("sk-ant-api"):
            return value
    from . import envfile
    path = envfile.settings(Path(root)).get("env_file")
    if path:
        value = envfile.parse(Path(path)).get("ANTHROPIC_API_KEY", "").strip()
        if value:
            return value
    raise ValidationError("Messages API Companions need an Anthropic API key: an sk-ant-api key in "
                          "seed/token or ANTHROPIC_API_KEY in the instance env file")


def request_bytes(system, user, tools):
    return len(system.encode()) + len(user.encode()) + len(canonical(tools).encode())


def preflight(root, payload, *, system, user, tools, timeout_s=60):
    """Validate every deterministic input before a paid execution slot is claimed."""
    if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 60:
        raise ValidationError("a Companion pass deadline must be at most 60 seconds")
    verify_runtime(payload)
    size = request_bytes(system, user, tools)
    bound = min(payload.get("request_bytes", payload["model_input_limit"]), payload["model_input_limit"])
    if size > bound or size + payload.get("evidence_budget", 0) > payload["model_input_limit"]:
        raise ValidationError("the complete Companion request exceeds its model context budget")
    return {"credential": credential(root)}


def usage(data, round_number):
    """The response's own counts, as the job records them."""
    counts = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    entry = {key: counts[key] for key in USAGE_FIELDS if type(counts.get(key)) is int}
    return {"round": round_number, "id": data.get("id"), "model": data.get("model"),
            "stop_reason": data.get("stop_reason"), **entry}


def _failure(response):
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    detail = error.get("message") if isinstance(error.get("message"), str) else ""
    return f"Messages API {response.status_code} {error.get('type', 'error')}: {detail[:240]}"


def exchange(payload, *, credential, system, user, tools, handle, record, timeout_s=60, env=None):
    """At most MAX_ROUNDS requests for one pass.

    `handle(name, arguments)` answers one tool call with `(text, is_error, done)`;
    `done` ends the pass. `record(entry)` keeps each response's usage before the
    next round, so a crash still leaves what was paid for.
    """
    values = os.environ if env is None else env
    base = values.get("ANTHROPIC_BASE_URL") or BASE_URL
    headers = {"x-api-key": credential, "anthropic-version": API_VERSION, "content-type": "application/json"}
    messages = [{"role": "user", "content": [{"type": "text", "text": user}]}]
    deadline = time.monotonic() + timeout_s
    stop = None
    with httpx.Client(base_url=base, headers=headers, transport=TRANSPORT) as client:
        for number in range(1, MAX_ROUNDS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"outcome": "deadline", "rounds": number - 1, "stop_reason": stop}
            body = {"model": payload["model"], "max_tokens": payload["max_output_tokens"],
                    "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    "tools": tools, "tool_choice": {"type": "auto"}, "messages": messages,
                    "output_config": {"effort": payload["effort"]}}
            try:
                response = client.post("/v1/messages", content=canonical(body).encode(), timeout=remaining)
            except httpx.HTTPError as exc:
                return {"outcome": "transport_error", "rounds": number - 1, "error": type(exc).__name__}
            if response.status_code != 200:
                return {"outcome": "http_error", "rounds": number, "status": response.status_code,
                        "error": _failure(response)}
            if len(response.content) > RESPONSE_BYTES:
                return {"outcome": "oversized_response", "rounds": number}
            try:
                data = response.json()
            except ValueError:
                return {"outcome": "malformed_response", "rounds": number}
            record(usage(data, number))
            stop = data.get("stop_reason")
            if stop != "tool_use":
                return {"outcome": "no_submission", "rounds": number, "stop_reason": stop}
            results = []
            for block in data.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                text, is_error, done = handle(block.get("name"), block.get("input"))
                if done:
                    return {"outcome": "submitted", "rounds": number, "stop_reason": stop}
                results.append({"type": "tool_result", "tool_use_id": block.get("id"), "content": text,
                                **({"is_error": True} if is_error else {})})
            if not results:
                return {"outcome": "no_submission", "rounds": number, "stop_reason": stop}
            if number == MAX_ROUNDS - 1:
                results.append({"type": "text", "text": LAST_ROUND})
            messages = [*messages, {"role": "assistant", "content": data["content"]},
                        {"role": "user", "content": results}]
    return {"outcome": "rounds_exhausted", "rounds": MAX_ROUNDS, "stop_reason": stop}


def own_process():
    """This process: the owner of every job it sends, for reconciliation after a crash."""
    from . import native_processes
    return native_processes.probe(os.getpid())


def supersede_unpaid(store, table, job_id, prompt_version):
    """Retire stale prepared inputs without ever replaying attempted execution."""
    if table not in {"companion_jobs", "learning_jobs"}:
        raise ValueError("unknown Companion table")
    with store.transaction() as tx:
        current = tx.db.execute(f"SELECT * FROM {table} WHERE job_id=?", (job_id,)).fetchone()
        payload = json.loads(current["payload"])
        if current["status"] != "prepared" or payload.get("attempts", 0) or payload["prompt_version"] == prompt_version:
            return False
        tx._change()
        tx.db.execute(f"UPDATE {table} SET status='superseded',request_key=? WHERE job_id=?",
                      (digest(["superseded-unpaid", current["request_key"], job_id]), job_id))
    return True
