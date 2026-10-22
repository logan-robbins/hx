"""Immutable ledger context for workers and bounded Partner orchestration notes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import store, streams
from .config_harness import flavor_of, invariants_channel
from .errors import NotFound, Refused, ValidationError
from .ids import PARTNER


MAIN_SUFFIX = "-main"
SECTION_STATUS = "## Status"
SECTION_TASKS = "## Tasks"

#: Written where a section has nothing to show, so the agent sees the section exists and is
#: empty rather than wondering whether hx failed to compose it.
NONE_YET = "_none yet_"


def context_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "run" / item_id / f"{stream}.context.md"


def main_stream_name(item_id: str) -> str:
    return f"{item_id}{MAIN_SUFFIX}"


def is_main_stream(item_id: str, stream: str) -> bool:
    return stream == main_stream_name(item_id)


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _section(title: str, source: str | None, body: str) -> str:
    """One section, carrying the path it came from (goal build-3 item 1)."""
    head = f"## {title}" if source is None else f"## {title}\n\n_source: `{source}`_"
    return f"{head}\n\n{body.rstrip() or NONE_YET}\n"


def carries_invariants(root: Path, item_id: str) -> bool:
    """Whether this agent's subagent context files carry the invariants: where the parent gets
    them from the `context` hook (`INVARIANTS_CHANNEL` `hook`), which a subagent does not run."""
    return invariants_channel(flavor_of(root, item_id)) == "hook"


def invariants(root: Path) -> tuple[str | None, str]:
    """The global invariants, whole: one copy at `config/CLAUDE.md`, managed only by the
    Partner, reaching each agent exactly once (`config_harness.INVARIANTS_CHANNEL`)."""
    path = root / "config" / "CLAUDE.md"
    if not path.is_file():
        return _relative(root, path), ""
    return _relative(root, path), path.read_text().strip("\n")


def subagents(root: Path, item_id: str) -> tuple[str, str]:
    """What this agent's subagents are told, rendered at delivery from the one shared
    template, so nothing is copied per id."""
    from . import persona as persona_mod

    source, text = persona_mod.render_subagents(root, item_id)
    return source or f"templates/worker/SUBAGENTS.md", text


#: What a subagent gets in place of the goal. Spec 07.3 says "the subagent prompt", but the
#: `SubagentStart` payload does not carry one: its documented fields are `session_id`,
#: `hook_event_name`, `agent_id`, `agent_type`, `cwd` and `permission_mode`, and a live run on
#: 2026-09-20 confirmed there is nothing else to read. The subagent already has the parent's
#: instruction as its first message, so the section says where its task came from rather than
#: pretending to repeat it.
SUBAGENT_TASK = (
    "Your task is the message you were spawned with, which is already in this conversation.\n"
    "This file is the rest of what hx has for you.\n"
)




#: The tag vocabulary of the rendered step state. The reader of this section is an LLM
#: resuming a task after `/clear`, and it pays for every character of it at every boundary, so
#: the rendering is telegraphic: one line per entry, a leading tag instead of a heading, no
#: bold, no blank lines, no prose. The tags are the whole grammar and are fixed here,
#: `companion/BASE.md` (which tells the Companion what each field must contain) and the
#: hx-worker skill (which tells the agent how to read them).
STEP_STATE_TAGS = {
    "goal": "the task as actually pursued",
    "con": "a constraint that still holds",
    "dec": "decision <- why [evidence seqs]; do not re-decide",
    "open": "<id> an open step [evidence seqs]",
    "next": "<id> the one action that continues that step",
    "done": "<id> verified|UNVERIFIED <sha> outcome [evidence seqs]",
    "dead": "tried and abandoned; do not retry",
    "commit": "<sha> message",
    "dirty": "uncommitted path",
    "file": "<path> the fact taken from it; do not read it again",
    "fail": "the last failure, verbatim",
    "hypo": "the current theory about it",
    "block": "an impediment outside the task",
    "seq": "the stream cursor this state was written at",
}


def _line(value: object) -> str:
    """One line for a value that should have been a string; never a multi-line blob."""
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    return " ".join(text.split())


def _ev(entry: dict) -> str:
    """`[398,410]` — evidence seqs, compact: the agent follows them into the raw stream."""
    refs = entry.get("ev") or []
    return f" [{','.join(str(ref) for ref in refs)}]" if refs else ""


def render_step_state(state: dict) -> str:
    """The step state, rendered dense rather than dumped: one tagged line per fact (spec 07.2).

    Unknown keys are rendered too, so a Companion that grows a field is not silently dropped
    from the file the agent actually reads.
    """
    lines: list[str] = []

    if state.get("goal"):
        lines.append(f"goal: {_line(state['goal'])}")
    for item in state.get("constraints") or []:
        lines.append(f"con: {_line(item)}")
    for item in state.get("decisions") or []:
        if not isinstance(item, dict):
            lines.append(f"dec: {_line(item)}")
            continue
        why = f" <- {_line(item.get('why'))}" if item.get("why") else ""
        lines.append(f"dec: {_line(item.get('d') or '')}{why}{_ev(item)}")
    for item in state.get("open_steps") or []:
        if not isinstance(item, dict):
            lines.append(f"open: {_line(item)}")
            continue
        step = _line(item.get("id") or "?")
        lines.append(f"open {step}: {_line(item.get('intent') or '')}{_ev(item)}")
        # Kept on its own line and next to its step: after a seam this is the first thing the
        # agent acts on, and it is the one line that must never be scrolled past.
        if item.get("next"):
            lines.append(f"next {step}: {_line(item['next'])}")
    for item in state.get("closed_steps") or []:
        if not isinstance(item, dict):
            lines.append(f"done: {_line(item)}")
            continue
        # `verified` means a record proved it. The unverified case is shouted, because
        # trusting one is how the same work gets done twice or shipped broken.
        mark = "verified" if item.get("verified") else "UNVERIFIED"
        commit = f" {_line(item['commit'])}" if item.get("commit") else ""
        step = _line(item.get("id") or "?")
        lines.append(f"done {step} {mark}{commit}: {_line(item.get('outcome') or '')}{_ev(item)}")
    for item in state.get("dead_ends") or []:
        lines.append(f"dead: {_line(item)}")

    working_set = state.get("working_set") or {}
    for commit in working_set.get("commits") or []:
        if isinstance(commit, dict):
            lines.append(f"commit {_line(commit.get('sha') or '')}: {_line(commit.get('msg') or '')}")
        else:
            lines.append(f"commit: {_line(commit)}")
    for path in working_set.get("dirty") or []:
        lines.append(f"dirty: {_line(path)}")
    for entry in working_set.get("files") or []:
        # Files already read; the note is the fact, so the file is not read again.
        if isinstance(entry, dict):
            lines.append(f"file {_line(entry.get('path') or '')}: {_line(entry.get('note') or '')}")
        else:
            lines.append(f"file: {_line(entry)}")
    if working_set.get("last_failure"):
        lines.append(f"fail: {_line(working_set['last_failure'])}")
    if working_set.get("hypothesis"):
        lines.append(f"hypo: {_line(working_set['hypothesis'])}")
    for item in state.get("blockers") or []:
        lines.append(f"block: {_line(item)}")

    known = {
        "seq", "prompt_version", "ts", "goal", "constraints", "decisions", "open_steps",
        "closed_steps", "dead_ends", "working_set", "blockers", "subagents_open",
    }
    extra = {key: value for key, value in state.items() if key not in known and value}
    if extra:
        lines.append(f"other: {json.dumps(extra, separators=(',', ':'))}")

    # `ts` is hx's install stamp (companion.run_pass), so the reader knows how fresh this is.
    tail = f"seq {state['seq']}" if state.get("seq") is not None else ""
    if state.get("ts"):
        tail = f"{tail} ts {state['ts']}".strip()
    if tail:
        lines.append(tail)

    return "\n".join(lines).strip("\n")


def step_state(root: Path, item_id: str, stream: str) -> tuple[str | None, str]:
    """Written by the Companion (spec 07.2); absent until its first pass."""
    path = root / "state" / item_id / f"{stream}.json"
    if not path.is_file():
        return None, ""
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError:
        return _relative(root, path), "_the Companion's last write was not valid JSON_"
    if not isinstance(state, dict):
        return _relative(root, path), "_the Companion's last write was not an object_"
    if item_id == PARTNER and is_main_stream(item_id, stream):
        # Worker troubleshooting belongs to workers. The Partner companion lifts
        # only scheduling-relevant consequences into decisions or blockers.
        fields = {'seq', 'prompt_version', 'ts', 'goal', 'constraints', 'decisions',
                  'open_steps', 'blockers', 'subagents_open'}
        state = {key: value for key, value in state.items() if key in fields}
    return _relative(root, path), render_step_state(state)




def open_handles(root: Path, item_id: str) -> tuple[str | None, str]:
    """The subagents still running, by handle, so the agent knows what it is owed."""
    mapping_path = root / "run" / item_id / "subagents.json"
    mapping: dict = {}
    if mapping_path.is_file():
        try:
            loaded = json.loads(mapping_path.read_text())
            mapping = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            mapping = {}

    open_streams = streams.subagent_streams(root, item_id, state="open")
    if not open_streams:
        return (_relative(root, mapping_path) if mapping_path.is_file() else None), ""

    by_handle = {handle: agent_id for agent_id, handle in mapping.items()}
    lines = []
    for path in open_streams:
        handle = path.name.rsplit("-", 1)[0]
        agent_id = by_handle.get(handle)
        suffix = f" (agent `{agent_id}`)" if agent_id else ""
        lines.append(f"- `{handle}`{suffix} — `{_relative(root, path)}`")
    return _relative(root, mapping_path), "\n".join(lines)


def status_parts(
    root: Path, item_id: str, stream: str, *, fallback: str = ""
) -> list[tuple[str, str | None, str]]:
    """Partner orchestration step state and open research children."""
    parts = [("Step state", *step_state(root, item_id, stream))]
    parts.append(("Open subagent handles", *open_handles(root, item_id)))
    return parts


STATUS_NOTE = "_hx rewrites this section at every boundary (`hx compose`); do not edit it._"


def status_text(parts: list[tuple[str, str | None, str]]) -> str:
    """The body of `## Status`: the parts as `###` subsections, sources left out."""
    blocks = [STATUS_NOTE]
    for title, _source, body in parts:
        blocks.append(f"### {title}\n\n{body.rstrip() or NONE_YET}")
    return "\n\n".join(blocks)




def _trimmed(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return lines


def with_status(text: str, body: str) -> str:
    """`text` with its `## Status` section's content replaced by `body`; without one, the
    section goes right after `## Tasks`, or at the end when there is no `## Tasks`."""
    rest = text
    lines = rest.split("\n")
    block = [SECTION_STATUS, "", *body.strip("\n").split("\n")]
    bounds = section_bounds(rest, SECTION_STATUS) or section_bounds(rest, SECTION_TASKS)
    if bounds is None:
        head, tail = lines, []
    elif lines[bounds[0]].rstrip() == SECTION_STATUS:
        head, tail = lines[: bounds[0]], lines[bounds[1]:]
    else:
        head, tail = lines[: bounds[1]], lines[bounds[1]:]
    head = _trimmed(head)
    updated = (head + [""] if head else []) + block + ([""] + tail if tail else [""])
    return "\n".join(updated)


def write_status(root: Path, item_id: str, parts: list[tuple[str, str | None, str]]) -> Path | None:
    """Rewrite `## Status` in the agent's session-memory file (`status_file`); the file is
    left untouched when nothing changed. Returns the file, or None when there is none."""
    if item_id != PARTNER:
        raise Refused("worker checkpoints are immutable; compose a new ledger checkpoint")
    path = status_file(root, item_id)
    if path is None:
        return None
    text = path.read_text()
    updated = with_status(text, status_text(parts))
    if updated != text:
        store.atomic_write_text(path, updated)
    return path


#: The opening of a subagent's context file.
SUBAGENT_INTRO = ("This is the complete current context HX adds to your native task instructions. "
                  "It is already delivered here; no additional file read is required.")


def compose_text(
    root: Path, item_id: str, stream: str, *, subagent_prompt: str | None = None, env=None,
    status: list[tuple[str, str | None, str]] | None = None,
) -> str:
    """A subagent stream's context file, sections in order. `status` is the parts already
    computed (`status_parts`). The Partner main stream reads PARTNER.md."""
    if is_main_stream(item_id, stream):
        raise ValidationError(f"{stream}: the main stream has no context file; the `context` "
                              f"hook points {item_id} at PARTNER.md")
    parts = [f"# Context for {stream}", "", SUBAGENT_INTRO, ""]
    if carries_invariants(root, item_id):
        parts.append(_section("Invariants", *invariants(root)))
    parts.append(_section("Who your subagents are", *subagents(root, item_id)))
    source, text = task(root, item_id, subagent_prompt or "")
    parts.append(_section("Task", source, text))
    for title, source, body in status if status is not None else status_parts(
            root, item_id, stream, fallback=text):
        parts.append(_section(title, source, body))
    return "\n".join(parts).rstrip("\n") + "\n"


def compose(
    root: Path, item_id: str, stream: str | None = None, *, subagent_prompt: str | None = None, env=None
) -> Path | None:
    """Refresh Partner orchestration notes or its bounded research child context."""
    root = Path(root)
    stream = stream or main_stream_name(item_id)
    if item_id != PARTNER:
        raise Refused("worker context requires --run and an immutable checkpoint request")
    if not (root / "config" / item_id).is_dir():
        raise NotFound(f"{item_id}: missing harness configuration")
    if is_main_stream(item_id, stream):
        status = status_parts(root, item_id, stream, fallback=task(root, item_id)[1])
        return write_status(root, item_id, status)
    path = context_path(root, item_id, stream)
    store.atomic_write_text(
        path, compose_text(root, item_id, stream, subagent_prompt=subagent_prompt, env=env)
    )
    return path



def section_bounds(text: str, heading: str) -> tuple[int, int] | None:
    lines = text.split("\n")
    fence = None
    start = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
        if fence is None and line.rstrip() == heading and start is None:
            start = index
        elif start is not None and fence is None and line.startswith("## "):
            return start, index
    return (start, len(lines)) if start is not None else None


def task(root: Path, item_id: str, subagent_prompt: str | None = None) -> tuple[str | None, str]:
    if subagent_prompt is not None:
        return "spawn prompt", subagent_prompt or SUBAGENT_TASK
    if item_id == PARTNER:
        return "PARTNER.md", "Maintain the current directive and dependency scheduling decisions."
    from .task import task as assignment
    return "continuity ledger", assignment(root, item_id)


def status_file(root: Path, item_id: str) -> Path | None:
    if item_id == PARTNER:
        path = root / "PARTNER.md"
        return path if path.is_file() else None
    import sqlite3
    path = root / "state" / "continuity.sqlite"
    if not path.is_file():
        return None
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT c.packet_hash FROM runs r JOIN checkpoints c ON c.checkpoint_id=r.checkpoint_id AND c.run_id=r.run_id WHERE r.worker_id=? AND r.ended_at IS NULL", (item_id,)).fetchone()
        return root / "state" / "artifacts" / row[0] if row else None
    finally:
        connection.close()

# A compiled codex/meta assignment is about 8.3 KB, counted in bytes without a tokenizer;
# the default has to hold the system's own largest native delivery.
DEFAULT_MAX_TOKENS = 16000

def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx compose", add_help=True)
    parser.add_argument("id")
    parser.add_argument("stream", nargs="?", default=None, help="default: <id>-main")
    parser.add_argument("--run", help="compose an immutable packet for a planned ledger assignment")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--request", help="stable checkpoint request ID; required when issuing ledger context")
    action.add_argument("--checkpoint", help="read a retained immutable checkpoint")
    parser.add_argument("--ack", action="store_true", help="acknowledge the current --checkpoint after rehydration")
    parser.add_argument("--planned", action="store_true", help="require extraction/capture readiness before issuing")
    parser.add_argument("--record", action="append", default=[])
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--instructions", help="explicit resolved instruction file to include and charge once")
    parser.add_argument("--prompt-manifest", help="verify compiled context-channel instructions and include them once")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--optional-tokens", type=int, default=1000)
    parser.add_argument("--json", action="store_true", help="return checkpoint identity, artifact path, and coverage")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    ledger_options = args.request or args.checkpoint or args.ack or args.planned or args.record or args.require or args.instructions or args.prompt_manifest or args.json or args.max_tokens != DEFAULT_MAX_TOKENS or args.optional_tokens != 1000
    if not args.run and ledger_options:
        raise ValidationError("ledger context options require --run")
    if args.run:
        from . import context_packets
        from .caller import caller
        from .continuity_store import ContinuityStore, canonical
        if args.stream is not None or not (args.request or args.checkpoint) or (args.ack and not args.checkpoint):
            raise ValidationError("ledger context requires --request or --checkpoint, and scopes all run streams together")
        if args.instructions and args.prompt_manifest:
            raise ValidationError("use a prompt manifest or an explicit instruction file, not both")
        if args.checkpoint and (args.record or args.require or args.instructions or args.prompt_manifest or args.planned or args.max_tokens != DEFAULT_MAX_TOKENS or args.optional_tokens != 1000):
            raise ValidationError("checkpoint replay cannot change its frozen selection or budget")
        who = caller(env)
        if who not in {None, "partner", args.id}:
            raise Refused("worker may compose only its own assignment")
        with ContinuityStore(root) as ledger:
            row = ledger.db.execute("SELECT worker_id FROM runs WHERE run_id=?", (args.run,)).fetchone()
            if row is None or row[0] != args.id:
                raise Refused("context run does not belong to the named worker")
            if args.checkpoint:
                result = context_packets.acknowledge(ledger, args.checkpoint, args.run) if args.ack else context_packets.read(ledger, args.checkpoint, args.run)
            else:
                instructions = ""
                if args.instructions:
                    with Path(args.instructions).open("rb") as handle:
                        raw = handle.read(262145)
                    if len(raw) > 262144:
                        raise ValidationError("resolved instructions exceed the context input bound")
                    try:
                        instructions = raw.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise ValidationError("resolved instructions must be UTF-8") from exc
                result = context_packets.issue(ledger, args.run, request_id=args.request,
                    record_ids=tuple(args.record), required_ids=tuple(args.require),
                    mode="planned" if args.planned else "forced", instructions=instructions,
                    max_tokens=args.max_tokens, optional_tokens=args.optional_tokens, prompt_manifest=args.prompt_manifest)
            path = ledger.artifacts / result["packet_hash"]
            print(canonical({key: value for key, value in {**result, "path": str(path)}.items() if key != "text"}) if args.json or args.ack else path)
        return 0
    written = compose(root, args.id, args.stream, env=env)
    print(written if written is not None else f"{args.id}: no task in progress; no `## Status` to write")
    return 0
