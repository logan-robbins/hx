"""`hx metrics` — per-agent seam metrics and the fleet rollup (spec 07.4, 08).

`hx metrics <id>` is the contracted seam document (CONTRACTS.md): one entry per
seam record on the main stream since `dispatched`. `next_10_turns` counts
assistant turns from the harness transcript the post-seam boundary record
references (Claude-flavor JSONL); when no parseable transcript exists those
fields are `null`, never fabricated.

`hx metrics` with no id is the fleet document: one entry per board item with
its harness (flavor/model/effort), machine-derived ties (id mentions in its
goal and addenda), and links (relative paths to work item, harness config,
context file, stream, pane log). `summary` rolls up counts, the usage-limit
paused list, needs-input and dead panes. `graph` is always `null` here: the
instance overlay (e.g. the conductor-backed graph status) fills it for the UI.

`--watch` streams JSONL: one `snapshot`, then `delta` objects naming changed
scopes. Pane-derived fields (alive/needs-input/limit) refresh on the slower
pane tick; everything else is pure filesystem.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from . import streams, timestamps, tmux
from .errors import NotFound
from .ids import PARTNER, sort_key
from .tasks import load_tasks
from .workitems import find_work_item, find_work_items, parse_work_item

#: Pane-tail markers of a usage-limit pause (observed live; a pane matching one
#: advances only when the limit lifts or a human restarts it).
LIMIT_MARKERS = (
    "usage limit reached",
    "rate limit",
)

#: Text-input tool names whose file argument counts as a read (Claude flavor).
READ_TOOLS = {"Read"}


def _read_json_rel(root: Path, rel: str) -> dict | None:
    try:
        return json.loads((root / rel).read_text())
    except (OSError, ValueError):
        return None


def _prompt_version(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        base = value.get("base")
        role = value.get("role")
        if base or role:
            return f"{base}/{role}"
    return None


def _working_set_paths(root: Path, item_id: str) -> set[str]:
    """Files the agent was told not to re-read (state/<id>/*.json)."""
    paths: set[str] = set()
    directory = root / "state" / item_id
    if not directory.is_dir():
        return paths
    for path in directory.glob("*.json"):
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        working_set = state.get("working_set")
        if not isinstance(working_set, dict):
            continue
        for entry in working_set.get("files") or []:
            if isinstance(entry, dict) and entry.get("path"):
                paths.add(str(entry["path"]))
            elif isinstance(entry, str):
                paths.add(entry)
        for entry in working_set.get("dirty") or []:
            if isinstance(entry, str):
                paths.add(entry)
    return paths


def _iter_transcript_assistant(path: Path):
    """Yield (timestamp, tool_uses) for Claude-flavor assistant messages."""
    try:
        handle = path.open("r", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") != "assistant" or record.get("isSidechain"):
                continue
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            yield record.get("timestamp"), uses


def _next_10_turns(root: Path, item_id: str, seam_ts: str | None,
                   boundary_ref: str | None) -> dict:
    """M7's post-seam window: first 10 assistant turns after the seam.

    `null` throughout when no parseable transcript exists — a missing window
    is reported, never approximated.
    """
    nulls = {"turns": None, "tool_calls": None, "reads_of_context_file": None,
             "reads_of_working_set": None, "other": None}
    if not boundary_ref:
        return dict(nulls)
    transcript = Path(boundary_ref)
    if not transcript.is_file():
        return dict(nulls)
    floor = timestamps.parse(seam_ts) if seam_ts else None
    context_name = f"{item_id}-main.context.md"
    working_set = _working_set_paths(root, item_id)
    turns = tool_calls = reads_ctx = reads_ws = 0
    try:
        assistant = [(ts, uses) for ts, uses in _iter_transcript_assistant(transcript)]
    except OSError:
        return dict(nulls)
    if not assistant:
        return dict(nulls)
    for ts, uses in assistant:
        if floor is not None and isinstance(ts, str):
            parsed = timestamps.parse(ts)
            if parsed is not None and parsed < floor:
                continue
        if turns >= 10:
            break
        turns += 1
        for use in uses:
            tool_calls += 1
            if use.get("name") not in READ_TOOLS:
                continue
            target = (use.get("input") or {}).get("file_path", "")
            if not isinstance(target, str):
                continue
            if target.endswith(context_name):
                reads_ctx += 1
            elif target in working_set:
                reads_ws += 1
    if turns == 0:
        return dict(nulls)
    return {"turns": turns, "tool_calls": tool_calls,
            "reads_of_context_file": reads_ctx,
            "reads_of_working_set": reads_ws,
            "other": tool_calls - reads_ctx - reads_ws}


def _seam_entries(root: Path, item_id: str, since: str | None) -> list[dict]:
    """One contracted entry per seam record since `dispatched`."""
    path = streams.main_stream(root, item_id)
    if not path.is_file():
        return []
    floor = timestamps.parse(since) if since else None
    records = [r for r in streams.iter_records(path)]
    entries = []
    for index, record in enumerate(records):
        if record.get("event") != "seam":
            continue
        ts = record.get("ts")
        if floor is not None and isinstance(ts, str):
            parsed = timestamps.parse(ts)
            if parsed is not None and parsed < floor:
                continue
        boundary_ref = None
        for later in records[index + 1:]:
            if later.get("event") == "boundary":
                ref = later.get("ref") or {}
                boundary_ref = ref.get("transcript") if isinstance(ref, dict) else None
                break
            if later.get("event") == "seam":
                break
        entries.append({
            "seq": record.get("seq"),
            "ts": ts,
            "source": record.get("source"),
            "prompt_version": _prompt_version(record.get("prompt_version")),
            "context_tokens_before": record.get("context_tokens_before"),
            "context_file_bytes": record.get("context_file_bytes"),
            "working_set_size": record.get("working_set_size"),
            "next_10_turns": _next_10_turns(root, item_id,
                                           ts if isinstance(ts, str) else None,
                                           boundary_ref),
        })
    return entries


def per_id(root: Path, item_id: str) -> dict:
    """The contracted `hx metrics <id>` document (CONTRACTS.md)."""
    known = set(find_work_items(root)) | {p.name for p in (root / "config").iterdir()
                                          if p.is_dir()} if (root / "config").is_dir() else set(find_work_items(root))
    try:
        tasks = load_tasks(root)
    except Exception:
        tasks = {}
    known |= set(tasks)
    path = streams.main_stream(root, item_id)
    if item_id not in known and not path.is_file() and item_id != PARTNER:
        raise NotFound(f"unknown id: {item_id}")
    task = tasks.get(item_id) or {}
    dispatched = task.get("dispatched")
    if dispatched is None:
        found = find_work_item(root, item_id)
        if found is not None:
            try:
                dispatched = parse_work_item(found).dispatched
            except Exception:
                pass
    seams = _seam_entries(root, item_id, dispatched if isinstance(dispatched, str) else None)
    totals: dict[str, int | None] = {"seams": len(seams), "tool_calls": None,
                                    "reads_of_context_file": None,
                                    "reads_of_working_set": None, "other": None}
    contributed = False
    for key in ("tool_calls", "reads_of_context_file", "reads_of_working_set", "other"):
        values = [s["next_10_turns"][key] for s in seams
                  if s["next_10_turns"][key] is not None]
        if values:
            totals[key] = sum(values)
            contributed = True
    if not contributed:
        pass
    return {
        "id": item_id,
        "stream": f"{item_id}-main",
        "dispatched": dispatched,
        "seams": seams,
        "totals": totals,
    }


def render_per_id_text(doc: dict) -> str:
    """Text form: one line per seam with the same fields (CONTRACTS.md)."""
    lines = [f"# {doc['id']} stream={doc['stream']} dispatched={doc['dispatched']}"]
    for seam in doc["seams"]:
        window = seam["next_10_turns"]
        lines.append(
            " ".join(str(value) for value in [
                seam["seq"], seam["ts"], seam["source"], seam["prompt_version"],
                seam["context_tokens_before"], seam["context_file_bytes"],
                seam["working_set_size"], window["turns"], window["tool_calls"],
                window["reads_of_context_file"], window["reads_of_working_set"],
                window["other"],
            ]))
    totals = doc["totals"]
    lines.append(f"totals seams={totals['seams']} tool_calls={totals['tool_calls']} "
                 f"reads_of_context_file={totals['reads_of_context_file']} "
                 f"reads_of_working_set={totals['reads_of_working_set']} other={totals['other']}")
    return "\n".join(lines) + "\n"


# --- fleet ---------------------------------------------------------------------

#: Id mentions (`eng-001`, `partner`) found in goal and addenda text.
_MENTION_RE = re.compile(r"\b(partner|[a-z]+-[0-9]{3})\b")


def _harness(root: Path, item_id: str) -> dict | None:
    """Best-effort harness config; metrics never fails on a bad one."""
    data = _read_json_rel(root, f"config/{item_id}/harness.json")
    if not isinstance(data, dict):
        return None
    return {
        "flavor": data.get("flavor"),
        "model": data.get("model"),
        "effort": data.get("effort"),
        "workdir": data.get("workdir"),
    }


def _ties(root: Path, item_id: str, task: dict, scope: str | None) -> dict:
    texts = [task.get("goal") or "", scope or ""]
    for addendum in task.get("addenda") or []:
        if isinstance(addendum, dict):
            texts.append(addendum.get("text") or "")
    mentions = sorted(set(_MENTION_RE.findall("\n".join(texts))) - {item_id})
    return {"mentions": mentions}


def _links(root: Path, item_id: str, work_item_rel: str | None) -> dict:
    def rel(path: Path) -> str | None:
        return str(path.relative_to(root)) if path.is_file() else None

    return {
        "work_item": work_item_rel,
        "harness": rel(root / "config" / item_id / "harness.json"),
        "context": rel(root / "run" / item_id / f"{item_id}-main.context.md"),
        "stream": rel(streams.main_stream(root, item_id)),
        "pane_log": rel(root / "logs" / item_id / f"{item_id}-pane.log"),
        "goal_marker": rel(root / "run" / item_id / "goal"),
    }


def _limit_paused(item_id: str, alive: bool, env: dict[str, str] | None) -> bool:
    if not alive:
        return False
    from .goal import capture_pane  # lazy: goal pulls the adapter layer

    try:
        captured = capture_pane(item_id, env, lines=12) or ""
    except Exception:
        return False
    tail = captured.lower()
    return any(marker in tail for marker in LIMIT_MARKERS)


def _partner_tail(root: Path) -> dict | None:
    path = root / "logs" / "partner" / "partner-main.jsonl"
    if not path.is_file():
        return None
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines[-5:]):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            return {"seq": record.get("seq"), "ts": record.get("ts"),
                    "event": record.get("event")}
    return None


def enrich_agent(root: Path, board_item: dict, task: dict,
                 env: dict[str, str] | None, *, pane: bool) -> dict:
    """A board item plus harness, ties, links and (optionally) pane state."""
    item_id = board_item["id"]
    agent = dict(board_item)
    agent["harness"] = _harness(root, item_id)
    agent["ties"] = _ties(root, item_id, task, board_item.get("scope"))
    agent["links"] = _links(root, item_id, board_item.get("file"))
    if pane:
        agent["limit_paused"] = _limit_paused(item_id, bool(board_item.get("session_alive")), env)
    else:
        agent["limit_paused"] = None
    return agent


def summarize(agents: list[dict]) -> dict:
    """Fleet counts; pure function of agent entries (CONTRACTS.md)."""
    by_pod: dict[str, dict[str, int]] = {}
    paused, needs, dead, working = [], [], [], 0
    for agent in agents:
        pod, state = agent.get("pod") or "?", agent.get("state") or "?"
        by_pod.setdefault(pod, {}).setdefault(state, 0)
        by_pod[pod][state] += 1
        if agent.get("state") == "working":
            working += 1
            if not agent.get("session_alive"):
                dead.append(agent["id"])
        if agent.get("limit_paused"):
            paused.append(agent["id"])
        if agent.get("needs_input"):
            needs.append(agent["id"])
    return {
        "agents": len(agents),
        "working": working,
        "by_pod": by_pod,
        "paused_limit": sorted(paused),
        "needs_input": sorted(needs),
        "dead": sorted(dead),
    }


def collect_fleet(root: Path, *, env: dict[str, str] | None = None) -> dict:
    """The fleet document: every agent enriched, plus summary and partner."""
    from . import board as board_mod  # local import: board never imports metrics

    board = board_mod.collect(root, env=env)
    try:
        tasks = load_tasks(root)
    except Exception:
        tasks = {}
    agents = [enrich_agent(root, item, tasks.get(item["id"]) or {}, env, pane=True)
              for item in board["items"]]
    agents.sort(key=lambda agent: sort_key(agent["id"]))
    return {
        "root_abs": board["root_abs"],
        "ts": timestamps.now(),
        "agents": agents,
        "summary": summarize(agents),
        "partner": _partner_tail(root),
        # The instance overlay (graph status) fills this for the UI; the
        # product has no graph of its own, so it is always null here.
        "graph": None,
    }


#: The build lane's published binding (`hx.ui.data.PUBLISHED`): the UI reads the
#: fleet document through the same function the CLI calls.
collect = collect_fleet


def render_fleet_text(doc: dict) -> str:
    """Text form: one line per agent, then summary, paused and needs-input."""
    lines = [f"# fleet root={doc['root_abs']} ts={doc['ts']}"]
    for agent in doc["agents"]:
        harness = agent.get("harness") or {}
        flags = []
        if agent.get("limit_paused"):
            flags.append("LIMIT")
        if agent.get("needs_input"):
            flags.append("INPUT")
        if agent.get("state") == "working" and not agent.get("session_alive"):
            flags.append("DEAD")
        lines.append(
            f"{agent['id']} {agent.get('pod')} {agent.get('state')} "
            f"{harness.get('model')}/{harness.get('effort')} "
            f"{'alive' if agent.get('session_alive') else 'dead'} "
            f"{','.join(flags) or '-'} {(agent.get('scope') or '')[:80]}")
    summary = doc["summary"]
    lines.append(f"summary agents={summary['agents']} working={summary['working']} "
                 f"by_pod={json.dumps(summary['by_pod'], sort_keys=True)}")
    lines.append(f"paused_limit({len(summary['paused_limit'])}): "
                 f"{' '.join(summary['paused_limit'])}")
    lines.append(f"needs_input({len(summary['needs_input'])}): "
                 f"{' '.join(summary['needs_input'])}")
    lines.append(f"dead({len(summary['dead'])}): {' '.join(summary['dead'])}")
    partner = doc.get("partner") or {}
    lines.append(f"partner seq={partner.get('seq')} ts={partner.get('ts')} "
                 f"event={partner.get('event')}")
    return "\n".join(lines) + "\n"


# --- watch ---------------------------------------------------------------------

#: Watched globs mapping to scopes: an agent id, or `tasks`/`partner`.
_WATCH_GLOBS = (
    ("tasks.json", None),
    ("pods/*/*.md", "id"),
    ("logs/partner/*.jsonl", "partner"),
    ("logs/*/*.jsonl", "id"),
    ("logs/*/*-pane.log", "id"),
    ("run/*/goal", "id"),
    ("run/*/turn", "id"),
    ("run/*/*.context.md", "id"),
    ("config/*/harness.json", "id"),
    ("state/*/*.json", "id"),
)


