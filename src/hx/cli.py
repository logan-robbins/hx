"""`hx` — subcommand dispatch for the whole command set of spec 08.

Every command resolves HARNESS_ROOT through `hx.root.resolve_root` before doing anything
else, so the standing refusal (a root that is, or resolves inside, the user's `~/.claude`)
applies to all of them, implemented or not.

Commands that a later milestone delivers exit 2 with
`hx: <cmd>: not implemented (build-N)`, where N is the build-lane goal that delivers it —
spec 13's milestone plus one, since goal build-1 is M0.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import tool_execution as tool_execution_cmd
from . import tool_catalog as tool_catalog_cmd
from . import code_index as code_cmd
from . import content_store as content_cmd
from . import search_exec as exec_cmd
from . import search_sift as sift_cmd
from . import discipline as discipline_cmd
from . import prompt_cache as cache_cmd
from . import application_loop as loop_cmd
from . import amend as amend_cmd
from . import archive as archive_cmd
from . import bench as bench_cmd
from . import board as board_cmd
from . import complete as complete_cmd
from . import companion as companion_cmd
from . import compile as compile_cmd
from . import compose as compose_cmd
from . import dispatch as dispatch_cmd
from . import distill as distill_cmd
from . import doctor as doctor_cmd
from . import flush as flush_cmd
from . import goal as goal_cmd
from . import install as install_cmd
from . import lifecycle
from . import memory as memory_cmd
from . import observer as observer_cmd
from . import native_capture as capture_cmd
from . import progress as progress_cmd
from . import checks as checks_cmd
from . import appmap as map_cmd
from . import planning as planning_cmd
from . import evidence as evidence_cmd
from . import metrics as metrics_cmd
from . import goals as goals_cmd
from . import handoff as handoff_cmd
from . import learn as learn_cmd
from . import read as read_cmd
from . import recall as recall_cmd
from . import resume as resume_cmd
from . import seam as seam_cmd
from . import show as show_cmd
from . import task as task_cmd
from . import ui_cmd
from . import models_cmd
from . import wake as wake_cmd
from .errors import HxError, ValidationError

#: Every command of spec 08, plus `install`, `up`, `doctor`, `ui` and `show`. The v1 cut
#: (spec 14 D25) removed `repo`, `push` and `upgrade`: hx does not manage git or its own
#: version. The value is the build-lane goal that delivers each remaining command.
NOT_IMPLEMENTED: dict[str, int] = {}

IMPLEMENTED = {
    "amend": amend_cmd.main,
    "archive": archive_cmd.main,
    "bench": bench_cmd.main,
    "board": board_cmd.main,
    "companion": companion_cmd.main,
    "capture": capture_cmd.main,
    "check": checks_cmd.main,
    "map": map_cmd.main,
    "plan": planning_cmd.main,
    "compile": compile_cmd.main,
    "complete": complete_cmd.main,
    # `hx compose` and `hx flush` are call sites the hooks and `hx complete` already use;
    # what they do arrives at M2 and M5 (spec 13).
    "compose": compose_cmd.main,
    "dispatch": dispatch_cmd.main,
    "distill": distill_cmd.main,
    "doctor": doctor_cmd.main,
    "flush": flush_cmd.main,
    "goal": goal_cmd.main,
    "heartbeat": lifecycle.main_heartbeat,
    "install": install_cmd.main,
    "launch": lifecycle.main_launch,
    "loop": loop_cmd.main,
    "tool-exec": tool_execution_cmd.main,
    "tools": tool_catalog_cmd.main,
    "code": code_cmd.main,
    "content": content_cmd.main,
    "exec": exec_cmd.main,
    "sift": sift_cmd.main,
    "discipline": discipline_cmd.main,
    "cache": cache_cmd.main,
    "memory": memory_cmd.main,
    "observe": observer_cmd.main,
    "progress": progress_cmd.main,
    "evidence": evidence_cmd.main,
    "metrics": metrics_cmd.main,
    "goals": goals_cmd.main,
    "handoff": handoff_cmd.main,
    "learn": learn_cmd.main,
    "read": read_cmd.main,
    "recall": recall_cmd.main,
    "restart": lifecycle.main_restart,
    "resume": resume_cmd.main,
    "seam": seam_cmd.main,
    "show": show_cmd.main,
    "task": task_cmd.main,
    "ui": ui_cmd.main,
    "up": lifecycle.main_up,
    "wake": wake_cmd.main,
    # Provider model lists diffed against the registry (TOOL_SKILL_PLAN step 3).
    "models": models_cmd.main,
}

COMMANDS = sorted(set(IMPLEMENTED) | set(NOT_IMPLEMENTED))

USAGE = f"""usage: hx <command> [options]

