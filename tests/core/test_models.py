"""`hx models` (TOOL_SKILL_PLAN step 3): provider lists diffed against the registry.

Every request goes through `hx.models_cmd.fetch`, which these tests replace: no network call
is ever made here. One fixture per provider listing shape; one deprecated ID, one missing
credential, one window mismatch.
"""

from __future__ import annotations

import json

import pytest

from hx import models_cmd

ANTHROPIC_KEY = "sk-ant-api03-test-key-anthropic-0000"
OPENAI_KEY = "sk-proj-test-key-openai-1111"
XAI_KEY = "xai-test-key-2222"
META_KEY = "meta-test-key-3333"

DOCS = """# Provider models

## Anthropic

- `claude-opus-5-5`
- `claude-sonnet-5-5`
- `claude-opus-4-1`

## OpenAI

- `gpt-6-astra`
- `gpt-5.5`

## xAI

- `grok-5`

## Meta

- `muse-wonder-1.0`

## TypeSafe

- `jev-1.13.0` (decision model, pinned in `hx.jev`; `jev-latest` is its alias)
"""

#: Anthropic: `{data: [...], has_more}` with max_input_tokens and an effort capability map.
ANTHROPIC = {
    "data": [
        {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
         "max_input_tokens": 1000000, "max_tokens": 128000,
         "capabilities": {"effort": {"low": {"supported": True}, "medium": {"supported": True},
                                     "high": {"supported": True}, "max": {"supported": False}}}},
        {"type": "model", "id": "claude-sonnet-5-5", "max_input_tokens": 500000},
        {"type": "model", "id": "claude-haiku-5-5", "max_input_tokens": 200000},
        {"type": "model", "id": "claude-opus-4-1", "lifecycle": "deprecated", "max_input_tokens": 200000},
    ],
    "has_more": False,
}
#: OpenAI: `{object: list, data: [...]}`, no window, many non-chat models.
OPENAI = {"object": "list", "data": [
    {"id": "gpt-6-astra", "object": "model", "created": 1, "owned_by": "openai"},
    {"id": "gpt-6-luna", "object": "model", "created": 1, "owned_by": "openai"},
    {"id": "gpt-5.5", "object": "model", "created": 1, "owned_by": "openai"},
    {"id": "whisper-1", "object": "model", "created": 1, "owned_by": "openai"},
]}
#: xAI language-models: `{models: [...]}` with `max_prompt_length` and reasoning efforts.
XAI = {"models": [
    {"id": "grok-5", "object": "model", "max_prompt_length": 2000000,
     "supported_reasoning_efforts": ["low", "high"]},
    {"id": "grok-5-mini", "object": "model", "max_prompt_length": 256000},
]}
#: Meta: `{data: [...]}` with `context_window`.
META = {"data": [{"id": "muse-wonder-1.0", "context_window": 1000000},
                 {"id": "muse-spark-1.3", "context_window": 262144}]}

BODIES = {
    "https://api.anthropic.com/v1/models?lifecycle=active&limit=1000": ANTHROPIC,
    "https://api.openai.com/v1/models": OPENAI,
    "https://api.x.ai/v1/language-models": XAI,
    "https://api.meta.ai/v1/models": META,
}


@pytest.fixture
def calls(monkeypatch):
    """Replace the one network function; record every request it is asked to make."""
    seen = []

    def fake(url, headers, timeout=models_cmd.TIMEOUT_S):
        seen.append({"url": url, "headers": dict(headers), "timeout": timeout})
        return json.loads(json.dumps(BODIES[url]))

    monkeypatch.setattr(models_cmd, "fetch", fake)
    return seen


@pytest.fixture
def registry(instance, tmp_path):
    models = {
        "claude-opus-5-5": {"window": 1000000, "autocompact_window": 250000, "threshold": 200000},
        # Configured above the provider's max_input_tokens (500000): a window mismatch.
        "claude-sonnet-5-5": {"window": 1000000, "autocompact_window": 250000, "threshold": 200000},
        # Configured but deprecated upstream: not in the active list.
        "claude-opus-4-1": {"window": 200000, "threshold": 100000},
    }
    (instance / "config" / "models.json").write_text(json.dumps(models))
    docs = tmp_path / "models.md"
    docs.write_text(DOCS)
    return docs


