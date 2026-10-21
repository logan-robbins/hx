"""The Codex search route around the guard (docs/codex-search-guard.md): the toolbox header
helper, the installer wiring both ways, and the real `codex exec` scenario."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from .conftest import clean_env
from .search_guard_fixture import INJECTION, SECRET, run_codex, serve_shields
from .test_adapter_codex import _codex_token, _fake_codex, _no_login_home

#: The gate of test_installed_codex_rollout_and_hook_contract: only an explicitly chosen binary.
SCENARIO = pytest.mark.skipif(not os.environ.get("HX_CODEX_TEST_BIN"), reason="requires an explicitly selected installed Codex CLI; local provider fixture only")

ADAPTER = Path(__file__).resolve().parents[2] / "src" / "hx" / "skeleton" / "adapters" / "codex"
HEADERS = ADAPTER / "toolbox_headers.py"
SKILLS = Path(__file__).resolve().parents[2] / "src" / "hx" / "skills"
TOOLBOX = "https://acct.services.ai.azure.com/api/projects/proj/toolboxes/search/mcp?api-version=v1"
SAFETY = "https://shields.cognitiveservices.azure.com"
MATCHER = "mcp__foundry__web_search"


def _headers(tmp_path, az_script=None, **env):
    """Run toolbox_headers.py with only a fake `az` (or none) on PATH."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    if az_script is not None:
        (fakebin / "az").write_text("#!/bin/sh\n" + az_script)
        (fakebin / "az").chmod(0o755)
    return subprocess.run([sys.executable, str(HEADERS)], env={"PATH": str(fakebin), **env},
                          capture_output=True, text=True, timeout=30, check=False)


def test_headers_prefer_the_environment_token(tmp_path):
    done = _headers(tmp_path, "exit 9\n", FOUNDRY_TOOLBOX_TOKEN="stub-toolbox-token")
    assert (done.returncode, done.stderr) == (0, "")
    assert json.loads(done.stdout) == {"Authorization": "Bearer stub-toolbox-token"}


def test_headers_fall_back_to_an_az_token_for_the_foundry_resource(tmp_path):
    record = tmp_path / "az-argv"
    done = _headers(tmp_path, f'printf "%s\\n" "$@" > {record}\necho stub-az-token\n')
    assert json.loads(done.stdout) == {"Authorization": "Bearer stub-az-token"}
    assert record.read_text().split() == [
        "account", "get-access-token", "--resource", "https://ai.azure.com", "--query", "accessToken", "-o", "tsv"]


@pytest.mark.parametrize("az_script", [None, "echo 'az: not logged in (stub)' >&2\nexit 1\n", "exit 0\n"])
def test_headers_fail_with_one_stderr_line_and_no_stdout(tmp_path, az_script):
    done = _headers(tmp_path, az_script)
    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr.startswith("toolbox_headers: ") and done.stderr.count("\n") == 1


def _install(instance, tmp_path, item_id, search_guard=None):
    """adapters/codex/install.sh exactly as test_codex_install_writes_home_and_provisions_auth."""
    _codex_token(instance)
    if search_guard is not None:
        (instance / "config" / "codex.json").write_text(json.dumps({"search_guard": search_guard}))
    return subprocess.run(
        ["bash", str(instance / "adapters" / "codex" / "install.sh"), item_id],
        env=clean_env(HARNESS_ROOT=str(instance), HOME=str(_no_login_home(tmp_path)),
                      HX_CODEX_BIN=str(_fake_codex(tmp_path)), HX_SKILLS_DIR=str(SKILLS)),
        capture_output=True, text=True)


def _home_config(instance, item_id):
    return tomllib.loads((instance / "run" / item_id / "home" / "config.toml").read_text())


def test_install_without_search_guard_keeps_hosted_search_cached(instance, tmp_path):
    done = _install(instance, tmp_path, "eng-001")
    assert done.returncode == 0, done.stderr
    config = _home_config(instance, "eng-001")
    assert config["web_search"] == "cached"
    assert "mcp_servers" not in config and "PreToolUse" not in config["hooks"]
    assert "search_guard" not in json.dumps(config)