hx is the control plane for a HarnessAgent fleet. HARNESS_ROOT
selects the instance; it defaults to ~/hx and may never be inside the user's ~/.claude.

the control plane:
  launch ID                    idle work item, home, tmux session, goal if working
  launch ID --run RUN --request REQUEST  advance a planned native launch once
    --drain                    close native request/tool admission; retain leases
    --shutdown                 stop observed owned processes; retain unreconciled leases
  dispatch ID GOAL [ID GOAL]   validate the goals, then working; the files are consumed
  goal ID [--now]              paste the pointer into a worker's pane
  task                         print your own goal and its addenda
  progress --file JSON         commit a sparse typed progress update
  progress --run RUN           read the progress revision and current cursor
  plan draft/replan/changes     compile assignments and inspect changed dependencies
  plan validate/apply/unit     validate behavior units and inspect their assignments
  plan ready/assign            admit ready units with prerequisites and write leases
  plan audit/finish/materialize/stop  verify scope, outputs, and integration
  check CHECK [--run RUN]      execute or reuse an input-bound verification receipt
  map init/check/anchor/import/export/publish/get/overlay/propose/refresh  manage the semantic application map
  map plan-context            retrieve a bounded assignment brief for a goal
  capture bind/enqueue         bind and durably capture native execution events
  capture pending/retry        inspect delivery gaps and retry a bounded producer batch
  complete OUTCOME             the agent's last action; checks run here
  resume ID ADDENDUM           continue a blocked or decision item
  amend ID ADDENDUM            append to a working goal; a `### Checks` in it replaces the gate
  distill ID FILE [--memory]   shrink goal addenda into distilled directives above the header
  bench ID                     archive the body and free the id
  seam ID                      flush, recompose, and cut the conversation
  read ID [--detail|--full]      status; prose only with --detail, body with --full
  recall [QUERY] [--id ID]       last-resort file-memory search over completed items
  companion ID [--once]        the Companion loop, one per agent
  companion ID --run RUN --request ID [--stream STREAM]  execute one frozen ledger pass
  compile ID                   distribute base rules into config/ID/AGENTS.md
  flush ID                     wait for the Companion to reach the log head
  restart ID / up / heartbeat  relaunch, boot, and the human's own cron
  wake partner TEXT            the one way anything reaches the Partner
  memory search QUERY          what other agents already learned; see `hx memory --help`
  learn propose/pending/accept/reject/check/list  the central LEARNINGS file: propose a rule, a
                               different session accepts or merges it; see `hx learn --help`
  handoff check/capture/pushed/sums  what a handoff carries before another session acts on it
  loop [--once]                capture, refresh mapped sources, and run one companion at a time
  observe register/drain/serve/status  incremental continuity source capture
  evidence EVENT [--offset N --limit N]  retrieve a bounded slice of retained evidence
  code index --repo PATH       index symbols, imports, and source text for code search
  code search QUERY            exact-symbol and FTS code search (porter + trigram, RRF)
  code symbols NAME            symbol definitions; code impact PATH... for change impact
  content index/search/fetch   persistent exact-text knowledge store for docs and web
  exec --language L --code C   isolated execution: program the analysis, only stdout returns
  exec file PATH --language L --code C  run code over a file without reading it into context
  exec batch --file F.json     run many commands, auto-index output, search in one call
  sift candidates/triage       Jev semantic sifting over deterministic candidates (advisory)
  discipline profile/orient/commit  repo-derived size bounds, orientation load, committable check
  discipline pull --repo DIR        commits the principal branch has that HEAD lacks (--expect fresh)

read-only views:
  board [--json]                          a plain listing of what is on disk
  show ID [--json]                        everything hx knows about one id
  metrics [ID] [--json] [--watch]         seam metrics, or the fleet rollup
  goals [--json] / archive [--json]       the goal records, and what has been archived
  ui [--port N]                           the read-only web view on 127.0.0.1
  doctor [--json]                         what is here, what is missing, what is broken
  help [--all | --audience A] [--json]    the commands your audience sees (HARNESS_ID)
  models [--provider P] [--expect current] [--json]
                                        provider model lists vs config/models.json; writes nothing
  install --root PATH [--env-file PATH] [--ignore-claude-version]
                                        create the instance (spec 17.2)

every command of spec 08:
  {chr(10) + '  '}{'  '.join(COMMANDS)}
"""


def _root_for(argv: list[str], env) -> Path:
    """Resolve the root, honouring a `--root` anywhere in the command's own arguments."""
    from .root import resolve_root

    override = None
    for index, arg in enumerate(argv):
        if arg == "--root" and index + 1 < len(argv):
            override = argv[index + 1]
        elif arg.startswith("--root="):
            override = arg.split("=", 1)[1]
    return resolve_root(override, env)


