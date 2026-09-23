"""`hx compose <id> [<stream>]` — the single file the agent is handed at every boundary.

Spec 02 "Single-file context": everything the agent needs that is not already in its system
prompt, composed by hx into one file per stream with a fixed section schema, always current on
disk. The hook output is only the path, so rehydration costs exactly one Read, never a search
and never a second file.

Sections, in the order spec 07.3 fixes them:

  1. Memory        — `config/<id>/AGENTS.md` below `## UPDATES BELOW ONLY` (main stream),
                     or `config/<id>/SUBAGENTS.md` whole (subagent streams)
  2. Task          — the `## Goal` section of the Work Item, with the addenda `hx resume`
                     appended to it
  3. Tasks         — the work item's `## Tasks` section (main stream only)
  4. Step state    — `state/<id>/<stream>.json`, rendered
  4b. Memory episodes — the closest episodes other agents left behind (`docs/memory.md`),
                     omitted when `companion.memory_inject_k` is 0
  5. Open handles  — `run/<id>/subagents.json` and every `-open` stream

The persona is never here: it is in the system prompt, via `--append-system-prompt-file`
(spec 02 Identity, 11). For `partner` the file also carries `PARTNER.md` and the `hx board`
text, which is what it supervises from (spec 09.1). No size cap applies, because nothing is
injected: the agent reads the file with a tool call (spec 02).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import board as board_mod, store, streams
from .errors import NotFound
from .ids import PARTNER
from .workitems import (
    SECTION_GOAL,
    SECTION_TASKS,
    find_work_item,
    section_text,
    split_frontmatter_text,
)

HEADER = "## UPDATES BELOW ONLY"
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


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _section(title: str, source: str | None, body: str) -> str:
    """One section, carrying the path it came from (goal build-3 item 1)."""
    head = f"## {title}" if source is None else f"## {title}\n\n_source: `{source}`_"
    return f"{head}\n\n{body.rstrip() or NONE_YET}\n"


def memory(root: Path, item_id: str, stream: str) -> tuple[str, str]:
    """Section 1. The agent's own long-term memory, or the subagent identity file."""
    if is_main_stream(item_id, stream):
        path = root / "config" / item_id / "AGENTS.md"
        if not path.is_file():
            return _relative(root, path), ""
        text = path.read_text()
        if HEADER not in text:
            # Without the header hx cannot tell memory from persona, and the persona must
            # never reach this file (spec 02 Identity). Nothing is safer than nothing.
            return _relative(root, path), ""
        return _relative(root, path), text.split(HEADER, 1)[1].strip("\n")

    path = root / "config" / item_id / "SUBAGENTS.md"
    if not path.is_file():
        return _relative(root, path), ""
    return _relative(root, path), path.read_text().strip("\n")


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
    """Section 2. The goal as dispatched, plus every addendum `hx resume` appended.

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


def tasks_section(root: Path, item_id: str) -> tuple[str | None, str]:
    """Section 3. The agent's own running task list — what it gets back after a seam."""
    work_item = find_work_item(root, item_id)
    if work_item is None:
        return None, ""
    _, body = split_frontmatter_text(work_item.read_text())
    return _relative(root, work_item), section_text(body, SECTION_TASKS) or ""


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
    """Section 4, rendered dense rather than dumped: one tagged line per fact (spec 07.2).

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
    """Section 4. Written by the Companion (spec 07.2); absent until M5."""
    path = root / "state" / item_id / f"{stream}.json"
    if not path.is_file():
        return None, ""
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError:
        return _relative(root, path), "_the Companion's last write was not valid JSON_"
    if not isinstance(state, dict):
        return _relative(root, path), "_the Companion's last write was not an object_"
    return _relative(root, path), render_step_state(state)


def memory_episodes(
    root: Path, item_id: str, stream: str, *, fallback: str = ""
) -> tuple[str | None, str] | None:
    """Section 4b. What other agents already worked out about this, from episode memory.

    Everything the Companions of this instance ever compacted lives in one searchable
    collection (`docs/memory.md`). The query is built from this stream's own step state — goal,
    what is next, the current hypothesis — falling back to the goal text before the Companion
    has written anything, and this agent's own id is excluded, because its own state is the
    section directly above.

    Returns `None` when the agent has `companion.memory_inject_k: 0`, which is how the section
    is turned off. Chroma being absent or broken is not an error here: the body says so in one
    line and the rest of the file composes exactly as before.
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
    """Section 5. The subagents still running, by handle, so the agent knows what it is owed."""
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