#: Id prefix in a file or directory name (`eng-001-working.md` → `eng-001`).
_ID_PREFIX_RE = re.compile(r"^(partner|[a-z]+-[0-9]{3})(?=[-.]|$)")


def _scope_of(root: Path, path: Path, kind: str | None) -> str | None:
    if kind is None:
        return "tasks"
    if kind == "partner":
        return "partner"
    for part in path.relative_to(root).parts:
        match = _ID_PREFIX_RE.match(part)
        if match:
            return match.group(1)
    return None


def snapshot_scopes(root: Path) -> dict[str, float]:
    """{scope: max mtime} over everything a metrics read observes."""
    scopes: dict[str, float] = {}
    for pattern, kind in _WATCH_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            scope = _scope_of(root, path, kind)
            if scope is None:
                continue
            scopes[scope] = max(scopes.get(scope, 0.0), mtime)
    return scopes


def _agent_key(agent: dict) -> str:
    return json.dumps(agent, sort_keys=True, default=str)


def watch(root: Path, interval: float = 2.0, slow: float = 30.0,
          *, env: dict[str, str] | None = None):
    """Yield JSONL stream dicts: one snapshot, then deltas on change.

    Fast ticks compare mtime fingerprints (pure stat); a pane that dies or a
    limit banner with no file write behind it surfaces on the slow tick,
    which always re-collects and diffs.
    """
    fleet = collect_fleet(root, env=env)
    known = {agent["id"]: _agent_key(agent) for agent in fleet["agents"]}
    scopes = snapshot_scopes(root)
    yield {"type": "snapshot", "ts": fleet["ts"], "fleet": fleet}
    last_slow = time.monotonic()
    while True:
        time.sleep(interval)
        current = snapshot_scopes(root)
        moved = [scope for scope, mtime in current.items()
                 if mtime > scopes.get(scope, 0.0)]
        scopes = current
        forced = time.monotonic() - last_slow >= slow
        if not moved and not forced:
            continue
        if forced:
            last_slow = time.monotonic()
        fleet = collect_fleet(root, env=env)
        changed = [agent["id"] for agent in fleet["agents"]
                   if _agent_key(agent) != known.get(agent["id"])]
        gone = [item_id for item_id in known
                if item_id not in {agent["id"] for agent in fleet["agents"]}]
        known = {agent["id"]: _agent_key(agent) for agent in fleet["agents"]}
        if not changed and not gone and not moved:
            continue
        yield {
            "type": "delta",
            "ts": fleet["ts"],
            "changed": sorted(set(changed) | set(gone) | set(moved)),
            "agents": [agent for agent in fleet["agents"] if agent["id"] in changed],
            "summary": fleet["summary"],
            "partner": fleet["partner"],
        }