# --- exposure (TOOL_SKILL_PLAN §Inventory) --------------------------------------------------------

#: Who sees which capability. Each entry is `command`, `command sub` or `command sub --flag`
#: (`command --flag` for a flag of a command without subcommands). The worker column is the
#: whole list a worker is shown by `hx help`; the subagent column is what a worker's child
#: reads (it shares the parent's `HARNESS_ID`, so it is a subset of the worker's); `runtime`
#: is what hooks, the root service and the Partner's own commands run under a worker's
#: `HARNESS_ID` and is never gated or listed. A worker caller is refused, with the
#: `refuse:` exit, every entry that is not worker, subagent or runtime (`gate`).
AUDIENCES: dict[str, set[str]] = {
    "operator": {
        "install", "up", "doctor", "ui", "heartbeat", "archive",
        "learn check", "learn path", "learn categories", "learn invariant",
        "cache assess", "cache budget", "memory index",
        "models",
        "metrics --commands",
    },
    "partner": {
        "launch", "dispatch", "goal", "resume", "amend", "bench", "distill", "restart", "read",
        "board", "show", "metrics", "goals", "recall",
        "plan draft", "plan replan", "plan changes", "plan validate", "plan apply", "plan unit",
        "plan ready", "plan assign", "plan audit", "plan finish", "plan materialize", "plan stop",
        "map init", "map import", "map export", "map overlay", "map propose", "map refresh",
        "map publish", "map plan-context", "map get", "map check", "map anchor",
        "learn pending", "learn accept", "learn reject", "learn list",
        "tools index", "compile",
        "handoff check", "handoff capture", "handoff pushed", "handoff sums",
        "discipline profile", "discipline orient",
        "memory search --all-roles", "memory stats", "memory list",
        "content index", "content stats",
        # GRAPH_PLAN steps 2-8 (the graph lane builds them; the gate covers them already).
        "code contracts", "code submaps", "code stats", "code import",
        "models",
        "metrics --commands",
    },
    "worker": {
        "task", "complete", "progress",
        "learn propose", "learn classify", "learn repeat", "learn list",
        "memory search", "recall",
        "code search", "code symbols", "code impact", "code edges",
        "content search", "content fetch",
        "exec", "sift", "tool-exec",
        "tools discover", "tools load", "tools call", "tools status",
        "discipline commit", "evidence",
        # GRAPH_PLAN steps 2-6, with --expect/--owners.
        "code type", "code signature", "code callers", "code register", "code tests-for",
        "code conform", "code precedent", "code contract", "code map",
        # TOOL_SKILL_PLAN step 5.
        "discipline pull",
    },
    "subagent": {
        "code search", "code type", "code signature", "code callers",
        "content search", "content fetch", "exec", "memory search", "evidence",
    },
    # The Companion is a Read + Write session: no `hx` command at all.
    "companion": set(),
    "runtime": {
        "capture", "observe", "flush", "seam", "compose", "check", "companion", "loop", "wake",
        "code index", "tools acknowledge",
    },
}

#: Partner-column entries whose module grants a worker its own assignment and refuses
#: everything else itself (`planning.main`: "worker may audit, finish, or materialize only
#: its own assignment"). The CLI defers to that finer check instead of refusing outright.
OWN_ASSIGNMENT = {"plan audit", "plan finish", "plan materialize"}

#: The audiences a worker caller may run, and the audiences `hx help` can print.
WORKER_MAY_RUN = ("worker", "subagent", "runtime")
HELP_AUDIENCES = ("operator", "partner", "worker", "subagent", "companion")


def _entries(audiences: dict[str, set[str]] | None = None) -> set[str]:
    audiences = AUDIENCES if audiences is None else audiences
    return set().union(*audiences.values())


def resolve_capability(command: str, rest: list[str],
                       audiences: dict[str, set[str]] | None = None) -> str | None:
    """The most specific AUDIENCES entry `hx <command> <rest…>` names, or None.

    The subcommand is the first non-flag argument that forms a known `command sub` entry
    (`--root` and its value are skipped); a `--flag` entry wins over the plain one when the
    flag is present.
    """
    known = _entries(audiences)
    tokens, skip = [], False
    for arg in rest:
        if skip:
            skip = False
            continue
        if arg == "--root":
            skip = True
            continue
        tokens.append(arg)
    flags = {t.split("=", 1)[0] for t in tokens if t.startswith("--")}
    sub = next((t for t in tokens if not t.startswith("-") and f"{command} {t}" in known), None)
    candidates = []
    if sub is not None:
        candidates += [f"{command} {sub} {flag}" for flag in sorted(flags)] + [f"{command} {sub}"]
    candidates += [f"{command} {flag}" for flag in sorted(flags)] + [command]
    return next((c for c in candidates if c in known), None)


