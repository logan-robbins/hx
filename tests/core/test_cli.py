"""Subcommand dispatch for every command in spec 08 (goal build-1 item 1)."""

from __future__ import annotations

import pytest

from hx.cli import COMMANDS, IMPLEMENTED, NOT_IMPLEMENTED

#: Spec 08's table, plus install/up/doctor/ui/show from 17.2. The v1 cut (spec 14 D25)
#: removed `repo`, `push` and `upgrade`.
SPEC_08_COMMANDS = {
    "launch", "install", "doctor", "show", "ui", "up",
    "dispatch", "goal", "task", "compose", "seam", "restart", "complete", "resume", "amend",
    "read", "bench", "board", "flush", "companion", "wake", "heartbeat", "metrics",
    "goals", "archive",
    # Episode memory (docs/memory.md), not in spec 08's own table.
    "memory",
    # File memory (CONTRACTS.md `hx read` and `hx recall`), likewise post-spec-08.
    "recall",
    # Planned prompt compiler (`compile --planned`), and the persona every start.sh renders
    # from the role persona, `purpose` and the learnings; likewise post-spec-08.
    "compile", "persona",
    # Addenda distillation: shrinks the Work Item and tasks.json record back
    # down after resumes and amends accumulate; likewise post-spec-08.
    "distill",
    # Continuity v2: registered incremental capture and its resident observer.
    "observe",
    "capture",
    "progress",
    "check",
    "map",
    "plan",
    "evidence",
    "loop", "tool-exec", "tools",
    # Central learnings (docs/learnings.md) and the handoff contract (docs/multi-instance.md §6).
    "learn", "handoff",
    # Context economy: code index, content store, isolated execution, Jev sift
    # (docs/search-and-retrieval.md), likewise post-spec-08.
    "code", "content", "exec", "sift", "discipline", "cache",
    # Provider model lists against the registry (TOOL_SKILL_PLAN step 3).
    "models",
}


def test_every_spec_08_command_is_dispatched():
    assert set(COMMANDS) == SPEC_08_COMMANDS, set(COMMANDS) ^ SPEC_08_COMMANDS


def test_no_command_is_both_implemented_and_not():
    assert not (set(IMPLEMENTED) & set(NOT_IMPLEMENTED))


