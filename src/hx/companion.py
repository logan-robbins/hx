"""`hx companion <id>` and `hx wake companion` — the Companion as a tmux session (spec 10).

Every model call in hx is a Claude Code session in tmux, operated by pasting. There is no
`claude -p`, no headless call, no API client (spec 02 "Model calls";
`tests/guard/test_no_headless.py` enforces it).

The Companion of `<id>` runs in window `<id>:companion` with its own home, launched by
`start.sh <id> --companion`. hx drives it one pass at a time:

  1. `hx wake companion <id> <stream>` writes `run/<id>/companion/<stream>.pass.md`, naming
     the state file, the log file, the first new `seq`, and where to write the answer.
  2. It pastes `/clear` — which makes every pass stateless, the system prompt being the
     cached prefix — and then the fixed pointer to the pass file. Nothing else is ever
     pasted, and no task text is ever a command-line argument.
  3. The Companion reads those files with its Read tool and writes `out.json` with Write.
  4. Its own `stop` hook validates that file against the 07.2 schema, stamps it, and moves it
     to `state/<id>/<stream>.json`. On a validation failure it rewrites the pass with
     `retry_reason` and re-wakes once; a second failure keeps the prior state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

from . import goal as goal_mod, store, streams, timestamps
from .config_harness import HarnessConfig, load_harness
from .errors import HxError, NotFound
from .stepstate import InvalidState, empty, evict, load as load_state, validate

#: Spec 10, verbatim: the only thing hx ever pastes into a Companion pane besides `/clear`.
POINTER = "Companion pass: read {path} and do what it says."

#: The nine keys of CONTRACTS.md plus handoff/gtm-to-build.md gtm-8. Every key is written even
#: when its value is empty: `companion/BASE.md` evaluates the seam policy from this file alone,
#: and a Companion that finds a key missing has to guess whether it means "never" or "you
#: forgot". An empty value is unambiguous, and the texts define what each one means.
PASS_TEMPLATE = """# Companion pass
stream: {stream}
state: {state}
log: {log}
from_seq: {from_seq}
write: {write}
retry_reason: {retry_reason}
context_tokens: {context_tokens}
last_seam_ts: {last_seam_ts}
open_subagents: {open_subagents}
"""

#: How many times hx re-wakes a Companion for the same pass before giving up (spec 10).
MAX_ATTEMPTS = 2


def companion_dir(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / "companion"


def companion_home(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / "companion-home"


def system_prompt_path(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / "companion-system.md"


def pass_path(root: Path, item_id: str, stream: str) -> Path:
    return companion_dir(root, item_id) / f"{stream}.pass.md"


def out_path(root: Path, item_id: str, stream: str) -> Path:
    return companion_dir(root, item_id) / f"{stream}.out.json"


def state_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "state" / item_id / f"{stream}.json"


def digest_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "state" / item_id / f"{stream}.digest.md"


def attempts_path(root: Path, item_id: str, stream: str) -> Path:
    return companion_dir(root, item_id) / f"{stream}.attempts"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else ""


def prompt_version(root: Path, role: str) -> dict:
    """The shas of `BASE.md` and the role file — what `hx metrics` compares across (spec 07.4)."""
    return {
        "base": _sha(root / "companion" / "BASE.md"),
        "role": _sha(root / "companion" / "roles" / f"{role}.md"),
    }


def config_for(root: Path, item_id: str) -> HarnessConfig:
    harness = root / "config" / item_id / "harness.json"
    if not harness.is_file():
        raise NotFound(f"{harness}: no config for {item_id}")
    return load_harness(harness, check_cross_file=False)


def compose_system_prompt(root: Path, config: HarnessConfig) -> Path:
    """`BASE.md` + the role file + the harness facts, composed once at start (spec 10, 05).

    This is the cached prefix: identical on every pass for this agent, and shared as far as
    the role file with every other Companion on this model.
    """
    base = root / "companion" / "BASE.md"
    role = root / "companion" / "roles" / f"{config.role}.md"
    for path in (base, role):
        if not path.is_file():
            raise NotFound(f"{path}: missing; the Companion's system prompt is composed from it (spec 10)")

    companion = config.companion or {}
    budget = companion.get("state_budget_tokens", 10000)
    facts = "\n".join([
        "## This agent",
        "",
        f"- id: `{config.id}`",
        f"- pod: `{config.pod}`",
        f"- role: `{config.role}`",
        f"- model the agent runs on: `{config.model}`, effort `{config.effort}`",
        f"- state budget: {budget} tokens (about {budget * 4} characters of JSON)",
        f"- seam policy: not before {companion.get('seam_min_context_tokens', 60000)} context "
        f"tokens, and not more often than every {companion.get('seam_min_interval_s', 600)}s",
        "",
        "You read no configuration at runtime. Everything you need is above, and each pass "
        "tells you exactly which files to read and where to write.",
        "",
    ])
    target = system_prompt_path(root, config.id)
    store.atomic_write_text(target, f"{base.read_text()}\n\n{role.read_text()}\n\n{facts}")
    return target


# --- the session ------------------------------------------------------------------------------


def window(item_id: str) -> str:
    return f"={item_id}:companion"


def is_running(item_id: str, env=None) -> bool:
    from . import tmux

    result = subprocess.run(
        [*tmux.tmux_command(env), "list-windows", "-t", f"={item_id}", "-F", "#{window_name}"],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0 and "companion" in result.stdout.split()


def launch(root: Path, item_id: str, *, env=None) -> bool:
    """Start the Companion session. Idempotent: a live window is left alone (spec 10)."""
    config = config_for(root, item_id)
    compose_system_prompt(root, config)
    companion_dir(root, item_id).mkdir(parents=True, exist_ok=True)
    if is_running(item_id, env):
        return False

    from .lifecycle import run_adapter

    result = run_adapter(root, "start.sh", item_id, env, extra=["--companion"])
    if result.returncode != 0:
        raise HxError(
            f"companion {item_id}: start.sh --companion failed:\n{result.stdout}{result.stderr}"
        )

    # Wait for its pane before returning. A wake into a pane that has not drawn yet defers,
    # and for the very first pass there is no later turn to pick the deferral up — the
    # Companion's own `stop` hook is what does that, and it has not run once yet.
    wait_until_ready(item_id, env=env)
    return True


def wait_until_ready(item_id: str, *, env=None) -> None:
    """Block until the Companion pane is at its prompt. No timeout, as everywhere in hx."""
    session = f"{item_id}:companion"
    while True:
        pane = goal_mod.capture_pane(session, env)
        if pane is not None and goal_mod.pane_is_idle(pane):
            return
        time.sleep(0.05)


# --- one pass ----------------------------------------------------------------------------------


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
    """True when every stream's state `seq` equals its log head — what `hx flush` waits for."""
    return all(
        cursor(root, item_id, stream) >= head_seq(root, item_id, stream)
        for stream in open_streams(root, item_id)
    )


