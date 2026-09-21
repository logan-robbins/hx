"""`hx companion <id>` — the Companion loop (spec 10, 05, 07.2).

One process per HarnessAgent, in tmux window `<id>:companion`, serving every stream that agent
owns. It reads raw streams and writes step state, closed-stream digests, and the seam marker.
It never writes the agent's files, with one exception: the `## Digest` section of the work
item, once, inside `hx complete`.

**Model access.** `provider: claude-cli` (spec 05): the pinned `claude -p` binary, the same
`seed/token`, a Companion-only `CLAUDE_CONFIG_DIR` with no hooks, no skills and no CLAUDE.md,
and every tool denied — the Companion reads what hx hands it on stdin and returns JSON. It
cannot act, by construction.

`--bare` would be the obvious way to get a clean run, and it is the wrong one: bare mode
"never reads OAuth credentials or the system keychain" and expects `ANTHROPIC_API_KEY`, which
a subscription-only deployment does not have. The dedicated config dir gets the same isolation
while the token still works (docs/en/headless, 2026-09-20).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import store, streams, timestamps
from .config_harness import HarnessConfig, load_harness
from .errors import HxError, NotFound
from .ids import PARTNER
from .stepstate import InvalidState, empty, estimate_tokens, evict, load as load_state, validate
from .subagents import load as load_subagents

#: What the Companion is asked for, in one line. The payload goes on stdin; this never grows.
PROMPT = (
    "Return the new step state for this stream as one JSON object, per your instructions. "
    "The current state and the new records follow on stdin."
)

#: Constrains the model's output to the shape `hx.stepstate` validates (docs/en/headless).
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "seq": {"type": "integer"},
        "goal": {"type": "string"},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "object"}},
        "open_steps": {"type": "array", "items": {"type": "object"}},
        "closed_steps": {"type": "array", "items": {"type": "object"}},
        "dead_ends": {"type": "array", "items": {"type": "string"}},
        "working_set": {"type": "object"},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "subagents_open": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["seq"],
}

DIGEST_PROMPT = (
    "Write the closed-stream digest for this subagent: a few lines of what it did, what it "
    "committed, and what it left open. Plain markdown, no fences."
)

POLL_SECONDS = 1.0


@dataclass
class Call:
    """One provider call and what came back."""

    state: dict | None
    raw: str
    usage: dict
    error: str | None = None


def companion_home(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / "companion-home"


def system_prompt_path(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / "companion-system.md"


def state_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "state" / item_id / f"{stream}.json"


def digest_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "state" / item_id / f"{stream}.digest.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else ""


def prompt_version(root: Path, role: str) -> dict:
    """The shas of `BASE.md` and the role file — what `hx metrics` compares across (spec 07.4)."""
    return {
        "base": _sha(root / "companion" / "BASE.md"),
        "role": _sha(root / "companion" / "roles" / f"{role}.md"),
    }


def compose_system_prompt(root: Path, config: HarnessConfig) -> Path:
    """`BASE.md` + the role file + the harness facts, composed once at start (spec 10, 05).

    The layered order is the prefix the binary caches, so it is identical on every call for
    this agent and shared as far as the role file with every other companion on this model.
    """
    base = root / "companion" / "BASE.md"
    role = root / "companion" / "roles" / f"{config.role}.md"
    for path in (base, role):
        if not path.is_file():
            raise NotFound(f"{path}: missing; the Companion's system prompt is composed from it (spec 10)")

    companion = config.companion or {}
    facts = "\n".join([
        "## This agent",
        "",
        f"- id: `{config.id}`",
        f"- pod: `{config.pod}`",
        f"- role: `{config.role}`",
        f"- model the agent runs on: `{config.model}`, effort `{config.effort}`",
        f"- state budget: {companion.get('state_budget_tokens', 10000)} tokens "
        f"(about {companion.get('state_budget_tokens', 10000) * 4} characters of JSON)",
        f"- seam policy: not before {companion.get('seam_min_context_tokens', 60000)} context "
        f"tokens, and not more often than every {companion.get('seam_min_interval_s', 600)}s",
        "",
        "You read no configuration at runtime. Everything you need is above.",
        "",
    ])
    target = system_prompt_path(root, config.id)
    store.atomic_write_text(target, f"{base.read_text()}\n\n{role.read_text()}\n\n{facts}")
    return target


def identity_text(root: Path, item_id: str, is_main: bool) -> str:
    path = root / "config" / item_id / ("AGENTS.md" if is_main else "SUBAGENTS.md")
    return path.read_text() if path.is_file() else ""


def task_text(root: Path, item_id: str) -> str:
    from .tasks import load_tasks

    entry = load_tasks(root).get(item_id) or {}
    parts = [entry.get("order") or ""]
    for addendum in entry.get("addenda") or []:
        parts.append(f"## Order addendum {addendum.get('ts', '')}\n\n{addendum.get('text', '')}")
    return "\n\n".join(part.strip("\n") for part in parts if part.strip())


def build_payload(root: Path, item_id: str, stream: str, state: dict, records: list[dict]) -> str:
    """The layered payload of spec 10, in the order the binary caches (identical prefix first)."""
    is_main = stream == f"{item_id}-main"
    return "\n".join([
        "# Identity of this stream",
        "",
        identity_text(root, item_id, is_main) or "_none_",
        "",
        "# Task",
        "",
        task_text(root, item_id) or "_none recorded_",
        "",
        "# Current step state",
        "",
        "```json",
        json.dumps(state, indent=2),
        "```",
        "",
        f"# New records on {stream} (seq > {state.get('seq', 0)})",
        "",
        "```jsonl",
        "\n".join(json.dumps(record) for record in records),
        "```",
        "",
    ])


def call_model(
    root: Path, config: HarnessConfig, payload: str, *, schema: dict | None = OUTPUT_SCHEMA,
    prompt: str = PROMPT, text_result: bool = False, env=None,
) -> Call:
    """One stateless `claude -p` call. Never raises: a failed call keeps the prior state."""
    from .claude_bin import load_pin

    env = dict(os.environ if env is None else env)
    pin = load_pin(root) or {}
    binary = os.environ.get("HX_COMPANION_BIN") or env.get("HX_COMPANION_BIN") or pin.get("bin")
    if not binary:
        return Call(None, "", {}, error="no claude binary pinned in config/claude.json")

    token = root / "seed" / "token"
    if token.is_file():
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token.read_text().strip()
    env["CLAUDE_CONFIG_DIR"] = str(companion_home(root, config.id))
    env["DISABLE_AUTOUPDATER"] = "1"

    model = (config.companion or {}).get("model") or config.model
    argv = [
        binary, "-p", prompt,
        "--model", model,
        "--append-system-prompt-file", str(system_prompt_path(root, config.id)),
        "--output-format", "json",
        # The Companion interprets; it never acts. `*` removes every tool (docs/en/cli-reference).
        "--disallowedTools", "*",
        # Nothing to resume: every call is stateless (spec 10).
        "--no-session-persistence",
    ]
    if schema is not None:
        argv += ["--json-schema", json.dumps(schema)]

    try:
        result = subprocess.run(
            argv, input=payload, capture_output=True, text=True, check=False,
            cwd=str(root), env=env,
        )
    except OSError as exc:
        return Call(None, "", {}, error=f"could not run {binary}: {exc}")

    if result.returncode != 0:
        return Call(None, result.stdout, {}, error=f"exit {result.returncode}: {result.stderr.strip()[:400]}")

    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        return Call(None, result.stdout, {}, error="the result was not JSON")

    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    if text_result:
        # A digest is prose for the Partner, not a document hx validates.
        text = body.get("result")
        if isinstance(text, str) and text.strip():
            return Call({"text": text}, result.stdout, usage)
        return Call(None, result.stdout, usage, error="the digest call returned no text")

    structured = body.get("structured_output")
    if isinstance(structured, dict):
        return Call(structured, result.stdout, usage)

    text = body.get("result")
    if isinstance(text, str):
        # Strict: the Companion is asked for one JSON object and `--json-schema` constrains it.
        # A parser that strips fences would teach it that fences are acceptable, and hx would
        # never see the failure it needs to name on the retry.
        try:
            return Call(json.loads(text), result.stdout, usage)
        except json.JSONDecodeError as exc:
            return Call(
                None, result.stdout, usage,
                error=f"the result was not a bare JSON object ({exc}); "
                      f"it began {text.strip()[:60]!r}",
            )
    return Call(None, result.stdout, usage, error="no structured_output and no result")


# --- the loop -------------------------------------------------------------------------------


def open_streams(root: Path, item_id: str) -> list[str]:
    """Every stream this agent owns: main, plus one per subagent, open or closed."""
    found = [f"{item_id}-main"]
    for path in streams.subagent_streams(root, item_id):
        found.append(path.name.rsplit("-", 1)[0])
    return found


def pending(root: Path, item_id: str, stream: str) -> tuple[dict, list[dict]]:
    """The current state and the records past its cursor."""
    state = load_state(state_path(root, item_id, stream)) or empty()
    cursor = state.get("seq") or 0
    path = streams.stream_path(root, item_id, stream)
    records = [r for r in streams.iter_records(path) if (r.get("seq") or 0) > cursor]
    return state, records


def head_seq(root: Path, item_id: str, stream: str) -> int:
    return streams.last_seq(streams.stream_path(root, item_id, stream))


def is_caught_up(root: Path, item_id: str) -> bool:
    """True when every stream's state `seq` equals its log head — what `hx flush` waits for."""
    for stream in open_streams(root, item_id):
        state = load_state(state_path(root, item_id, stream)) or empty()
        if (state.get("seq") or 0) < head_seq(root, item_id, stream):
            return False
    return True