def _all_keys():
    return {"ANTHROPIC_API_KEY": ANTHROPIC_KEY, "OPENAI_API_KEY": OPENAI_KEY,
            "XAI_API_KEY": XAI_KEY, "META_API_KEY": META_KEY}


def test_every_provider_shape_is_diffed(instance, registry, calls):
    doc = models_cmd.report(instance, env=_all_keys(), docs_path=registry)
    assert list(doc["providers"]) == ["anthropic", "openai", "xai", "meta", "typesafe"]
    for block in doc["providers"].values():
        assert {"status", "source", "ids", "added", "removed", "deprecated", "window", "effort"} <= set(block)

    anthropic = doc["providers"]["anthropic"]
    assert anthropic["status"] == "ok" and anthropic["credential"] == "env:ANTHROPIC_API_KEY"
    assert anthropic["ids"] == ["claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5"]
    assert anthropic["deprecated"] == ["claude-opus-4-1"]
    assert anthropic["added"] == ["claude-haiku-5-5"]
    assert anthropic["removed"] == []
    assert anthropic["window"]["claude-sonnet-5-5"] == 500000
    assert anthropic["effort"]["claude-opus-5-5"] == ["low", "medium", "high"]

    openai = doc["providers"]["openai"]
    assert openai["added"] == ["gpt-6-luna"], "whisper-1 is not of the gpt family"
    assert openai["window"] == {} and openai["removed"] == []
    xai = doc["providers"]["xai"]
    assert xai["window"] == {"grok-5": 2000000, "grok-5-mini": 256000}
    assert xai["effort"] == {"grok-5": ["low", "high"]} and xai["added"] == ["grok-5-mini"]
    meta = doc["providers"]["meta"]
    assert meta["added"] == ["muse-spark-1.3"] and meta["window"]["muse-wonder-1.0"] == 1000000
    typesafe = doc["providers"]["typesafe"]
    assert typesafe["status"] == "pinned" and typesafe["ids"] == ["jev-1.13.0"]
    assert typesafe["added"] == [] and typesafe["removed"] == []


def test_requests_carry_each_providers_auth_and_a_5s_timeout(instance, registry, calls):
    models_cmd.report(instance, env=_all_keys(), docs_path=registry)
    by_url = {c["url"]: c for c in calls}
    anthropic = by_url["https://api.anthropic.com/v1/models?lifecycle=active&limit=1000"]
    assert anthropic["headers"] == {"X-Api-Key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"}
    assert by_url["https://api.openai.com/v1/models"]["headers"] == {"Authorization": f"Bearer {OPENAI_KEY}"}
    assert by_url["https://api.x.ai/v1/language-models"]["headers"] == {"Authorization": f"Bearer {XAI_KEY}"}
    assert by_url["https://api.meta.ai/v1/models"]["headers"] == {"Authorization": f"Bearer {META_KEY}"}
    assert {c["timeout"] for c in calls} == {5.0}
    assert len(calls) == 4, "one request per provider, no retry"


def test_a_missing_credential_is_unverified_and_makes_no_request(instance, registry, calls):
    doc = models_cmd.report(instance, providers=["anthropic"], env={}, docs_path=registry)
    block = doc["providers"]["anthropic"]
    assert block["status"] == "unverified" and block["credential"] is None
    assert "no credential" in block["reason"] and "OAuth" in block["reason"]
    assert calls == [] and doc["stale"] == []


def test_the_oauth_seed_token_is_never_used_for_anthropic(instance, registry, calls):
    (instance / "seed").mkdir(exist_ok=True)
    (instance / "seed" / "token").write_text("sk-ant-oat01-oauth-seed\n")
    doc = models_cmd.report(instance, providers=["anthropic"], env={}, docs_path=registry)
    assert doc["providers"]["anthropic"]["status"] == "unverified" and calls == []
    (instance / "seed" / "token").write_text("sk-ant-api03-seeded-key\n")
    doc = models_cmd.report(instance, providers=["anthropic"], env={}, docs_path=registry)
    assert doc["providers"]["anthropic"]["credential"] == "seed/token"
    assert calls[0]["headers"]["X-Api-Key"] == "sk-ant-api03-seeded-key"


