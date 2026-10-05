"""Bounded TypeSafe System One API calls for required Jev decisions."""

from __future__ import annotations

import asyncio
import json
import math
import os
import shlex
import time
from pathlib import Path

import httpx

from .continuity_store import canonical
from .errors import ValidationError

MODEL = "jev-1.13.0"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
INPUT_BYTES = 4000
OUTPUT_BYTES = 32768
MAX_QUESTIONS = 16
DEADLINE = 0.5


class Unavailable(Exception):
    """A required Jev decision failed; the dependent operation must stop."""


def credential(root, env=None):
    env = os.environ if env is None else env
    value = env.get("TYPESAFE_API_KEY") or env.get("JEV_API_KEY")
    if not value:
        path = Path(env.get("TYPESAFE_API_KEY_FILE") or env.get("JEV_API_KEY_FILE")
                    or Path.home() / "workspace/.env/TYPESAFE_API_KEY").expanduser()
        try:
            # Also accept .env/KEY notation for a named entry in a dotenv file.
            dotenv = path.name == ".env"
            if path.name == "TYPESAFE_API_KEY" and path.parent.is_file():
                path, dotenv = path.parent, True
            with path.open("rb") as source:
                if dotenv:
                    value, scanned = None, 0
                    while scanned <= 65536:
                        raw = source.readline(8193)
                        if not raw:
                            break
                        scanned += len(raw)
                        line = raw.decode().strip().removeprefix("export ").lstrip()
                        if line.split("=", 1)[0].strip() == "TYPESAFE_API_KEY" and "=" in line:
                            parts = shlex.split(line.split("=", 1)[1], comments=True)
                            value = parts[0] if len(parts) == 1 else None
                            break
                    if value is None:
                        raise Unavailable("missing_credentials")
                else:
                    raw = source.read(8193)
                    if len(raw) > 8192:
                        raise Unavailable("invalid_credentials")
                    value = raw.decode().strip()
            if value.startswith("TYPESAFE_API_KEY="):
                value = value.split("=", 1)[1].strip().strip('"\'')
        except FileNotFoundError:
            raise Unavailable("missing_credentials") from None
        except (OSError, ValueError):
            raise Unavailable("invalid_credentials") from None
    if not isinstance(value, str) or not value or len(value) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise Unavailable("invalid_credentials")
    return value


def encode(state, questions):
    if not isinstance(state, (str, dict, list)) or not isinstance(questions, dict) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValidationError("Jev requires bounded state and 1–16 typed questions")
    for name, question in questions.items():
        if not isinstance(name, str) or not name or not isinstance(question, dict):
            raise ValidationError("Jev question IDs and bodies are invalid")
        if question.keys() - {"type", "instructions", "criteria"} or not question.get("instructions"):
            raise ValidationError("Jev question requires instructions and known fields")
        kind, criteria = question.get("type"), question.get("criteria")
        if kind == "choice":
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 16 or any(not isinstance(key, str) or not key for key in criteria):
                raise ValidationError("Jev Choice requires 2–16 exact options; never split its option set")
        elif kind == "noul":
            if criteria is not None and (not isinstance(criteria, dict) or criteria.keys() != {"true", "false"}):
                raise ValidationError("Jev Noul criteria require true and false")
        else:
            raise ValidationError("unsupported Jev question type")
    body = canonical({"model": MODEL, "state": state, "questions": questions}).encode()
    if len(body) > INPUT_BYTES:
        raise Unavailable("input_budget")
    return body


def _probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def validate(response, questions):
    if not isinstance(response, dict) or response.get("model") != MODEL:
        raise Unavailable("invalid_response")
    answers, usage = response.get("answers"), response.get("usage")
    if not isinstance(answers, dict) or answers.keys() != questions.keys():
        raise Unavailable("invalid_response")
    if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0 for key in ("input_tokens", "output_tokens")):
        raise Unavailable("invalid_response")
    clean = {}
    for name, question in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise Unavailable("invalid_response")
        if question["type"] == "noul":
            if not _probability(answer.get("noul")):
                raise Unavailable("invalid_response")
            clean[name] = {"type": "noul", "noul": answer["noul"]}
        else:
            probabilities = answer.get("probabilities")
            if (not isinstance(probabilities, dict) or probabilities.keys() != question["criteria"].keys()
                or any(not _probability(value) for value in probabilities.values())
                or not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-6)
                or not isinstance(answer.get("choice"), str) or answer["choice"] not in probabilities or not _probability(answer.get("confidence"))
                or probabilities[answer["choice"]] != max(probabilities.values())):
                raise Unavailable("invalid_response")
            clean[name] = {key: answer[key] for key in ("type", "choice", "confidence", "probabilities")}
    return {"model": MODEL, "answers": clean, "usage": {key: usage[key] for key in ("input_tokens", "output_tokens")}}


async def _post(endpoint, key, body, timeout):
    # One overall deadline covers connection, headers, and a slowly streamed body.
    async with asyncio.timeout(timeout):
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            async with client.stream("POST", endpoint, content=body,
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept-Encoding": "identity"}) as reply:
                if reply.status_code != 200:
                    raise Unavailable("http_" + str(reply.status_code))
                raw = bytearray()
                async for chunk in reply.aiter_raw():
                    if len(raw) + len(chunk) > OUTPUT_BYTES:
                        raise Unavailable("output_budget")
                    raw.extend(chunk)
                return json.loads(raw)


def evaluate(state, questions, *, key, endpoint=ENDPOINT, timeout=DEADLINE):
    if not 0 < timeout <= DEADLINE:
        raise ValidationError("Jev deadline must be at most 500 ms")
    body = encode(state, questions)
    started = time.monotonic()
    try:
        response = asyncio.run(_post(endpoint, key, body, timeout))
        result = validate(response, questions)
    except (TimeoutError, httpx.TimeoutException):
        raise Unavailable("timeout") from None
    except httpx.HTTPError:
        raise Unavailable("transport_error") from None
    except (ValueError, UnicodeError):
        raise Unavailable("invalid_response") from None
    return {**result, "input_bytes": len(body), "latency_ms": round((time.monotonic() - started) * 1000, 2)}
