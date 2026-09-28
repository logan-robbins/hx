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


def commands(home, adapter):
    if adapter == "pi":
        contract = json.loads((home / "extensions" / "hx" / "hook-contract.json").read_text())
        return [shlex.join([contract["command"], *contract["args"], "log"])]
    if adapter in {"codex", "grok"}:
        config = tomllib.loads((home / "config.toml").read_text())
    else:
        config = json.loads((home / ("muse/settings.json" if adapter == "meta" else "settings.json")).read_text())
    return [hook["command"] for block in config["hooks"]["PostToolUse"] for hook in block["hooks"]]


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
    hook.write_text(f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(SRC)!r})\nfrom hx.hooks import main\nraise SystemExit(main())\n")
    hook.chmod(0o755)
    extension = tmp_path / "native-extension" / "index.ts"
    extension.parent.mkdir()
    shutil.copyfile(instance / "adapters" / "pi" / "extension" / "index.ts", extension)
    script = tmp_path / "load.mjs"
    script.write_text('''import { pathToFileURL } from "node:url";
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
const [loader, extension, cwd] = process.argv.slice(2);
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
console.log("installed Pi loader and callback completed");
''')
    with ContinuityStore(instance) as ledger:
        run = start(ledger)
        args = hook_contract.installation_args(instance, "eng-001", "pi", identity(run, "pi"))
        (extension.parent / "hook-contract.json").write_text(json.dumps({"command": str(hook),
            "args": ["--root", str(instance), "--id", "eng-001", *args]}))
        isolated_home = tmp_path / "isolated-loader-home"
        isolated_home.mkdir()
        result = subprocess.run(["node", str(script), os.environ["HX_PI_EXTENSION_LOADER"], str(extension), str(tmp_path)],
                                env=child_env(HOME=str(isolated_home), PI_CODING_AGENT_DIR=str(isolated_home / "pi")),
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        row = ledger.db.execute("SELECT payload FROM events WHERE run_id=?", (run,)).fetchone()
        assert row and "Native loader callback delivered." in row[0], result.stderr