def log_problem(root: Path, item_id: str, message: str) -> None:
    from .hooks import log_error

    log_error(root, item_id, "companion", message)


def process_stream(root: Path, config: HarnessConfig, stream: str, *, env=None) -> dict | None:
    """One pass over one stream. Returns the state written, or None when nothing changed."""
    item_id = config.id
    state, records = pending(root, item_id, stream)
    if not records:
        return None

    payload = build_payload(root, item_id, stream, state, records)
    head = max((r.get("seq") or 0) for r in records)
    previous_seq = state.get("seq") or 0

    candidate = None
    failure = None
    for attempt in (1, 2):
        # One retry, with the failure named, then the prior state stands. A second bad answer
        # is not worth a third call: the records are still there and the next pass sees them.
        body = payload if attempt == 1 else f"{payload}\n{_retry_note(failure)}\n"
        call = call_model(root, config, body, env=env)
        if call.usage:
            record_usage(root, item_id, stream, call.usage)
        if call.error or call.state is None:
            failure = call.error or "no state returned"
            continue
        try:
            candidate = validate(call.state, previous_seq=previous_seq)
            break
        except InvalidState as exc:
            failure = str(exc)

    if candidate is None:
        # An invalid write keeps the previous state (spec 10, 13 M5).
        log_problem(
            root, item_id,
            f"{stream}: {failure}; retried once, still bad, keeping the prior state",
        )
        return None

    budget = (config.companion or {}).get("state_budget_tokens", 10000)
    candidate, evicted = evict(candidate, budget)
    if evicted:
        log_problem(root, item_id, f"{stream}: over budget, evicted {', '.join(evicted)}")

    # hx owns the cursor and the stamp, not the model: a Companion that forgets to move `seq`
    # would make hx re-feed the same records forever.
    candidate["seq"] = max(int(candidate.get("seq") or 0), head)
    candidate["prompt_version"] = prompt_version(root, config.role)
    candidate["ts"] = timestamps.now()

    store.atomic_write_json(state_path(root, item_id, stream), candidate)
    return candidate