def open_subagent_handles(root: Path, item_id: str) -> list[str]:
    return [
        path.name.rsplit("-", 1)[0].removeprefix(f"{item_id}-")
        for path in streams.subagent_streams(root, item_id, state="open")
    ]


def write_pass(root: Path, item_id: str, stream: str, *, retry_reason: str = "") -> Path:
    """The pass file: the only thing that tells the Companion what this turn is about.

    The three seam-policy values are hx's to supply, so the Companion never derives anything
    or looks outside what it was handed (handoff/gtm-to-build.md gtm-8). An empty
    `last_seam_ts` means no seam has been taken yet, which **satisfies** the interval
    condition: an instance that has never seamed should take its first at the first quiet
    step close, not be blocked until one has somehow already happened.
    """
    state = state_path(root, item_id, stream)
    is_main = stream == f"{item_id}-main"
    last_seam = last_seam_ts(root, item_id)

    path = pass_path(root, item_id, stream)
    store.atomic_write_text(path, PASS_TEMPLATE.format(
        stream=stream,
        state=str(state) if state.is_file() else "(none yet: this is the first pass)",
        log=str(streams.stream_path(root, item_id, stream)),
        from_seq=cursor(root, item_id, stream),
        write=str(out_path(root, item_id, stream)),
        retry_reason=retry_reason,
        context_tokens=(streams.last_context_tokens(root, item_id) or 0) if is_main else "",
        last_seam_ts=timestamps.from_unix(last_seam) if last_seam is not None else "",
        open_subagents=",".join(open_subagent_handles(root, item_id)) if is_main else "",
    ))
    return path


