from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from hx import hook_contract, native_producer
from hx.continuity_store import Conflict, ContinuityStore, SCHEMA_VERSION
from hx.errors import ValidationError
from .conftest import SRC


def configure(instance, flavor):
    path = instance / "config" / "eng-001" / "harness.json"
    config = json.loads(path.read_text())
    config["flavor"] = flavor
    path.write_text(json.dumps(config))


def start(ledger, task="T"):
    with ledger.transaction() as tx:
        tx.put_task(task, {"goal": "Capture belongs to this exact assignment."}, expected_revision=0)
        return tx.start_run(task, 1, "eng-001")


def identity(run, adapter, launch="launch-one"):
    return {"HX_CONTINUITY_RUN": run, "HX_CONTINUITY_LAUNCH": launch, "HX_CONTINUITY_ADAPTER": adapter}


def commands(home, adapter, event="PostToolUse"):
    if adapter == "pi":
        contract = json.loads((home / "extensions" / "hx" / "hook-contract.json").read_text())
        return [shlex.join([contract["command"], *contract["args"], "log"])]
    if adapter in {"codex", "grok"}:
        config = tomllib.loads((home / "config.toml").read_text())
    else:
        config = json.loads((home / ("muse/settings.json" if adapter == "meta" else "settings.json")).read_text())
    return [hook["command"] for block in config["hooks"][event] for hook in block["hooks"]]


