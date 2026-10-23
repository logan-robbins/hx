"""Audience-scoped commands for the current graph, packet and delivery lifecycle.

All commands resolve the instance root and enforce caller authority before execution.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import tool_execution as tool_execution_cmd
from . import file_reads as file_reads_cmd
from . import tool_catalog as tool_catalog_cmd
from . import code_index as code_cmd
from . import repo_service as repo_cmd
from . import change_delivery as change_cmd
from . import dotnet_build as build_cmd
from . import content_store as content_cmd
from . import search_exec as exec_cmd
from . import search_sift as sift_cmd
from . import discipline as discipline_cmd
from . import prompt_cache as cache_cmd
from . import application_loop as loop_cmd
from . import board as board_cmd
from . import complete as complete_cmd
from . import companion as companion_cmd
from . import compose as compose_cmd
from . import doctor as doctor_cmd
from . import fleet as fleet_cmd
from . import flush as flush_cmd
from . import install as install_cmd
from . import lifecycle
from . import observer as observer_cmd
from . import persona as persona_cmd
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
from . import seam as seam_cmd
from . import show as show_cmd
from . import task as task_cmd
from . import ui_cmd
from . import models_cmd
from . import wake as wake_cmd
from .errors import HxError, ValidationError

IMPLEMENTED = {
    "board": board_cmd.main,
    "companion": companion_cmd.main,
    "capture": capture_cmd.main,
    "check": checks_cmd.main,
    "map": map_cmd.main,
    "plan": planning_cmd.main,
    "complete": complete_cmd.main,
    "compose": compose_cmd.main,
    "doctor": doctor_cmd.main,
    "fleet": fleet_cmd.main,
    "flush": flush_cmd.main,
    "heartbeat": lifecycle.main_heartbeat,
    "install": install_cmd.main,
    "launch": lifecycle.main_launch,
    "loop": loop_cmd.main,
    "tool-exec": tool_execution_cmd.main,
    "file-read": file_reads_cmd.main,
    "tools": tool_catalog_cmd.main,
    "code": code_cmd.main,
    "repo": repo_cmd.main,
    "change": change_cmd.main,
    "build": build_cmd.main,
    "content": content_cmd.main,
    "exec": exec_cmd.main,
    "sift": sift_cmd.main,
    "discipline": discipline_cmd.main,
    "cache": cache_cmd.main,
    "observe": observer_cmd.main,
    "persona": persona_cmd.main,
    "progress": progress_cmd.main,
    "evidence": evidence_cmd.main,
    "metrics": metrics_cmd.main,
    "goals": goals_cmd.main,
    "handoff": handoff_cmd.main,
    "learn": learn_cmd.main,
    "read": read_cmd.main,
    "seam": seam_cmd.main,
    "show": show_cmd.main,
    "task": task_cmd.main,
    "ui": ui_cmd.main,
    "up": lifecycle.main_up,
    "wake": wake_cmd.main,
    # Provider model lists diffed against the registry.
    "models": models_cmd.main,
}

COMMANDS = sorted(IMPLEMENTED)

USAGE = f"""usage: hx <command> [options]

Every directive uses the code/application graph and a dependency packet plan.
Search existing capabilities before building. Schedule all ready independent packets
subject to dependency proofs, source ownership, resource leases and host capacity.

  plan draft/validate/apply     define and record graph-driven dependency packets
  plan partition --repo WT     graph providers, families and waves; --out drafts the plan
  plan ready/assign            inspect readiness and admit a packet
  plan run PLAN [--dry-run]    admit and launch ready packets on idle seats within budgets
  launch ID --run RUN --request REQUEST  advance the admitted native worker
  launch partner               start the Partner orchestrator
  plan bind/approve-binding    freeze worker checks and independent approval
  plan publish-contract/acquire-contract  release consumers against exact interfaces
  check CHECK --run RUN        execute or reuse source-bound proof
  complete done                request verified completion of the caller's run
  progress --file JSON         record task findings, blockers and decisions
  plan finish/materialize/verify-integration  publish outputs and verify dependencies
  change submit/review/land    review intent and publish a short expected-head update
  change candidate/run-validation  validate fixed integration and deployment candidates
  board/show/task/goals        project current task and run state from the ledger
  code                         query the shared code graph and source snapshots
  repo                         metadata queries and exact source materialization
  build                        reusable .NET builds and isolated test shards
  help [--all | --audience A]   inspect the tool inventory for an audience

commands:
  {'  '.join(COMMANDS)}
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


# --- exposure -------------------------------------------------------------------------------------