def wake(root: Path, item_id: str, stream: str, *, retry_reason: str = "", env=None) -> str:
    """Write the pass, clear the Companion's conversation, and point it at the file.

    `/clear` first, so every pass is stateless and the system prompt is the whole of the
    cached prefix (spec 10). Returns `pasted` or `pending`.

    `pending` means the pane was mid-turn, so the pass file stays on disk and the next wake
    — any trigger, or `hx flush` — delivers it. That is the only delivery mechanism there is.
    """
    path = write_pass(root, item_id, stream, retry_reason=retry_reason)
    out_path(root, item_id, stream).unlink(missing_ok=True)

    session = f"{item_id}:companion"
    pane = goal_mod.capture_pane(session, env)
    if pane is None:
        raise NotFound(
            f"{item_id}: no Companion pane; `hx companion {item_id}` starts it (spec 10)"
        )
    if not goal_mod.pane_is_idle(pane):
        return "pending"

    try:
        goal_mod.paste(session, "/clear", env)
        goal_mod.paste(session, POINTER.format(path=path), env)
    except subprocess.CalledProcessError as exc:
        # The window went away between the pane check and the paste (live rehearsal
        # 2026-09-21: a Companion exiting under the caller's feet took `hx complete` down with
        # it). The pass file stays on disk for the next wake; the caller degrades like any
        # other missing Companion.
        raise NotFound(f"{item_id}: the Companion pane vanished mid-wake ({exc})") from exc
    return "pasted"


def ingest(root: Path, item_id: str, stream: str, *, env=None) -> dict | None:
    """Validate, stamp and install what the Companion wrote. Called from its `stop` hook.

    Returns the state written, or None when the pass produced nothing usable. A failure
    rewrites the pass with `retry_reason` and re-wakes once; a second failure leaves the
    prior state standing, which is the rule that matters: a corrupted step state is worse
    than a stale one (spec 10).
    """
    out = out_path(root, item_id, stream)
    if not out.is_file():
        return None

    config = config_for(root, item_id)
    previous = cursor(root, item_id, stream)
    head = head_seq(root, item_id, stream)

    failure = None
    candidate = None
    try:
        candidate = validate(json.loads(out.read_text()), previous_seq=previous)
    except json.JSONDecodeError as exc:
        failure = f"{out.name} is not valid JSON: {exc}"
    except InvalidState as exc:
        failure = str(exc)

    if failure is not None:
        out.unlink(missing_ok=True)
        if _bump_attempts(root, item_id, stream) < MAX_ATTEMPTS:
            # Write the pass with the reason and return. The next wake — any trigger, or
            # `hx flush` — delivers whatever pass file is sitting there. One mechanism.
            log_problem(root, item_id, f"{stream}: {failure}; pass rewritten with the reason")
            write_pass(root, item_id, stream, retry_reason=failure)
        else:
            log_problem(
                root, item_id,
                f"{stream}: {failure}; retried once, still bad, keeping the prior state",
            )
            _clear_pass(root, item_id, stream)
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
    out.unlink(missing_ok=True)
    _clear_pass(root, item_id, stream)

    # Each installed state is one episode chunk. `enqueue_quietly` only writes a small JSON
    # file to the memory queue — no chromadb, no lock — and swallows anything that goes wrong
    # (docs/memory.md): a memory failure must never cost the agent a pass.
    from . import memory as memory_mod

    memory_mod.enqueue_quietly(
        root, item_id, stream, "pass", candidate, role=config.role, pod=config.pod
    )

    if stream == f"{item_id}-main" and seam_is_due(root, config, candidate):
        from .hook_log import seam_marker

        marker = seam_marker(root, item_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    return candidate


def _clear_pass(root: Path, item_id: str, stream: str) -> None:
    pass_path(root, item_id, stream).unlink(missing_ok=True)
    attempts_path(root, item_id, stream).unlink(missing_ok=True)


def _bump_attempts(root: Path, item_id: str, stream: str) -> int:
    path = attempts_path(root, item_id, stream)
    count = 0
    if path.is_file():
        try:
            count = int(path.read_text().strip() or 0)
        except ValueError:
            count = 0
    count += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(count))
    return count


