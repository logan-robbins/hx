"""Immutable run identity for hooks whose native host may clear its environment."""

from __future__ import annotations

from pathlib import Path

from .config_harness import load_harness
from .continuity_store import Conflict, ContinuityStore, _id
from .errors import ValidationError
from .native_capture import _run

FIELDS = ("run", "launch", "adapter")


def installation_args(root: Path, item_id: str, adapter: str, env) -> list[str]:
    """Validate and register once; put these exact arguments in the hook command.

    A launch ID never changes its assignment, even after worker reuse. This does
    not start a process or assert that a native process consumed its instructions.
    """
    values = [env.get("HX_CONTINUITY_" + field.upper()) for field in FIELDS]
    if not any(values):
        return []
    if not all(values):
        raise ValidationError("planned installation requires run, launch, and adapter together")
    run_id, launch_id, declared = values
    installation_root = root.resolve()
    root = Path(env.get("HX_CONTINUITY_AUTHORITY") or root).resolve()
    _id(launch_id)
    if adapter != declared or load_harness(root / "config" / item_id / "harness.json", check_cross_file=False).flavor != adapter:
        raise ValidationError("planned hook adapter differs from the executor configuration")
    with ContinuityStore(root) as ledger, ledger.transaction() as tx:
        _run(tx, run_id, item_id)
        previous = tx.db.execute("SELECT run_id,worker_id,adapter FROM native_launch_contracts WHERE launch_id=?", (launch_id,)).fetchone()
        expected = (run_id, item_id, adapter)
        if previous and tuple(previous) != expected:
            raise Conflict("native launch identity is already bound to another assignment")
        if not previous:
            tx._change()
            tx.db.execute("INSERT INTO native_launch_contracts VALUES(?,?,?,?)", (launch_id, *expected))
    return [*(["--root", str(root)] if root != installation_root else []),
            *(argument for field, value in zip(FIELDS, values) for argument in ("--continuity-" + field, value))]


def validate(ledger, *, run_id: str, launch_id: str, adapter: str, worker_id: str):
    row = ledger.db.execute("SELECT run_id,worker_id,adapter FROM native_launch_contracts WHERE launch_id=?", (launch_id,)).fetchone()
    if row is None or tuple(row) != (run_id, worker_id, adapter):
        raise Conflict("planned hook does not match a registered native launch contract")