def worker_may_run(command: str, capability: str | None,
                   audiences: dict[str, set[str]] | None = None) -> bool:
    """Whether a worker caller may run it. An unknown subcommand of a command with any
    worker entry is left to the module (its own argparse names the mistake)."""
    audiences = AUDIENCES if audiences is None else audiences
    allowed = set().union(*(audiences.get(name, set()) for name in WORKER_MAY_RUN))
    if capability is None:
        family = {e for e in _entries(audiences) if e.split()[0] == command}
        return not family or bool(family & allowed)
    return capability in allowed or capability in OWN_ASSIGNMENT


def help_audience(env) -> str:
    """`operator` with no HARNESS_ID, `partner`, `companion` (HX_ROLE), else `worker`."""
    from .caller import caller
    from .ids import PARTNER

    who = caller(env)
    if who is None:
        return "operator"
    if who == PARTNER:
        return "partner"
    if env.get("HX_ROLE") == "companion":
        return "companion"
    return "worker"


def gate(command: str, rest: list[str], env) -> None:
    """Refuse a worker caller anything outside its column (TOOL_SKILL_PLAN §Key Decisions)."""
    from .caller import caller, require_partner_caller
    from .ids import PARTNER

    who = caller(env)
    if who is None or who == PARTNER:
        return
    capability = resolve_capability(command, rest)
    if worker_may_run(command, capability):
        return
    name = capability or command
    operator_only = name in AUDIENCES["operator"] and name not in AUDIENCES["partner"]
    require_partner_caller(name, env, owner="the operator's" if operator_only else None)


def _grouped(entries: set[str]) -> list[str]:
    families: dict[str, list[str]] = {}
    for entry in sorted(entries):
        head, _, tail = entry.partition(" ")
        families.setdefault(head, [])
        if tail:
            families[head].append(tail)
    width = max((len(head) for head in families), default=0)
    return [f"  {head.ljust(width)}  {' | '.join(subs)}".rstrip() for head, subs in families.items()]


def main_help(argv: list[str], env) -> int:
    """`hx help [--all | --audience NAME] [--json]`: the caller's column of the inventory."""
    import argparse
    import json

    from .caller import caller

    parser = argparse.ArgumentParser(prog="hx help", description="the commands your audience sees")
    parser.add_argument("--all", action="store_true", help="every audience's column")
    parser.add_argument("--audience", choices=HELP_AUDIENCES, help="one audience's column")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if args.all:
        columns = {name: sorted(AUDIENCES[name]) for name in HELP_AUDIENCES}
        if args.json:
            print(json.dumps({"audiences": columns}, indent=2))
            return 0
        for name in HELP_AUDIENCES:
            print(f"{name}:")
            print("\n".join(_grouped(AUDIENCES[name])) or "  (no hx command)")
        return 0
    audience = args.audience or help_audience(env)
    entries = sorted(AUDIENCES[audience])
    if args.json:
        print(json.dumps({"audience": audience, "caller": caller(env),
                          "role": env.get("HX_ROLE") or None, "commands": entries}, indent=2))
        return 0
    print(f"hx help: the {audience} view; `hx <command> --help` for one command, "
          "`hx help --all` for every audience")
    print("\n".join(_grouped(set(entries))) or "  (no hx command: Read and Write only)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    import os

    env = os.environ

    if argv and argv[0] == "help":
        # The caller's column of the inventory (TOOL_SKILL_PLAN step 1); `--all` for every one.
        return main_help(argv[1:], env)
    if argv and argv[0] in ("-h", "--help") and help_audience(env) in ("worker", "companion"):
        # A worker is shown its own column, never the whole list.
        return main_help([], env)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 2
    if argv[0] in ("-V", "--version"):
        from . import __version__

        print(f"hx {__version__}")
        return 0

    command, rest = argv[0], argv[1:]
    if command not in IMPLEMENTED and command not in NOT_IMPLEMENTED:
        print(f"hx: {command}: unknown command; try `hx --help`", file=sys.stderr)
        return 2

    try:
        # The standing refusal runs before anything else, for every command (ORCHESTRATION.md).
        root = _root_for(rest, env)
        import os
        if (os.environ if env is None else env).get('HX_CONTINUITY_RUN') and command in {'memory', 'recall'}:
            raise ValidationError('planned workers have no episodic memory; use current task progress, map inputs and bounded evidence')
        # Authority backs visibility: a worker caller is refused anything outside its column.
        gate(command, rest, env)
        if command in NOT_IMPLEMENTED:
            print(
                f"hx: {command}: not implemented (build-{NOT_IMPLEMENTED[command]})",
                file=sys.stderr,
            )
            return 2
        return IMPLEMENTED[command](rest, root, env=env)
    except HxError as exc:
        print(f"hx: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