# --- CLI -----------------------------------------------------------------------


def main(argv: list[str], root: Path, *, env: dict[str, str] | None = None) -> int:
    """`hx metrics [ID] [--json] [--watch[=SECS]]` (spec 08)."""
    parser = argparse.ArgumentParser(prog="hx metrics", description=(
        "Per-agent seam metrics (with ID), or the fleet rollup (without). "
        "--watch streams JSONL snapshot/delta objects."))
    parser.add_argument("id", nargs="?", help="agent id for the seam document")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--watch", nargs="?", const=2.0, type=float, metavar="SECS",
                        help="stream JSONL; re-scan every SECS seconds (default 2)")
    args = parser.parse_args(argv)
    if args.watch is not None:
        if args.id is not None:
            parser.error("--watch takes no ID")
        try:
            for event in watch(root, interval=args.watch, env=env):
                print(json.dumps(event, default=str), flush=True)
        except BrokenPipeError:
            return 0
        return 0
    if args.id is not None:
        doc = per_id(root, args.id)
        if args.json:
            print(json.dumps(doc, indent=2, default=str))
        else:
            print(render_per_id_text(doc), end="")
        return 0
    doc = collect_fleet(root, env=env)
    if args.json:
        print(json.dumps(doc, indent=2, default=str))
    else:
        print(render_fleet_text(doc), end="")
    return 0
