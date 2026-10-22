"""Recorded Messages API responses, replayed through `httpx.MockTransport`.

Each file in fixtures/companion_api is one response as the API returns it. A
tool_use block's input names the value the test supplies (`"$patch"`), because
a frozen pass's identities differ per run. No request leaves the process: the
handler answers from the recording and keeps what was sent for assertions.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from hx import companion_api

FIXTURES = Path(__file__).parent / "fixtures" / "companion_api"
#: A Messages API key shape; it never leaves the test process.
KEY = "sk-ant-api03-fixture-local-only"


def api_key(root):
    token = Path(root) / "seed/token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(KEY + "\n")
    token.chmod(0o600)


def _fill(value, inputs):
    if isinstance(value, str) and value.startswith("$"):
        return inputs[value[1:]]
    if isinstance(value, dict):
        return {key: _fill(item, inputs) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill(item, inputs) for item in value]
    return value


@contextmanager
def replay(*steps, **inputs):
    """Answer each request with the next recorded response; yield what was sent.

    A step is a fixture name, or `(name, inputs)` to fill that response alone.
    One request more than was recorded fails the test.
    """
    recorded = []
    for step in steps:
        name, extra = (step, {}) if isinstance(step, str) else step
        recorded.append((json.loads((FIXTURES / f"{name}.json").read_text()), {**inputs, **extra}))
    sent = []

    def handler(request):
        if len(sent) >= len(recorded):
            pytest.fail("the Companion sent a request no recording answers")
        sent.append({"url": str(request.url), "headers": request.headers, "body": json.loads(request.content),
                     "content": request.content})
        response, values = recorded[len(sent) - 1]
        return httpx.Response(response["status"], json=_fill(response["body"], values))

    previous = companion_api.TRANSPORT
    companion_api.TRANSPORT = httpx.MockTransport(handler)
    try:
        yield sent
    finally:
        companion_api.TRANSPORT = previous


def silent():
    """No request may be sent."""
    return replay()