@pytest.mark.parametrize("adapter", ["claude", "codex", "grok", "meta", "pi"])
def test_installed_hook_survives_cleared_environment_and_worker_reuse(instance, child_env, tmp_path, adapter):
    configure(instance, adapter)
    hook = instance / "bin" / "hx-hook"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(SRC)!r})\nfrom hx.hooks import main\nraise SystemExit(main())\n")
    hook.chmod(0o755)
    for flavor in ("codex", "grok", "meta", "pi"):
        token = instance / "seed" / (flavor + "-token")
        token.write_text("fixture-key")
        token.chmod(0o600)
    fake_home = tmp_path / "isolated-user-home"
    auth = fake_home / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"fixture":true}')
    with ContinuityStore(instance) as ledger:
        run = start(ledger)
        env = child_env(HARNESS_ROOT=str(instance), HX_PYTHON=sys.executable, HX_CODEX_BIN=sys.executable,
                        HOME=str(fake_home), **identity(run, adapter))
        result = subprocess.run(["bash", str(instance / "adapters" / adapter / "install.sh"), "eng-001"],
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        command = next(command for command in commands(instance / "run" / "eng-001" / "home", adapter) if command.endswith(" log"))
        assert "--continuity-run" in command and run in command
        payload = {"session_id": "S1", "tool_name": "Bash", "tool_use_id": "C1", "tool_response": "Passed"}
        # No HARNESS_* or HX_* variables survive, as on an environment-clearing host.
        result = subprocess.run(shlex.split(command), input=json.dumps(payload), capture_output=True,
                                text=True, env={"PATH": env["PATH"], "HOME": str(fake_home), "PYTHONPATH": str(SRC)})
        assert result.returncode == 0 and not result.stderr, result.stderr
        assert ledger.db.execute("SELECT count(*) FROM events WHERE run_id=?", (run,)).fetchone()[0] == 1
        if adapter in {"claude", "meta", "grok"}:
            events = ["StopFailure", "SessionEnd"] + (["StopCancelled"] if adapter == "grok" else [])
            for event in events:
                terminal = commands(instance / "run" / "eng-001" / "home", adapter, event)[0]
                body = {"session_id": "S1", "hook_event_name": event, "promptId": "earlier-turn",
                        "error": "rate_limit", "errorDetails": "Retry after reset.", "reason": "user_interrupt",
                        "lastAssistantMessage": "Rendered error", "private_reasoning": "PRIVATE"}
                observed = subprocess.run(shlex.split(terminal), input=json.dumps(body), capture_output=True,
                    text=True, env={"PATH": env["PATH"], "PYTHONPATH": str(SRC)})
                assert observed.returncode == 0 and not observed.stdout and not observed.stderr, observed.stderr
                item = json.loads(ledger.db.execute("SELECT payload FROM events ORDER BY seq DESC LIMIT 1").fetchone()[0])["observation"]
                assert item["kind"] == "boundary" and item["data"]["settled"] is False
                assert item["data"]["prompt_id"] == "earlier-turn" and item["data"]["error_details"] == "Retry after reset."
                assert "PRIVATE" not in "\n".join(ledger.db.iterdump())
        with ledger.transaction() as tx:
            tx.finish_run(run, "stopped")
        newer = start(ledger, "new")
        configure(instance, "codex" if adapter != "codex" else "claude")
        reused = {**env, **identity(newer, "codex", "different-launch"), "HARNESS_ID": "eng-001"}
        result = subprocess.run(shlex.split(command), input=json.dumps({**payload, "tool_use_id": "late"}),
                                capture_output=True, text=True, env=reused)
        assert result.returncode == 0 and not result.stderr, result.stderr
        assert ledger.db.execute("SELECT count(*) FROM events WHERE run_id=?", (newer,)).fetchone()[0] == 0
        with ledger.transaction() as tx:
            assert native_producer.status(tx, run)["pending_deliveries"] == 1


def test_launch_identity_cannot_be_reassigned_and_partial_contract_refuses(instance):
    configure(instance, "meta")
    with ContinuityStore(instance) as ledger:
        run = start(ledger)
        env = identity(run, "meta")
        first = hook_contract.installation_args(instance, "eng-001", "meta", env)
        assert hook_contract.installation_args(instance, "eng-001", "meta", env) == first
        with pytest.raises(ValidationError, match="together"):
            hook_contract.installation_args(instance, "eng-001", "meta", {"HX_CONTINUITY_RUN": run})
        with ledger.transaction() as tx:
            tx.finish_run(run, "stopped")
        newer = start(ledger, "next")
        with pytest.raises(Conflict, match="already bound"):
            hook_contract.installation_args(instance, "eng-001", "meta", identity(newer, "meta"))
        with pytest.raises(Conflict, match="registered"):
            hook_contract.validate(ledger, run_id=newer, launch_id="launch-one", adapter="meta", worker_id="eng-001")


def test_schema_eleven_upgrade_retains_existing_runs_and_adds_launch_contracts(tmp_path):
    with ContinuityStore(tmp_path) as ledger:
        run = start(ledger)
        ledger.db.execute("DROP TABLE native_launch_contracts")
        ledger.db.execute("PRAGMA user_version=11")
    with ContinuityStore(tmp_path) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert upgraded.db.execute("SELECT 1 FROM runs WHERE run_id=?", (run,)).fetchone()
        assert upgraded.db.execute("SELECT count(*) FROM native_launch_contracts").fetchone()[0] == 0


@pytest.mark.skipif(not os.environ.get("HX_PI_EXTENSION_LOADER"), reason="requires an explicit installed Pi extension loader; no model calls")
def test_installed_pi_loader_delivers_with_frozen_contract(instance, child_env, tmp_path):
    import shutil
    configure(instance, "pi")
    hook = instance / "bin" / "hx-hook"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!{sys.executable}\nimport sys, io\nsys.path.insert(0, {str(SRC)!r})\nfrom hx.hooks import main\nraw = sys.stdin.read()\nassert 'PRIVATE' not in raw, 'private fields reached hook pipe'\nsys.stdin = io.StringIO(raw)\nraise SystemExit(main())\n")
    hook.chmod(0o755)
    extension = tmp_path / "native-extension" / "index.ts"
    extension.parent.mkdir()
    shutil.copyfile(instance / "adapters" / "pi" / "extension" / "index.ts", extension)
    script = tmp_path / "load.mjs"
    script.write_text('''import { pathToFileURL } from "node:url";
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
const [loader, extension, cwd, hook] = process.argv.slice(2);
const {loadExtensions} = await import(pathToFileURL(loader).href);
const result = await loadExtensions([extension], cwd);
if (result.errors.length) throw new Error(JSON.stringify(result.errors));
if (result.extensions.length !== 1) throw new Error("extension not loaded");
// Changes after module load cannot retarget this session's callbacks.
writeFileSync(join(dirname(extension), "hook-contract.json"), "{}");
process.env.HX_CONTINUITY_RUN = "different-run";
process.env.HX_CONTINUITY_LAUNCH = "different-launch";
process.env.HX_CONTINUITY_ADAPTER = "codex";
const handlers = result.extensions[0].handlers.get("tool_result");
if (!handlers?.length) throw new Error("tool_result handler missing");
for (const handler of handlers) await handler({toolName:"bash",toolCallId:"native-call",input:{command:"true"},
 content:[{type:"text",text:"Native loader callback delivered."}],isError:false},
 {sessionManager:{getSessionFile:()=>"native-session"}});
const inputs = result.extensions[0].handlers.get("input");
if (!inputs?.length) throw new Error("input handler missing");
for (const handler of inputs) await handler({type:"input",text:"Exact native request.",source:"interactive"},
 {sessionManager:{getSessionFile:()=>"native-session"}});
const messages = result.extensions[0].handlers.get("message_end");
if (!messages?.length) throw new Error("message_end handler missing");
for (let count=0; count<2; count++) for (const handler of messages) await handler({type:"message_end",message:{
 role:"assistant",content:[{type:"thinking",thinking:"PRIVATE THOUGHT",text:"PRIVATE TEXT"},
 {type:"text",text:"Use the source receipt when admitting dependent work.",textSignature:"PRIVATE SIGNATURE"},
 {type:"toolCall",id:"proposed-call",name:"read",arguments:{path:"src/x"},thoughtSignature:"PRIVATE SIGNATURE"},
 {type:"image",data:"PRIVATE IMAGE"}],usage:{input:11,output:7,cacheRead:2,cacheWrite:0,totalTokens:20,private:"PRIVATE USAGE"},
 stopReason:"length",providerMetadata:"PRIVATE METADATA"}}, {sessionManager:{getSessionFile:()=>"native-session"}});
// Tool results already use the admission/result route and must not be relayed twice.
for (const handler of messages) await handler({type:"message_end",message:{role:"toolResult",content:[]}}, {});
writeFileSync(hook, ["#!/bin/sh", "exit 2", ""].join(String.fromCharCode(10)));
const denied = await inputs[0]({type:"input",text:"Unverified context.",source:"interactive"},
 {sessionManager:{getSessionFile:()=>"native-session"}});
if (denied?.action !== "handled") throw new Error("refused input reached agent processing");
console.log("installed Pi loader and callback completed");
''')
    with ContinuityStore(instance) as ledger:
        run = start(ledger)
        args = hook_contract.installation_args(instance, "eng-001", "pi", identity(run, "pi"))
        (extension.parent / "hook-contract.json").write_text(json.dumps({"command": str(hook),
            "args": ["--root", str(instance), "--id", "eng-001", *args]}))
        isolated_home = tmp_path / "isolated-loader-home"
        isolated_home.mkdir()
        result = subprocess.run(["node", str(script), os.environ["HX_PI_EXTENSION_LOADER"], str(extension), str(tmp_path), str(hook)],
                                env=child_env(HOME=str(isolated_home), PI_CODING_AGENT_DIR=str(isolated_home / "pi")),
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        row = ledger.db.execute("SELECT payload FROM events WHERE run_id=? AND kind='tool_result'", (run,)).fetchone()
        assert row and "Native loader callback delivered." in row[0], result.stderr
        request = ledger.db.execute("SELECT payload FROM events WHERE run_id=? AND kind='request'", (run,)).fetchone()
        assert request and "Exact native request." in request[0]
        messages = [json.loads(row[0]) for row in ledger.db.execute(
            "SELECT payload FROM events WHERE run_id=? AND kind='assistant_message'", (run,))]
        assert len(messages) == 2  # No native message ID: equal text is not identity.
        public = messages[0]["observation"]
        assert public["data"]["text"] == "Use the source receipt when admitting dependent work."
        assert public["data"]["stopReason"] == "length"
        assert public["data"]["tool_calls"] == [{"tool_use_id": "proposed-call", "tool_name": "read", "tool_input": {"path": "src/x"}}]
        assert public["data"]["attachments"] == [{"attachment_type": "image", "content_available": False}]
        assert public["usage"] == {"input": 11, "output": 7, "cacheRead": 2, "cacheWrite": 0, "totalTokens": 20}
        assert "PRIVATE" not in "\n".join(ledger.db.iterdump())


@pytest.mark.skipif(not os.environ.get('HX_MUSE_TEST_BIN'), reason='requires an explicitly selected Muse CLI; loopback error fixture only')
def test_installed_muse_failure_reaches_ledger_through_generated_hooks(instance, tmp_path):
    from .muse_native_fixture import rejected_request
    configure(instance, 'meta')
    hook = instance / 'bin/hx-hook'
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f'#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(SRC)!r})\nfrom hx.hooks import main\nraise SystemExit(main())\n')
    hook.chmod(0o700)
    token = instance / 'seed/meta-token'
    token.write_text('fixture-local-only')
    token.chmod(0o600)
    with ContinuityStore(instance) as ledger:
        run = start(ledger)
        env = {key: os.environ[key] for key in ('PATH', 'TMPDIR') if key in os.environ}
        env.update(HOME=str(tmp_path), HARNESS_ROOT=str(instance), HX_PYTHON=sys.executable,
                   PYTHONPATH=str(SRC), **identity(run, 'meta'))
        installed = subprocess.run(['bash', str(instance / 'adapters/meta/install.sh'), '--no-companion', 'eng-001'],
                                   env=env, text=True, capture_output=True, timeout=10)
        assert installed.returncode == 0, installed.stderr
        home = instance / 'run/eng-001/home'
        work = tmp_path / 'native-work'
        work.mkdir()
        rejected_request(os.environ['HX_MUSE_TEST_BIN'], home, work)
        rows = ledger.db.execute('''SELECT e.payload,b.session_id FROM events e
            JOIN native_event_origins o USING(event_id) JOIN native_bindings b USING(binding_id)
            WHERE e.run_id=?''', (run,)).fetchall()
        observations = [(json.loads(row[0])['observation'], row[1]) for row in rows]
        request, session = next((item, session) for item, session in observations if item['kind'] == 'request')
        errors = [item for item, source in observations if source == session and item['data'].get('source') == 'model_response']
        assert len(errors) == 1
        assert errors[0]['data']['turn_id'] == request['data']['turn_id']
        assert errors[0]['data']['status'] == 'failed' and errors[0]['data']['settled'] is False
        assert 'HX local fixture rejected request' in errors[0]['data']['error']
        assert errors[0]['data']['usage_scope'] == 'provider_attempt'
        assert errors[0]['usage']['input_tokens'] == 0
        assert any(item['data'].get('outcome') == 'session_ended' for item, source in observations if source == session)
        assert not any(item['kind'] in {'assistant_message', 'finish'} for item, source in observations if source == session)
        assert native_producer.status(ledger, run)['lag'] is False