@pytest.mark.parametrize("item_id", ["eng-001", "partner"])
def test_install_with_search_guard_routes_search_through_the_guarded_toolbox(instance, tmp_path, item_id):
    done = _install(instance, tmp_path, item_id, {"toolbox_url": TOOLBOX, "content_safety_endpoint": SAFETY})
    assert done.returncode == 0, done.stderr
    config, root = _home_config(instance, item_id), str(instance)
    python = shlex.split(config["hooks"]["Stop"][0]["hooks"][0]["command"])[0]
    assert config["web_search"] == "disabled"
    assert config["mcp_servers"] == {"foundry": {
        "url": TOOLBOX, "http_headers_helper": shlex.join([python, f"{root}/adapters/codex/toolbox_headers.py"]),
        "enabled_tools": ["web_search"], "default_tools_approval_mode": "approve", "tool_timeout_sec": 60,
        "tools": {"web_search": {"output_token_limit": 4000}}}}
    log = instance / "run" / item_id / "search-guard.jsonl"
    commands = {}
    for event, mode, timeout, status in (("PreToolUse", "pre", 20, "Checking the search query"),
                                         ("PostToolUse", "post", 60, "Scanning search results with Prompt Shields")):
        [group] = [group for group in config["hooks"][event] if group.get("matcher") == MATCHER]
        [handler] = group["hooks"]
        commands[mode] = shlex.split(handler.pop("command"))
        assert commands[mode] == [python, f"{root}/adapters/codex/search_guard.py", mode,
                                  "--endpoint", SAFETY, "--log", str(log)]
        assert handler == {"type": "command", "timeout": timeout, "statusMessage": status}
    # The Partner keeps its own unmatched hx guard beside the search one; a worker has only this.
    assert len(config["hooks"]["PreToolUse"]) == (2 if item_id == "partner" else 1)
    ran = subprocess.run(commands["pre"], input=json.dumps({"tool_input": {"search_query": "weather"}}),
                         env=clean_env(), capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout) == (0, "")
    assert json.loads(log.read_text())["event"] == "query_allowed"


@pytest.mark.parametrize("search_guard, named", [
    ({"toolbox_url": TOOLBOX}, "lacks content_safety_endpoint"),
    ({"content_safety_endpoint": SAFETY}, "lacks toolbox_url"),
    ({"toolbox_url": " ", "content_safety_endpoint": SAFETY}, "lacks toolbox_url"),
    ({}, "lacks toolbox_url and lacks content_safety_endpoint"),
    ("on", "is not an object"),
])
def test_install_refuses_a_partial_search_guard(instance, tmp_path, search_guard, named):
    done = _install(instance, tmp_path, "eng-001", search_guard)
    assert done.returncode != 0
    assert f"config/codex.json search_guard {named}" in done.stderr
    assert not (instance / "run" / "eng-001" / "home" / "config.toml").exists()


@pytest.fixture
def scenario(tmp_path):
    """One real `codex exec` searching for a query through the stub MCP server and the guard."""
    def run(query):
        with serve_shields() as shields:
            return run_codex(os.environ["HX_CODEX_TEST_BIN"], tmp_path, query, shields)
    return run


@SCENARIO
def test_scenario_discovery_names_the_tool_the_matcher_expects(scenario):
    run = scenario("seattle weather")
    assert "mcp__local" in [tool.get("name") for tool in run.discovered if tool.get("type") == "namespace"]
    assert {row["tool_name"] for row in run.audit} == {"mcp__local__web_search"}


@SCENARIO
def test_scenario_clean_result_reaches_the_model_after_one_scan(scenario):
    run = scenario("seattle weather")
    assert [call["search_query"] for call in run.calls] == ["seattle weather"]
    assert [row["event"] for row in run.audit] == ["query_allowed", "result_allowed"]
    assert [(sent["key"], sent["body"]["documents"]) for sent in run.shield_requests] == [
        ("stub-key", ["Stub result for seattle weather: sunny, 18C."])]
    assert "Stub result for seattle weather: sunny" in run.seen
    assert "Prompt Shields found no injection" in json.dumps(run.model_requests[-1])
    assert '"type":"mcp_tool_call"' in run.stdout


@SCENARIO
def test_scenario_injected_result_reaches_the_model_as_the_block_reason(scenario):
    run = scenario("poison test")
    assert len(run.calls) == 1
    assert [row["event"] for row in run.audit] == ["query_allowed", "result_blocked"]
    assert "Search result withheld" in run.seen and INJECTION not in run.seen
    assert INJECTION not in json.dumps(run.model_requests), "no request to the model carries it"


@SCENARIO
def test_scenario_injected_citation_title_is_scanned_and_withheld(scenario):
    """Codex forwards a resource item whole, `_meta` included; the toolbox puts page titles there."""
    run = scenario("titled weather")
    assert [row["event"] for row in run.audit] == ["query_allowed", "result_blocked"]
    assert run.audit[1]["urls"] == ["https://evil.example/page"]
    assert "https://evil.example/page" in run.seen and INJECTION not in json.dumps(run.model_requests)


@SCENARIO
def test_scenario_credential_shaped_query_never_reaches_the_server(scenario):
    run = scenario("lookup " + SECRET)
    assert (run.calls, run.shield_requests) == ([], [])
    assert [row["event"] for row in run.audit] == ["query_denied"]
    assert "denied by the search guard" in run.seen