@pytest.mark.parametrize("command", sorted(NOT_IMPLEMENTED))
def test_unimplemented_commands_exit_2_with_the_build_goal(run_hx, tmp_path, command):
    result = run_hx(command, env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 2
    assert result.stderr.strip() == f"hx: {command}: not implemented (build-{NOT_IMPLEMENTED[command]})"
    assert result.stdout == ""


def test_unknown_command_exits_2(run_hx, tmp_path):
    result = run_hx("frobnicate", env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 2
    assert "unknown command" in result.stderr


def test_bare_hx_prints_usage_and_exits_2(run_hx, tmp_path):
    result = run_hx(env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 2
    assert "usage: hx <command>" in result.stdout


def test_help_exits_0(run_hx, tmp_path):
    result = run_hx("--help", env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 0
    assert "usage: hx <command>" in result.stdout


def test_python_dash_m_hx_exists(run_hx, tmp_path):
    """tests/guard requires `python -m hx` to work; prove __main__.py is importable."""
    result = run_hx("--version", env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 0
    assert result.stdout.startswith("hx ")


def test_hook_entrypoint_knows_the_spec_09_events():
    from hx.hooks import EVENTS

    assert set(EVENTS) == {
        "context", "log", "subagent-start", "subagent-stop",
        "subagent-result", "stop", "precompact", "postcompact",
        # The Partner's PreToolUse guard over config/partner/guard.json (spec 09.1).
        "guard",
        # The Companion's own `stop`, in its own home — the other half of the pass protocol
        # (spec 10), not one of the agent's own.
        "companion-stop",
        # Explicit normalized observation routes for planned-run capture.
        "request", "log-failure", "tool-start", "message", "model-response",
        "stop-failure", "stop-cancelled", "session-end",
    }


# --- exposure (TOOL_SKILL_PLAN step 1) -------------------------------------------------------------

from hx import cli as cli_mod  # noqa: E402

WORKER_SIDE = cli_mod.AUDIENCES["worker"] | cli_mod.AUDIENCES["subagent"] | cli_mod.AUDIENCES["runtime"]
#: Every Partner and operator entry a worker must be refused at the CLI. `plan audit|finish|
#: materialize` are the Partner's, but planning grants a worker its own assignment and refuses
#: the rest itself (tests/core/test_unit_execution.py), so the CLI defers to it.
REFUSED = sorted((cli_mod.AUDIENCES["partner"] | cli_mod.AUDIENCES["operator"]) - WORKER_SIDE
                 - cli_mod.OWN_ASSIGNMENT)
ACCEPTED = sorted(cli_mod.AUDIENCES["worker"])


def _dispatch_recorded(monkeypatch, instance, env: dict[str, str]):
    """`cli.main` with every module stubbed: what reaches dispatch is recorded, nothing runs."""
    for key in [k for k in list(__import__("os").environ) if k.startswith(("HARNESS_", "HX_"))]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HARNESS_ROOT", str(instance))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reached = []
    for name in list(cli_mod.IMPLEMENTED):
        monkeypatch.setitem(cli_mod.IMPLEMENTED, name,
                            lambda rest, root, env=None, _name=name: reached.append((_name, rest)) or 0)
    return reached


def test_audiences_cover_every_command_and_nothing_else():
    families = {entry.split()[0] for entries in cli_mod.AUDIENCES.values() for entry in entries}
    assert families <= set(cli_mod.COMMANDS), families - set(cli_mod.COMMANDS)
    assert set(cli_mod.COMMANDS) <= families, set(cli_mod.COMMANDS) - families
    # The subagent is a worker's child sharing its HARNESS_ID: its column is inside the worker's.
    assert cli_mod.AUDIENCES["subagent"] <= cli_mod.AUDIENCES["worker"]
    assert cli_mod.AUDIENCES["companion"] == set()


def test_a_worker_is_refused_every_partner_and_operator_command(monkeypatch, instance, capsys):
    reached = _dispatch_recorded(monkeypatch, instance, {"HARNESS_ID": "eng-001", "HX_ROLE": "engineer"})
    for entry in REFUSED:
        code = cli_mod.main(entry.split())
        err = capsys.readouterr().err
        assert code == 1 and "refuse" in err and f"`hx {entry}`" in err, (entry, code, err)
    assert reached == [], f"refused commands reached their modules: {reached}"


def test_a_worker_may_run_every_worker_command(monkeypatch, instance, capsys):
    reached = _dispatch_recorded(monkeypatch, instance, {"HARNESS_ID": "eng-001", "HX_ROLE": "engineer"})
    for entry in ACCEPTED:
        code = cli_mod.main(entry.split())
        assert code == 0, (entry, capsys.readouterr().err)
    assert len(reached) == len(ACCEPTED)


def test_the_partner_and_the_operator_are_not_gated(monkeypatch, instance):
    for env in ({"HARNESS_ID": "partner"}, {}):
        reached = _dispatch_recorded(monkeypatch, instance, env)
        for entry in REFUSED:
            assert cli_mod.main(entry.split()) == 0, (env, entry)
        assert len(reached) == len(REFUSED)


def test_the_capability_resolver_reads_subcommands_and_flags():
    resolve = cli_mod.resolve_capability
    assert resolve("memory", ["search", "q", "--all-roles"]) == "memory search --all-roles"
    assert resolve("memory", ["search", "q"]) == "memory search"
    assert resolve("map", ["--root", "/tmp/x", "publish", "--file", "f"]) == "map publish"
    assert resolve("plan", ["frobnicate"]) is None
    assert cli_mod.worker_may_run("plan", None) is False, "every plan entry is the Partner's"
    assert cli_mod.worker_may_run("code", None) is True, "code has worker entries; argparse decides"


def test_operator_refusals_name_the_operator(run_hx, instance):
    result = run_hx("doctor", env_extra={"HARNESS_ROOT": str(instance), "HARNESS_ID": "eng-001"})
    assert result.returncode == 1
    assert "refuse: `hx doctor` is the operator's" in result.stderr and "eng-001" in result.stderr
    result = run_hx("map", "publish", "--root", str(instance),
                    env_extra={"HARNESS_ROOT": str(instance), "HARNESS_ID": "eng-001"})
    assert result.returncode == 1 and "`hx map publish` is the Partner's" in result.stderr


@pytest.mark.parametrize("identity, audience", [
    ({}, "operator"),
    ({"HARNESS_ID": "partner", "HX_ROLE": "partner"}, "partner"),
    ({"HARNESS_ID": "eng-001", "HX_ROLE": "engineer"}, "worker"),
    ({"HARNESS_ID": "eng-001", "HX_ROLE": "companion"}, "companion"),
])
def test_hx_help_prints_the_callers_column(run_hx, instance, identity, audience):
    import json

    result = run_hx("help", "--json", env_extra={"HARNESS_ROOT": str(instance), **identity})
    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    assert shown["audience"] == audience
    assert shown["commands"] == sorted(cli_mod.AUDIENCES[audience])
    assert shown["caller"] == identity.get("HARNESS_ID")


def test_a_workers_help_names_no_partner_or_operator_command(run_hx, instance):
    result = run_hx("help", env_extra={"HARNESS_ROOT": str(instance), "HARNESS_ID": "eng-001"})
    assert result.returncode == 0 and "the worker view" in result.stdout
    listed = {line.split()[0] for line in result.stdout.splitlines()[1:] if line.strip()}
    assert listed == {entry.split()[0] for entry in cli_mod.AUDIENCES["worker"]}
    for word in ("dispatch", "bench", "doctor", "compile", "handoff", "publish", "accept"):
        assert word not in result.stdout, word
    # `hx --help` under a worker is the same filtered view, never the whole list.
    dashed = run_hx("--help", env_extra={"HARNESS_ROOT": str(instance), "HARNESS_ID": "eng-001"})
    assert dashed.returncode == 0 and dashed.stdout == result.stdout


def test_hx_help_all_prints_every_column(run_hx, instance):
    import json

    result = run_hx("help", "--all", "--json", env_extra={"HARNESS_ROOT": str(instance), "HARNESS_ID": "eng-001"})
    assert result.returncode == 0
    columns = json.loads(result.stdout)["audiences"]
    assert set(columns) == set(cli_mod.HELP_AUDIENCES)
    assert columns["partner"] == sorted(cli_mod.AUDIENCES["partner"])
    text = run_hx("help", "--all", env_extra={"HARNESS_ROOT": str(instance)})
    assert text.returncode == 0 and "operator:" in text.stdout and "companion:" in text.stdout
    # The operator's `hx --help` is still the whole usage text.
    usage = run_hx("--help", env_extra={"HARNESS_ROOT": str(instance)})
    assert usage.returncode == 0 and "usage: hx <command>" in usage.stdout


@pytest.mark.parametrize('command', ['memory', 'recall'])
def test_planned_workers_cannot_import_or_create_episodic_memory(run_hx, tmp_path, command):
    result = run_hx(command, env_extra={'HARNESS_ROOT': str(tmp_path / 'planned'), 'HX_CONTINUITY_RUN': 'current-run'})
    assert result.returncode == 2 and 'no episodic memory' in result.stderr
    assert not (tmp_path / 'planned/state').exists()


#: TOOL_SKILL_PLAN step 8: every worker-column command's `--help` carries an example. `code *`
#: is excluded: its parser lives in src/hx/code_index.py, which the code-graph work owns
#: (GRAPH_PLAN.md); that plan adds its own examples.
HELP_EXAMPLE_ENTRIES = sorted(
    entry for entry in cli_mod.AUDIENCES["worker"] | cli_mod.AUDIENCES["subagent"]
    if entry.split()[0] != "code"
)


@pytest.mark.parametrize("entry", HELP_EXAMPLE_ENTRIES)
def test_every_worker_command_help_has_an_example(entry, tmp_path, capsys):
    words = [word for word in entry.split() if not word.startswith("--")]
    with pytest.raises(SystemExit) as exit_info:
        IMPLEMENTED[words[0]](words[1:] + ["--help"], tmp_path, env={})
    assert exit_info.value.code == 0
    assert "example" in capsys.readouterr().out, entry


def test_the_help_example_sweep_covers_the_worker_column():
    assert {"complete", "task", "progress", "learn propose", "tools discover", "discipline pull"} <= set(
        HELP_EXAMPLE_ENTRIES)
