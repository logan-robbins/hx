"""`hx compose <id> [<stream>]` — session memory at every boundary.

The main stream has no context file: its boundary read is the work item itself. hx keeps the
work item's `## Status` (`PARTNER.md`'s for the Partner) current — rewritten at every
main-stream compose, which runs at a boundary, so the agent is not writing the file — and the
`context` hook points the agent at it (`hx.persona.boundary`). The agent keeps `## Tasks`.
`## Status` holds, as `###` subsections (`status_parts`):

  Step state       — `state/<id>/<stream>.json`, rendered (written by the Companion)
  Memory episodes  — the closest episodes other agents left behind (`docs/memory.md`);
                     never the Partner's, none with `companion.memory_inject_k: 0`
  Open subagent handles — `run/<id>/subagents.json` and every `-open` stream

A subagent stream gets one context file, `run/<id>/<stream>.context.md`, which the
`SubagentStart` hook names (spec 02 "Single-file context"), sections in this order:

  Invariants       — `config/CLAUDE.md` whole, on the flavors whose invariants reach the
                     parent through the `context` hook (`config_harness.INVARIANTS_CHANNEL`
                     `hook`: Pi, Grok, Meta), which a subagent does not see; Claude and
                     Codex subagents load the same file their parent does
  Who your subagents are — the shared `templates/worker/SUBAGENTS.md` rendered with this
                     agent's identity (`hx.persona.render_subagents`; `templates/contributor/`
                     for Meta), or its own `config/<id>/SUBAGENTS.md`
  Task             — the spawn prompt
  Step state, Memory episodes, Open subagent handles — the stream's own status
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import store, streams
from .config_harness import flavor_of, invariants_channel
from .errors import NotFound, Refused, ValidationError
from .ids import PARTNER
from .workitems import (
    SECTION_GOAL,
    SECTION_STATUS,
    SECTION_TASKS,
    find_work_item,
    section_bounds,
    section_text,
    split_frontmatter_text,
)

MAIN_SUFFIX = "-main"

#: Written where a section has nothing to show, so the agent sees the section exists and is
#: empty rather than wondering whether hx failed to compose it.
NONE_YET = "_none yet_"


def context_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "run" / item_id / f"{stream}.context.md"


def main_stream_name(item_id: str) -> str:
    return f"{item_id}{MAIN_SUFFIX}"


def is_main_stream(item_id: str, stream: str) -> bool:
    return stream == main_stream_name(item_id)


def boundary_name(item_id: str) -> str:
    """The name of the file a boundary's one Read is of: `PARTNER.md`, else the work item."""
    return "PARTNER.md" if item_id == PARTNER else f"{item_id}-working.md"


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


def task(root: Path, item_id: str, subagent_prompt: str | None = None) -> tuple[str | None, str]:
    """The goal as dispatched, plus every addendum `hx resume` appended: what memory is asked
    about before the Companion has written a step state.

    For a subagent stream it is the spawn prompt instead: that *is* its task, and the parent's
    goal is not its business (spec 07.3).
    """
    if subagent_prompt is not None:
        if subagent_prompt.strip():
            return "the spawn prompt", subagent_prompt
        return None, SUBAGENT_TASK
    work_item = find_work_item(root, item_id)
    if work_item is not None:
        _, body = split_frontmatter_text(work_item.read_text())
        goal = section_text(body, SECTION_GOAL)
        if goal:
            return _relative(root, work_item), goal

    # Before the first dispatch there is no rendered Work Item; `tasks.json` still has the
    # goal once one has been recorded.
    from .tasks import load_tasks

    entry = load_tasks(root).get(item_id) or {}
    parts = [entry.get("goal") or ""]
    for addendum in entry.get("addenda") or []:
        parts.append(f"### Goal addendum {addendum.get('ts', '')}\n\n{addendum.get('text', '')}")
    text = "\n\n".join(part.strip("\n") for part in parts if part.strip())
    return ("tasks.json" if text else None), text


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

    # `ts` is hx's ingest stamp (companion.ingest), so the reader knows how fresh this is.
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