#: Who sees which capability. Each entry is `command`, `command sub` or `command sub --flag`
#: (`command --flag` for a flag of a command without subcommands). The worker column is the
#: whole list a worker is shown by `hx help`; the subagent column is what a worker's child
#: reads (it shares the parent's `HARNESS_ID`, so it is a subset of the worker's); `runtime`
#: is what hooks, the root service and the Partner's own commands run under a worker's
#: `HARNESS_ID` and is never gated or listed. A worker caller is refused, with the
#: `refuse:` exit, every entry that is not worker, subagent or runtime (`gate`).
AUDIENCES: dict[str, set[str]] = {
    "operator": {
        "install", "up", "doctor", "ui", "heartbeat",
        "learn check", "learn path", "learn categories", "learn invariant",
        "cache assess", "cache budget",
        "models",
        "cache report",
        "repo serve", "repo register", "repo status",
        "change status", "change land", "change validate", "change reconcile",
        "change candidate", "change run-validation", "change fail", "change revert",
        "handoff delivery",
        "build status", "build reconcile",
        "code journal",
        "code audit",
        "fleet pause", "fleet resume",
    },
    "partner": {
        "launch",        "read", "file-read",
        "board", "show", "metrics", "goals",
        "plan draft", "plan replan", "plan changes", "plan validate", "plan apply", "plan unit",
        "plan ready", "plan run", "plan assign", "plan audit", "plan finish", "plan materialize", "plan stop",
        "plan partition",
        "map init", "map import", "map export", "map overlay", "map propose", "map refresh",
        "map publish", "map plan-context", "map get", "map check", "map anchor",
        "learn pending", "learn accept", "learn reject", "learn list",
        "tools index", "persona",
        "handoff check", "handoff capture", "handoff pushed", "handoff sums",
        "handoff delivery",
        "discipline profile", "discipline orient",

        "content index", "content stats",
        "code contracts", "code submaps", "code stats", "code import",
        "code journal",
        "code audit",
        "models",
        "cache report",
        "repo status", "repo register", "repo diff", "repo merge-base", "repo history", "repo file",
        "repo materialize",
        "repo assignment",
        "repo expand",
        "change status",
        "build graph", "build status",
    },
    "worker": {
        "task", "complete", "progress",
        "learn propose", "learn classify", "learn repeat", "learn list",

        "code search", "code symbols", "code impact", "code edges",
        "content search", "content fetch",
        "exec", "sift", "tool-exec", "file-read",
        "tools discover", "tools load", "tools call", "tools status",
        "discipline commit", "evidence",
        "code type", "code signature", "code callers", "code register", "code tests-for",
        "code conform", "code precedent", "code contract", "code map", "code fresh",
        "code audit",
        "discipline pull",
        "repo diff", "repo merge-base", "repo history", "repo file",
        "repo materialize",
        "change submit", "change review", "change status", "change land", "change validate", "change reconcile",
        "repo expand",
        "change candidate", "change run-validation", "change fail", "change revert",
        "handoff delivery", "handoff check", "handoff sums",
        "plan bind", "plan binding", "plan approve-binding", "plan publish-contract", "plan acquire-contract", "plan verify-integration",
        "build graph", "build build", "build test", "build status", "build materialize",
    },
    "subagent": {
        "code search", "code type", "code signature", "code callers",
        "content search", "content fetch", "exec",  "evidence", "file-read",
        "repo diff", "repo history", "repo file",
    },
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
HELP_AUDIENCES = ("operator", "partner", "worker", "subagent")


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
    """`operator` with no HARNESS_ID, `partner`, else `worker`."""
    from .caller import caller
    from .ids import PARTNER

    who = caller(env)
    if who is None:
        return "operator"
    if who == PARTNER:
        return "partner"
    return "worker"


def gate(command: str, rest: list[str], env) -> None:
    """Refuse a worker caller anything outside its column."""
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
        # The caller's column of the inventory; `--all` for every one.
        return main_help(argv[1:], env)
    if argv and argv[0] in ("-h", "--help") and help_audience(env) == "worker":
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
    if command not in IMPLEMENTED:
        print(f"hx: {command}: unknown command; try `hx --help`", file=sys.stderr)
        return 2

    try:
        # The standing refusal runs before anything else, for every command.
        root = _root_for(rest, env)
        # Authority backs visibility: a worker caller is refused anything outside its column.
        gate(command, rest, env)
        if command == "code" and not any(a in {"-h", "--help"} for a in rest):
            shared = repo_cmd.code_request(root, rest, env)
            if shared is not None:
                import json
                print(json.dumps(shared["result"]))
                status = shared["exit_code"]
            else:
                status = IMPLEMENTED[command](rest, root, env=env)
            # A worker's graph search is receipted to its run for its binding to cite.
            from . import search_receipts
            search_receipts.receipt_graph_query(root, rest, status, env)
            return status
        return IMPLEMENTED[command](rest, root, env=env)
    except HxError as exc:
        print(f"hx: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