def test_seed_files_and_the_env_file_are_credential_sources(instance, registry, calls, tmp_path):
    env_file = tmp_path / "hx.env"
    env_file.write_text(f"XAI_API_KEY={XAI_KEY}\n")
    (instance / "config" / "auth.json").write_text(json.dumps({"mode": "auto", "env_file": str(env_file)}))
    (instance / "seed").mkdir(exist_ok=True)
    (instance / "seed" / "codex-token").write_text(OPENAI_KEY + "\n")
    doc = models_cmd.report(instance, providers=["openai", "xai"], env={}, docs_path=registry)
    assert doc["providers"]["openai"]["credential"] == "seed/codex-token"
    assert doc["providers"]["xai"]["credential"] == "env-file:XAI_API_KEY"


def test_an_http_failure_is_an_error_with_its_status(instance, registry, monkeypatch):
    def refused(url, headers, timeout=5.0):
        raise models_cmd.FetchError(401, "HTTP 401 Unauthorized")

    monkeypatch.setattr(models_cmd, "fetch", refused)
    block = models_cmd.report(instance, providers=["openai"], env=_all_keys(),
                              docs_path=registry)["providers"]["openai"]
    assert block["status"] == "error" and block["http"] == 401 and block["ids"] == []


def test_expect_current_exits_3_on_a_deprecated_id_and_a_window_mismatch(instance, registry, calls, capsys):
    code = models_cmd.main(["--provider", "anthropic", "--expect", "current", "--json",
                            "--docs", str(registry)], instance, env=_all_keys())
    assert code == 3
    doc = json.loads(capsys.readouterr().out)
    block = doc["providers"]["anthropic"]
    assert block["missing"] == ["claude-opus-4-1"]
    assert block["window_over"] == {"claude-sonnet-5-5": {"configured": 1000000, "max_input_tokens": 500000}}
    assert any("claude-opus-4-1" in line for line in doc["stale"])
    assert any("claude-sonnet-5-5 window 1000000" in line for line in doc["stale"])


def test_expect_current_passes_when_the_registry_is_current(instance, registry, calls, capsys):
    (instance / "config" / "models.json").write_text(json.dumps(
        {"claude-opus-5-5": {"window": 1000000, "threshold": 200000}}))
    assert models_cmd.main(["--provider", "anthropic", "--expect", "current", "--docs", str(registry)],
                           instance, env=_all_keys()) == 0
    assert "HX-MODELS ok=1" in capsys.readouterr().out


def test_unverified_providers_do_not_fail_expect_current(instance, registry, calls):
    assert models_cmd.main(["--expect", "current", "--docs", str(registry)], instance, env={}) == 0


def test_no_credential_is_ever_printed_and_nothing_is_written(instance, registry, calls, capsys):
    before = {p: p.read_bytes() for p in instance.rglob("*") if p.is_file()}
    models_cmd.main(["--docs", str(registry)], instance, env=_all_keys())
    models_cmd.main(["--json", "--docs", str(registry)], instance, env=_all_keys())
    out = capsys.readouterr()
    for key in _all_keys().values():
        assert key not in out.out and key not in out.err
    after = {p: p.read_bytes() for p in instance.rglob("*") if p.is_file()}
    assert after == before, "hx models writes nothing"


def test_a_credential_echoed_by_a_provider_is_scrubbed(instance, registry, monkeypatch):
    def echo(url, headers, timeout=5.0):
        raise models_cmd.FetchError(None, f"transport: bad key {headers['Authorization']}")

    monkeypatch.setattr(models_cmd, "fetch", echo)
    doc = models_cmd.report(instance, providers=["meta"], env=_all_keys(), docs_path=registry)
    assert META_KEY not in json.dumps(doc) and "*" * len(META_KEY) in doc["providers"]["meta"]["reason"]


def test_usage_errors_exit_2_and_workers_are_refused(run_hx, instance):
    bad = run_hx("models", "--provider", "nope", env_extra={"HARNESS_ROOT": str(instance)})
    assert bad.returncode == 2
    worker = run_hx("models", env_extra={"HARNESS_ROOT": str(instance), "HARNESS_ID": "eng-001"})
    assert worker.returncode == 1 and "refuse" in worker.stderr


def test_the_shipped_docs_list_parses_per_provider():
    docs = models_cmd.documented(models_cmd.DOCS)
    assert docs is not None
    assert "claude-opus-5-5" in docs["anthropic"] and docs["typesafe"] == ["jev-1.13.0"]
    assert all(models_cmd.provider_of(i) == "openai" for i in docs["openai"])