def memory_episodes(
    root: Path, item_id: str, stream: str, *, fallback: str = ""
) -> tuple[str | None, str] | None:
    """What other agents already worked out about this, from episode memory.

    Everything the Companions of this instance ever compacted lives in one searchable
    collection (`docs/memory.md`). The query is built from this stream's own step state — goal,
    what is next, the current hypothesis — falling back to the goal text before the Companion
    has written anything, and this agent's own id is excluded, because its own state is the
    section directly above.

    Returns `None` when the agent has `companion.memory_inject_k: 0`, which is how the section
    is turned off. Chroma being absent or broken is not an error here: the body says so in one
    line and the rest composes exactly as before.
    """
    from . import memory as memory_mod

    try:
        path = root / "state" / item_id / f"{stream}.json"
        state: dict = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text())
                state = loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError:
                state = {}
        if memory_mod.companion_settings(root, item_id)[0] <= 0:
            return None
        body = memory_mod.section_text(root, item_id, stream, state, fallback=fallback)
    except Exception as exc:  # noqa: BLE001 — the context file is composed whatever happens
        body = f"_memory unavailable: {exc}_"
    return _relative(root, memory_mod.chroma_dir(root)), body


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
    """`(title, source, body)` for each part of a stream's status: its step state, the
    closest memory episodes (never the Partner's, none with `memory_inject_k: 0`), and its
    open subagent handles. Computed once per compose: the episode lookup opens chroma."""
    parts = [("Step state", *step_state(root, item_id, stream))]
    if not (item_id == PARTNER and is_main_stream(item_id, stream)):
        injected = memory_episodes(root, item_id, stream, fallback=fallback)
        if injected is not None:
            parts.append(("Memory episodes", *injected))
    parts.append(("Open subagent handles", *open_handles(root, item_id)))
    return parts


STATUS_NOTE = "_hx rewrites this section at every boundary (`hx compose`); do not edit it._"


def status_text(parts: list[tuple[str, str | None, str]]) -> str:
    """The body of `## Status`: the parts as `###` subsections, sources left out."""
    blocks = [STATUS_NOTE]
    for title, _source, body in parts:
        blocks.append(f"### {title}\n\n{body.rstrip() or NONE_YET}")
    return "\n\n".join(blocks)


def status_file(root: Path, item_id: str) -> Path | None:
    """Where the agent's session memory lives: `PARTNER.md` for the Partner, else the
    `-working` work item. None when there is none (no task in progress, or a completed item,
    which is a record and stays as the agent left it)."""
    if item_id == PARTNER:
        path = root / "PARTNER.md"
        return path if path.is_file() else None
    path = find_work_item(root, item_id)
    if path is None or not path.name.endswith("-working.md"):
        return None
    return path


def _trimmed(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return lines


def with_status(text: str, body: str) -> str:
    """`text` with its `## Status` section's content replaced by `body`; without one, the
    section goes right after `## Tasks`, or at the end when there is no `## Tasks`."""
    # A work item opens with frontmatter; PARTNER.md does not.
    front, rest = split_frontmatter_text(text) if text.startswith("---") else ("", text)
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
    return front + "\n".join(updated)


def write_status(root: Path, item_id: str, parts: list[tuple[str, str | None, str]]) -> Path | None:
    """Rewrite `## Status` in the agent's session-memory file (`status_file`); the file is
    left untouched when nothing changed. Returns the file, or None when there is none."""
    path = status_file(root, item_id)
    if path is None:
        return None
    text = path.read_text()
    updated = with_status(text, status_text(parts))
    if updated != text:
        store.atomic_write_text(path, updated)
    return path


#: The opening of a subagent's context file.
SUBAGENT_INTRO = ("This file is everything hx has for you that you were not already given at launch. "
                  "Read it once; you do not need to search for anything it contains.")


def compose_text(
    root: Path, item_id: str, stream: str, *, subagent_prompt: str | None = None, env=None,
    status: list[tuple[str, str | None, str]] | None = None,
) -> str:
    """A subagent stream's context file, sections in order. `status` is the parts already
    computed (`status_parts`). The main stream has none: its boundary read is the work item."""
    if is_main_stream(item_id, stream):
        raise ValidationError(f"{stream}: the main stream has no context file; the `context` "
                              f"hook points {item_id} at its work item (`hx persona {item_id}`)")
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
    """The main stream: rewrite `## Status` in the work item (`PARTNER.md`) and return that
    file, or None when there is none (no task in progress). A subagent stream: write its
    context file and return its path (spec 08 `hx compose`)."""
    root = Path(root)
    stream = stream or main_stream_name(item_id)
    if not (root / "config" / item_id).is_dir() and find_work_item(root, item_id) is None:
        raise NotFound(f"{item_id}: no work item and no config/{item_id}/; unknown id")
    if is_main_stream(item_id, stream):
        status = status_parts(root, item_id, stream, fallback=task(root, item_id)[1])
        return write_status(root, item_id, status)
    path = context_path(root, item_id, stream)
    store.atomic_write_text(
        path, compose_text(root, item_id, stream, subagent_prompt=subagent_prompt, env=env)
    )
    return path


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
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--optional-tokens", type=int, default=1000)
    parser.add_argument("--json", action="store_true", help="return checkpoint identity, artifact path, and coverage")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    ledger_options = args.request or args.checkpoint or args.ack or args.planned or args.record or args.require or args.instructions or args.prompt_manifest or args.json or args.max_tokens != 8000 or args.optional_tokens != 1000
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
        if args.checkpoint and (args.record or args.require or args.instructions or args.prompt_manifest or args.planned or args.max_tokens != 8000 or args.optional_tokens != 1000):
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
