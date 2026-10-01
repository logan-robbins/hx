"""Private native installation inputs for one frozen planned assignment.

Preparing files does not acknowledge native instruction delivery or start a
model. A controller must observe startup before submitting the task pointer.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import uuid
from pathlib import Path

from . import context_packets, prompt_compiler, store as files, unit_execution
from .caller import require_partner_caller
from .continuity_store import Conflict, canonical, digest, _id
from .errors import ValidationError
from .ids import is_id


def _bytes(path, limit=65536):
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValidationError(f"native launch input exceeds {limit} bytes: {path}")
    return data


def _configuration(root, worker, workdir):
    import json

    harness = json.loads(_bytes(root / "config" / worker / "harness.json"))
    harness["workdir"] = workdir
    result = {f"config/{worker}/harness.json": canonical(harness).encode() + b"\n"}
    for name in ("models.json", "auth.json", "claude.json", "codex.json", "meta.json", "grok.json", "pi.json"):
        path = root / "config" / name
        if path.is_file():
            result["config/" + name] = _bytes(path)
    return result


def _row(ledger, run_id):
    import json

    row = ledger.db.execute("SELECT * FROM native_launches WHERE run_id=?", (run_id,)).fetchone()
    return {**dict(row), "payload": json.loads(row["payload"])} if row else None


def _capsule(root, worker, capsule, configuration, system):
    """Copy only bounded launch inputs; never copy worker histories or memories."""
    capsule.mkdir(parents=True, exist_ok=False)
    capsule.chmod(0o700)
    for relative, data in configuration.items():
        files.atomic_write_text(capsule / relative, data.decode("utf-8"))
    files.atomic_write_text(capsule / "config" / worker / "AGENTS.md", system + "## UPDATES BELOW ONLY\n")
    # The current package supplies adapter code; old installed scripts cannot
    # silently select legacy hook behavior for a newly prepared assignment.
    shutil.copytree(Path(__file__).parent / "skeleton" / "adapters", capsule / "adapters")
    (capsule / "seed").symlink_to(root / "seed", target_is_directory=True)
    for command, module in (("hx", "hx.cli"), ("hx-hook", "hx.hooks")):
        wrapper = capsule / "bin" / command
        files.atomic_write_text(wrapper, f"#!{sys.executable}\nimport os, sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})\n"
            f"os.environ['HARNESS_ROOT'] = {str(root)!r}\n"
            f"from {module} import main\nraise SystemExit(main())\n")
        wrapper.chmod(0o700)
    files.atomic_write_json(capsule / "config" / "hx.json", {
        "python_bin": sys.executable, "hx_bin": str(capsule / "bin" / "hx"),
        "hook_bin": str(capsule / "bin" / "hx-hook"),
    })


def prepare(ledger, worker, run_id, request_id, *, env=None):
    """Reserve one preparation and freeze its instructions, packet, and home.

    A repeated request returns the same preparation. Interrupted preparation
    remains visible and retains assignment ownership; it never triggers a second
    installation implicitly. No native process is started here.
    """
    require_partner_caller("launch", env)
    _id(request_id)
    if not is_id(worker) or worker == "partner":
        raise ValidationError("native unit preparation requires a worker ID")
    root = ledger.root.resolve()
    launch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "hx-launch:" + run_id + ":" + request_id))
    capsule = root / "run" / worker / "launches" / launch_id / "root"
    with ledger.transaction() as tx:
        run, task, admission = unit_execution._run(tx, run_id)
        if run["worker_id"] != worker or run["phase"] == "paused":
            raise Conflict("native preparation requires this worker's unpaused assignment")
        manifest = prompt_compiler.build(root, worker)
        manifest, rendered = prompt_compiler.verified_bundle(root, worker, Path(manifest["manifest_path"]),
                                                             workdir=task["payload"]["workdir"])
        state = unit_execution.workspace(task["payload"], clean=True)
        if state != admission["workspace"]:
            raise Conflict("fresh native launch requires the exact admitted worktree state")
        if unit_execution._prerequisites(tx, task, state) != admission["prerequisites"]:
            raise Conflict("native launch prerequisite proof changed since admission")
        configuration = _configuration(root, worker, task["payload"]["workdir"])
        config_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in configuration.items()}
        request_hash = digest({"run": run_id, "worker": worker, "request": request_id,
                               "prompt": manifest["version"], "configuration": config_hashes})
        previous = _row(tx, run_id)
        if previous:
            if previous["request_hash"] != request_hash:
                raise Conflict("native launch already reserved with different inputs")
            return previous
        # Charge custom system instructions as well as task context. This is a
        # conservative initial-input bound, not full provider request accounting.
        packet_budget = 8000 - len(rendered["system"].encode())
        if packet_budget <= 0:
            raise ValidationError("required system instructions exhaust the initial context budget")
        payload = {"worker_id": worker, "capsule": str(capsule), "manifest": manifest["manifest_path"],
                   "prompt_version": manifest["version"], "configuration": config_hashes,
                   "instruction_delivery": "prepared", "tool_visibility": "unverified",
                   "checkpoint_id": None, "initial_input_limit": 8000}
        tx._change()
        tx.db.execute("INSERT INTO native_launch_contracts VALUES(?,?,?,?)",
                      (launch_id, run_id, worker, manifest["identity"]["runtime"]))
        tx.db.execute("INSERT INTO native_launches VALUES(?,?,?,?,?,'preparing',NULL)",
                      (launch_id, run_id, request_id, request_hash, canonical(payload)))
    try:
        packet = context_packets.issue(ledger, run_id, request_id="launch-" + launch_id, mode="planned",
                                       instructions=rendered["context"], max_tokens=packet_budget,
                                       optional_tokens=min(1000, packet_budget))
        _capsule(root, worker, capsule, configuration, rendered["system"])
        packet_path = capsule.parent / "task-context.md"
        files.atomic_write_text(packet_path, packet["text"])
        payload.update(checkpoint_id=packet["checkpoint_id"], packet_path=str(packet_path),
                       packet_hash=packet["packet_hash"],
                       charged_initial_tokens=packet["charged_tokens"] + len(rendered["system"].encode()))
        with ledger.transaction() as tx:
            unit_execution._run(tx, run_id)
            tx._change()
            tx.db.execute("UPDATE native_launches SET payload=?,status='prepared' WHERE launch_id=?",
                          (canonical(payload), launch_id))
        verify(ledger, run_id)
    except Exception as exc:
        with ledger.transaction() as tx:
            tx._change()
            # Do not persist arbitrary adapter or credential-bearing output.
            tx.db.execute("UPDATE native_launches SET status='preparation_failed',error=? WHERE launch_id=?",
                          (type(exc).__name__, launch_id))
        raise
    return _row(ledger, run_id)


def environment(root, launch, *, env=None):
    """Installer environment; hooks and worker commands address the authority."""
    from .config_harness import load_harness

    capsule = Path(launch["payload"]["capsule"])
    worker = launch["payload"]["worker_id"]
    flavor = load_harness(capsule / "config" / worker / "harness.json", check_cross_file=False).flavor
    return {**(os.environ if env is None else env), "HARNESS_ROOT": str(capsule),
            "HX_CONTINUITY_AUTHORITY": str(root.resolve()), "HX_CONTINUITY_RUN": launch["run_id"],
            "HX_CONTINUITY_LAUNCH": launch["launch_id"], "HX_CONTINUITY_ADAPTER": flavor,
            "HX_SKILLS_DIR": "", "HX_PYTHON": sys.executable}


def verify(ledger, run_id):
    """Revalidate prepared inputs before an installer/controller uses them."""
    with ledger.transaction() as tx:
        launch = _row(tx, run_id)
        if not launch or launch["status"] != "prepared":
            raise Conflict("native launch preparation is not ready")
        payload = launch["payload"]
        run, task, admission = unit_execution._run(tx, run_id)
        if run["phase"] == "paused":
            raise Conflict("native launch assignment is paused")
        manifest, rendered = prompt_compiler.verified_bundle(ledger.root, run["worker_id"],
            Path(payload["manifest"]), workdir=task["payload"]["workdir"])
        if manifest["version"] != payload["prompt_version"]:
            raise Conflict("native launch instructions changed")
        current = _configuration(ledger.root, run["worker_id"], task["payload"]["workdir"])
        if {name: hashlib.sha256(data).hexdigest() for name, data in current.items()} != payload["configuration"]:
            raise Conflict("native launch configuration changed")
        capsule = Path(payload["capsule"])
        for relative, expected in payload["configuration"].items():
            if hashlib.sha256(_bytes(capsule / relative)).hexdigest() != expected:
                raise Conflict("native launch capsule configuration changed")
        if _bytes(capsule / "config" / run["worker_id"] / "AGENTS.md", 262144) != (rendered["system"] + "## UPDATES BELOW ONLY\n").encode():
            raise Conflict("native launch system instructions changed")
        if unit_execution.workspace(task["payload"], clean=True) != admission["workspace"]:
            raise Conflict("native launch worktree changed")
        if unit_execution._prerequisites(tx, task, admission["workspace"]) != admission["prerequisites"]:
            raise Conflict("native launch prerequisite proof changed")
        packet = context_packets._read(tx, payload["checkpoint_id"], run_id)
        if packet["packet_hash"] != payload["packet_hash"] or _bytes(Path(payload["packet_path"]), 262144) != packet["text"].encode():
            raise Conflict("native launch context packet changed")
        cursors, pending = context_packets._pending(tx, run_id)
        if cursors != packet["cursors"] or pending != packet["pending"] or context_packets._sources(tx, run_id) != packet["capture"]:
            raise Conflict("native launch has newer execution evidence; compose current context")
        return launch