def log_problem(root: Path, item_id: str, message: str) -> None:
    from .hooks import log_error

    log_error(root, item_id, "companion", message)


# --- the seam policy ------------------------------------------------------------------------------


def last_seam_ts(root: Path, item_id: str) -> float | None:
    newest = None
    for record in streams.iter_records(streams.main_stream(root, item_id)):
        if record.get("event") == "seam":
            parsed = timestamps.parse(str(record.get("ts", "")))
            if parsed is not None:
                newest = parsed.timestamp()
    return newest


def seam_is_due(root: Path, config: HarnessConfig, state: dict) -> bool:
    """Spec 10's seam policy, all four conditions, on the main stream only."""
    companion = config.companion or {}
    if not state.get("closed_steps") or state.get("subagents_open"):
        return False

    tokens = streams.last_context_tokens(root, config.id)
    if tokens is None or tokens < companion.get("seam_min_context_tokens", 60000):
        return False

    last = last_seam_ts(root, config.id)
    if last is not None and (time.time() - last) < companion.get("seam_min_interval_s", 600):
        return False
    return True


# --- wake triggers ---------------------------------------------------------------------------------


def streams_needing_a_pass(root: Path, item_id: str, batch: int) -> list[str]:
    """Streams with at least `batch_records` new records, plus any closed one with no digest."""
    due = []
    for stream in open_streams(root, item_id):
        if head_seq(root, item_id, stream) - cursor(root, item_id, stream) >= batch:
            due.append(stream)
    return due


def wake_due(root: Path, item_id: str, *, force: bool = False, env=None) -> list[str]:
    """Wake the Companion for every stream that needs a pass. Called from the agent's hooks."""
    try:
        config = config_for(root, item_id)
    except NotFound:
        return []
    batch = 1 if force else (config.companion or {}).get("batch_records", 20)
    woken = []
    due = set(streams_needing_a_pass(root, item_id, batch))
    # A pass file already on disk is one the Companion has not taken yet — a retry, or one
    # written while it was busy. Delivering it comes first.
    waiting = {p.name.removesuffix(".pass.md") for p in companion_dir(root, item_id).glob("*.pass.md")}
    for stream in sorted(due | waiting):
        if out_path(root, item_id, stream).is_file():
            continue  # it is mid-pass on this stream
        reason = ""
        existing = pass_path(root, item_id, stream)
        if existing.is_file():
            for line in existing.read_text().splitlines():
                if line.startswith("retry_reason:"):
                    reason = line.split(":", 1)[1].strip()
        try:
            wake(root, item_id, stream, retry_reason=reason, env=env)
            woken.append(stream)
        except NotFound:
            return woken  # no Companion pane; nothing to wake
    return woken


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx companion", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--wake", metavar="STREAM", default=None,
                        help="wake the Companion for one stream")
    parser.add_argument("--ingest", metavar="STREAM", default=None,
                        help="validate and install what it wrote (its stop hook does this)")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.ingest:
        state = ingest(root, args.id, args.ingest, env=env)
        print(f"HX-COMPANION {args.id} {args.ingest} "
              f"{'installed seq ' + str(state['seq']) if state else 'nothing'}")
        return 0
    if args.wake:
        outcome = wake(root, args.id, args.wake, env=env)
        print(f"HX-COMPANION {args.id} {args.wake} {outcome}")
        return 0

    started = launch(root, args.id, env=env)
    print(f"HX-COMPANION {args.id} {'started' if started else 'already running'}")
    return 0
