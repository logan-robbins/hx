"""`hx compose <id> [<stream>]` — the single file the agent is handed at every boundary.

Spec 02 "Single-file context": everything the agent needs that is not already in its system
prompt, composed by hx into one file per stream with a fixed section schema, always current on
disk. The hook output is only the path, so rehydration costs exactly one Read, never a search
and never a second file.

Sections, in the order spec 07.3 fixes them:

  1. Memory        — `config/<id>/AGENTS.md` below `## UPDATES BELOW ONLY` (main stream),
                     or `config/<id>/SUBAGENTS.md` whole (subagent streams)
  2. Task          — the `## Order` section of the work item, with the addenda `hx resume`
                     appended to it
  3. Tasks         — the work item's `## Tasks` section (main stream only)
  4. Step state    — `state/<id>/<stream>.json`, rendered
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
    SECTION_ORDER,
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


def task(root: Path, item_id: str, subagent_prompt: str | None = None) -> tuple[str | None, str]:
    """Section 2. The order as dispatched, plus every addendum `hx resume` appended.

    For a subagent stream it is the spawn prompt instead: that *is* its task, and the parent's
    order is not its business (spec 07.3).
    """
    if subagent_prompt is not None:
        return ("the spawn prompt" if subagent_prompt.strip() else None), subagent_prompt
    work_item = find_work_item(root, item_id)
    if work_item is not None:
        _, body = split_frontmatter_text(work_item.read_text())
        order = section_text(body, SECTION_ORDER)
        if order:
            return _relative(root, work_item), order

    # Before the first dispatch there is no rendered work item; `tasks.json` still has the
    # order once one has been recorded.
    from .tasks import load_tasks

    entry = load_tasks(root).get(item_id) or {}
    parts = [entry.get("order") or ""]
    for addendum in entry.get("addenda") or []:
        parts.append(f"### Order addendum {addendum.get('ts', '')}\n\n{addendum.get('text', '')}")
    text = "\n\n".join(part.strip("\n") for part in parts if part.strip())
    return ("tasks.json" if text else None), text


def tasks_section(root: Path, item_id: str) -> tuple[str | None, str]:
    """Section 3. The agent's own running task list — what it gets back after a seam."""
    work_item = find_work_item(root, item_id)
    if work_item is None:
        return None, ""
    _, body = split_frontmatter_text(work_item.read_text())
    return _relative(root, work_item), section_text(body, SECTION_TASKS) or ""


def render_step_state(state: dict) -> str:
    """Section 4, rendered rather than dumped: the Companion's state as readable markdown.

    The schema is spec 07.2. Unknown keys are rendered too, so a Companion that grows a field
    is not silently dropped from the file the agent actually reads.
    """
    lines: list[str] = []

    def bullet(text: str, indent: int = 0) -> None:
        lines.append(f"{'  ' * indent}- {text}")

    def evidence(entry: dict) -> str:
        refs = entry.get("ev") or []
        return f" _(ev {', '.join(str(r) for r in refs)})_" if refs else ""

    if state.get("goal"):
        lines += [f"**Goal:** {state['goal']}", ""]
    if state.get("constraints"):
        lines.append("**Constraints**")
        for item in state["constraints"]:
            bullet(item if isinstance(item, str) else json.dumps(item))
        lines.append("")
    if state.get("decisions"):
        lines.append("**Decisions**")
        for item in state["decisions"]:
            bullet(f"{item.get('d', '')} — {item.get('why', '')}{evidence(item)}")
        lines.append("")
    if state.get("open_steps"):
        lines.append("**Open steps**")
        for item in state["open_steps"]:
            bullet(f"`{item.get('id', '?')}` {item.get('intent', '')}{evidence(item)}")
            if item.get("next"):
                bullet(f"next: {item['next']}", indent=1)
        lines.append("")
    if state.get("closed_steps"):
        lines.append("**Closed steps**")
        for item in state["closed_steps"]:
            verified = "verified" if item.get("verified") else "unverified"
            commit = f" `{item['commit']}`" if item.get("commit") else ""
            bullet(f"`{item.get('id', '?')}` {item.get('outcome', '')} ({verified}){commit}{evidence(item)}")
        lines.append("")
    if state.get("dead_ends"):
        lines.append("**Dead ends** — do not try these again")
        for item in state["dead_ends"]:
            bullet(item if isinstance(item, str) else json.dumps(item))
        lines.append("")

    working_set = state.get("working_set") or {}
    if any(working_set.get(key) for key in ("commits", "dirty", "files", "last_failure", "hypothesis")):
        lines.append("**Working set**")
        for commit in working_set.get("commits") or []:
            bullet(f"commit `{commit.get('sha', '')}` {commit.get('msg', '')}")
        for path in working_set.get("dirty") or []:
            bullet(f"dirty: `{path}`")
        for entry in working_set.get("files") or []:
            # These are files already read; the note is why, so they are not read again.
            bullet(f"`{entry.get('path', '')}` — {entry.get('note', '')}")
        if working_set.get("last_failure"):
            bullet(f"last failure: {working_set['last_failure']}")
        if working_set.get("hypothesis"):
            bullet(f"hypothesis: {working_set['hypothesis']}")
        lines.append("")
    if state.get("blockers"):
        lines.append("**Blockers**")
        for item in state["blockers"]:
            bullet(item if isinstance(item, str) else json.dumps(item))
        lines.append("")

    known = {
        "seq", "prompt_version", "goal", "constraints", "decisions", "open_steps",
        "closed_steps", "dead_ends", "working_set", "blockers", "subagents_open",
    }
    extra = {key: value for key, value in state.items() if key not in known and value}
    if extra:
        lines.append("**Other**")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(extra, indent=2))
        lines.append("```")
        lines.append("")

    if state.get("seq") is not None:
        lines.append(f"_step state at seq {state['seq']}_")
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

    if main:
        source, text = tasks_section(root, item_id)
        parts.append(_section("Tasks", source, text))

    source, text = step_state(root, item_id, stream)
    parts.append(_section("Step state", source, text))

    source, text = open_handles(root, item_id)
    parts.append(_section("Open subagent handles", source, text))

    if item_id == PARTNER and main:
        partner_md = root / "PARTNER.md"
        parts.append(
            _section(
                "PARTNER.md",
                _relative(root, partner_md),
                partner_md.read_text() if partner_md.is_file() else "",
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