def _retry_note(failure: str | None) -> str:
    """What the second call is told, so the model can see what it did wrong."""
    return (
        "\n# Your previous answer was rejected\n\n"
        f"{failure}\n\n"
        "Return one JSON object and nothing else: no prose, no markdown fences, no commentary."
    )


def record_usage(root: Path, item_id: str, stream: str, usage: dict) -> None:
    """Append the call's usage, so `hx metrics` can report cache reads (spec 05, 07.4)."""
    path = root / "state" / item_id / "companion-usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"ts": timestamps.now(), "stream": stream, "usage": usage}) + "\n")
        handle.flush()


def write_digest(root: Path, config: HarnessConfig, stream: str, *, env=None) -> Path | None:
    """The closed-stream digest the `subagent-result` hook hands to the parent (spec 10)."""
    state, _ = pending(root, config.id, stream)
    payload = build_payload(root, config.id, stream, state, [])
    call = call_model(
        root, config, payload, schema=None, prompt=DIGEST_PROMPT, text_result=True, env=env
    )
    if call.error or call.state is None:
        log_problem(root, config.id, f"{stream}: digest call failed ({call.error})")
        return None
    text = str(call.state.get("text") or "")
    if not text.strip():
        return None
    path = digest_path(root, config.id, stream)
    store.atomic_write_text(path, text.strip() + "\n")
    return path