#: The handover is a resume pointer, not an archive: PARTNER.md grows all day, so its
#: append-only sections ride bounded while identity, rules, digest, fleet, pods and open
#: questions ride whole. Tails win (newest last) except Notes, whose curated constants sit
#: at the head. Counts are data rows kept, not lines.
_PARTNER_TAIL_ROWS = {"## Decisions made": 15, "## Completed work": 5}
_PARTNER_HEAD_LINES = {"## Notes": 60}


def _is_separator(line: str) -> bool:
    return line.startswith("|") and "-" in line and set(line.strip()) <= {"|", "-", ":", " "}


def _tail_table(body: list[str], keep: int) -> list[str]:
    sep = next((i for i, line in enumerate(body) if _is_separator(line)), None)
    head = body[: sep + 1] if sep is not None else []
    rows = [line for line in body[len(head):] if line.startswith("|")]
    if len(rows) <= keep:
        return body
    return head + rows[-keep:] + ["", f"_{len(rows) - keep} older rows omitted — full text in PARTNER.md._"]


def _head_lines(body: list[str], keep: int) -> list[str]:
    if len(body) <= keep:
        return body
    return body[:keep] + ["", f"_{len(body) - keep} further lines omitted — full text in PARTNER.md._"]


def _bound_partner_md(text: str) -> str:
    """PARTNER.md with the append-only sections bounded (spec 07.3)."""
    head: list[str] = []
    chunks: list[tuple[str, list[str]]] = []
    header: str | None = None
    body: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if header is None:
                head = body
            else:
                chunks.append((header, body))
            header, body = line.strip(), []
        else:
            body.append(line)
    if header is None:
        return text
    chunks.append((header, body))
    out = head
    for name, section in chunks:
        if name in _PARTNER_TAIL_ROWS:
            section = _tail_table(section, _PARTNER_TAIL_ROWS[name])
        elif name in _PARTNER_HEAD_LINES:
            section = _head_lines(section, _PARTNER_HEAD_LINES[name])
        out.append(name)
        out.extend(section)
    return "\n".join(out)


def compose_text(
    root: Path, item_id: str, stream: str, *, subagent_prompt: str | None = None, env=None
) -> str:
    """The whole context file, sections in the order spec 07.3 fixes."""
    main = is_main_stream(item_id, stream)
    parts = [
        f"# Context for {stream}",
        "",
        "This file is everything hx has for you that is not already in your system prompt. "
        "Read it once; you do not need to search for anything it contains.",
        "",
    ]

    source, text = memory(root, item_id, stream)
    parts.append(_section("Memory" if main else "Who your subagents are", source, text))

    source, text = task(root, item_id, None if main else (subagent_prompt or ""))
    parts.append(_section("Task", source, text))
    # What memory is asked about before the Companion has written a step state to ask from.
    goal_text = text

    if main:
        source, text = tasks_section(root, item_id)
        parts.append(_section("Tasks", source, text))

    source, text = step_state(root, item_id, stream)
    parts.append(_section("Step state", source, text))

    injected = memory_episodes(root, item_id, stream, fallback=goal_text)
    if injected is not None:
        parts.append(_section("Memory episodes", injected[0], injected[1]))

    source, text = open_handles(root, item_id)
    parts.append(_section("Open subagent handles", source, text))

    if item_id == PARTNER and main:
        partner_md = root / "PARTNER.md"
        parts.append(
            _section(
                "PARTNER.md",
                _relative(root, partner_md),
                _bound_partner_md(partner_md.read_text()) if partner_md.is_file() else "",
            )
        )
        try:
            rendered = board_mod.render_text(board_mod.collect(root, env=env))
        except Exception as exc:  # the board must never stop the Partner from starting
            rendered = f"_hx board failed: {exc}_"
        parts.append(_section("Board", "hx board", f"```\n{rendered}\n```" if rendered else ""))

    return "\n".join(parts).rstrip("\n") + "\n"


def compose(
    root: Path, item_id: str, stream: str | None = None, *, subagent_prompt: str | None = None, env=None
) -> Path:
    """Write the context file and return its path (spec 08 `hx compose`)."""
    root = Path(root)
    stream = stream or main_stream_name(item_id)
    if not (root / "config" / item_id).is_dir() and find_work_item(root, item_id) is None:
        raise NotFound(f"{item_id}: no work item and no config/{item_id}/; unknown id")
    path = context_path(root, item_id, stream)
    store.atomic_write_text(
        path, compose_text(root, item_id, stream, subagent_prompt=subagent_prompt, env=env)
    )
    return path


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx compose", add_help=True)
    parser.add_argument("id")
    parser.add_argument("stream", nargs="?", default=None, help="default: <id>-main")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(compose(root, args.id, args.stream, env=env))
    return 0