def seam_is_due(root: Path, config: HarnessConfig, state: dict) -> bool:
    """Spec 10's seam policy, all four conditions, on the main stream only."""
    companion = config.companion or {}
    if not state.get("closed_steps"):
        return False
    if state.get("subagents_open"):
        return False

    tokens = streams.last_context_tokens(root, config.id)
    if tokens is None or tokens < companion.get("seam_min_context_tokens", 60000):
        return False

    interval = companion.get("seam_min_interval_s", 600)
    last = last_seam_ts(root, config.id)
    if last is not None and (time.time() - last) < interval:
        return False
    return True


def last_seam_ts(root: Path, item_id: str) -> float | None:
    """When the last `seam` record was appended to the main stream, as a unix time."""
    newest = None
    for record in streams.iter_records(streams.main_stream(root, item_id)):
        if record.get("event") == "seam":
            parsed = timestamps.parse(str(record.get("ts", "")))
            if parsed is not None:
                newest = parsed.timestamp()
    return newest


def pass_once(root: Path, config: HarnessConfig, *, env=None) -> dict:
    """One sweep over every stream. Returns what it did, for the loop and the tests."""
    from .hook_log import seam_marker

    item_id = config.id
    result: dict = {"streams": [], "digests": [], "seam": False}

    for stream in open_streams(root, item_id):
        written = process_stream(root, config, stream, env=env)
        if written is not None:
            result["streams"].append(stream)
        if stream.endswith("-main"):
            state = load_state(state_path(root, item_id, stream)) or empty()
            if seam_is_due(root, config, state):
                marker = seam_marker(root, item_id)
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
                result["seam"] = True

    # A closed stream gets its digest once, on the pass after it closed (spec 10).
    for path in streams.subagent_streams(root, item_id, state="closed"):
        stream = path.name.rsplit("-", 1)[0]
        digest = digest_path(root, item_id, stream)
        if digest.is_file() and digest.read_text().strip() not in ("", "_pending companion_"):
            continue
        if write_digest(root, config, stream, env=env):
            result["digests"].append(stream)
    return result


def config_for(root: Path, item_id: str) -> HarnessConfig:
    harness = root / "config" / item_id / "harness.json"
    if not harness.is_file():
        raise NotFound(f"{harness}: no config for {item_id}")
    return load_harness(harness, check_cross_file=False)


def run(root: Path, item_id: str, *, once: bool = False, env=None) -> int:
    """The loop of spec 10. No timeout: it waits for work and then does it."""
    config = config_for(root, item_id)
    compose_system_prompt(root, config)
    batch = (config.companion or {}).get("batch_records", 20)

    seen = {"turn": None, "flush": None}
    while True:
        result = pass_once(root, config, env=env)
        if once:
            print(json.dumps(result))
            return 0
        _wait_for_work(root, item_id, batch, seen)


def flush_marker(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / "companion-flush"


def _wait_for_work(root: Path, item_id: str, batch: int, seen: dict) -> None:
    """Wake on `batch_records` new records, a touched turn marker, or `hx flush` (spec 10)."""
    from .hook_stop import turn_marker

    while True:
        for stream in open_streams(root, item_id):
            state = load_state(state_path(root, item_id, stream)) or empty()
            if head_seq(root, item_id, stream) - (state.get("seq") or 0) >= batch:
                return
        for name, path in (("turn", turn_marker(root, item_id)), ("flush", flush_marker(root, item_id))):
            stamp = path.stat().st_mtime if path.exists() else None
            if stamp is not None and stamp != seen[name]:
                seen[name] = stamp
                return
        time.sleep(POLL_SECONDS)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx companion", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--once", action="store_true", help="one pass, then exit (tests, hx flush)")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return run(root, args.id, once=args.once, env=env)
