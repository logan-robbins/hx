"""Partner bootstrap and ledger-owned worker lifecycle reconciliation."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from . import timestamps, tmux
from .caller import require_partner_caller
from .config_harness import flavor_of
from .continuity_store import Conflict, ContinuityStore, canonical
from .errors import HxError, NotFound
from .ids import PARTNER

UI_SESSION = "ui"


def package_skills_dir() -> Path:
    """The package's `skills/`, which `install.sh` copies into `run/<id>/home/skills/`."""
    return Path(__file__).resolve().parent / "skills"


def adapter(root: Path, name: str, item_id: str | None = None) -> Path:
    """`adapters/<flavor>/<name>` for the agent's own flavor; Claude with no agent."""
    chosen = flavor_of(root, item_id) if item_id else "claude"
    path = root / "adapters" / chosen / name
    if not path.is_file():
        raise NotFound(f"{path}: missing; `hx install` copies the adapters from the package (spec 17.2)")
    return path


def run_adapter(
    root: Path, name: str, item_id: str, env=None, *, extra: list[str] | None = None
) -> subprocess.CompletedProcess:
    import os

    child = dict(os.environ if env is None else env)
    child["HARNESS_ROOT"] = str(root)
    child.setdefault("HX_SKILLS_DIR", str(package_skills_dir()))
    hx_json = root / "config" / "hx.json"
    if hx_json.is_file():
        try:
            recorded = json.loads(hx_json.read_text()).get("python_bin")
        except json.JSONDecodeError:
            recorded = None
        if recorded:
            child.setdefault("HX_PYTHON", recorded)
    return subprocess.run(
        ["bash", str(adapter(root, name, item_id)), *(extra or []), item_id],
        env=child,
        capture_output=True,
        text=True,
        check=False,
    )


def prepare_persona(root: Path, item_id: str, *, env=None) -> dict:
    """Validate the configured Partner prompt sources before bootstrap."""
    from . import persona as persona_mod
    result = persona_mod.boundary(root, item_id, env=env)
    if item_id == PARTNER:
        from .partner_context import validate
        result['delivery'] = validate(root, result['text'])
    return result


def ui_url(root: Path) -> str:
    from .ui.server import LOOPBACK_HOST, instance_port

    return f"http://{LOOPBACK_HOST}:{instance_port(root)}/"


def start_ui(root: Path, *, env=None) -> bool:
    """`hx ui` in tmux session `ui` (spec 16.1, 17.2). Idempotent: a live one is left alone.

    It is a session and not a daemon for the same reason every agent is: one place to look,
    `tmux attach -t ui`, and `hx heartbeat` can see whether it is still there.
    """
    import os

    if tmux.has_session(UI_SESSION, env):
        return False
    hx_bin = root / "bin" / "hx"
    if not hx_bin.exists():
        hx_json = root / "config" / "hx.json"
        recorded = json.loads(hx_json.read_text()).get("hx_bin") if hx_json.is_file() else None
        if not recorded:
            raise NotFound(
                f"{root}: no bin/hx and no `hx_bin` in config/hx.json; `hx install` records both"
            )
        hx_bin = Path(recorded)

    # The session env, as `start.sh` builds it: HARNESS_ROOT plus every HX_* the caller has,
    # so a scratch instance's UI talks to the scratch tmux server and nothing else.
    session_env = ["-e", f"HARNESS_ROOT={root}"]
    for key, value in (os.environ if env is None else env).items():
        if key.startswith("HX_"):
            session_env += ["-e", f"{key}={value}"]
    started = subprocess.run(
        [
            *tmux.tmux_command(env), "new-session", "-d", "-s", UI_SESSION,
            "-c", str(root), *session_env, str(hx_bin), "ui",
        ],
        capture_output=True, text=True, check=False,
    )
    if started.returncode:
        raise HxError(f"could not start UI session: {started.stderr.strip()}")
    return True


def launch(root: Path, item_id: str, *, run_id: str | None = None,
           request_id: str | None = None, env=None) -> dict:
    """Start the Partner, or advance this worker's explicitly admitted run."""
    require_partner_caller("launch", env)
    if item_id != PARTNER:
        if not run_id or not request_id:
            raise Conflict("worker launch requires --run RUN and --request REQUEST from hx plan assign")
        from .native_controller import advance
        with ContinuityStore(root) as ledger:
            return advance(ledger, item_id, run_id, request_id, env=env)
    if run_id or request_id:
        raise Conflict("the Partner orchestrates plans and cannot own a worker run")
    from . import claude_bin, envfile
    envfile.sync_seed(root)
    claude_bin.refresh_pin(root, env)
    already = tmux.has_session(PARTNER, env)
    if not already:
        from .partner_context import invalidate
        invalidate(root)
        result = run_adapter(root, 'install.sh', PARTNER, env)
        if result.returncode:
            raise HxError(f"launch partner: install.sh failed:\n{result.stdout}{result.stderr}")
        prepare_persona(root, PARTNER, env=env)
        result = run_adapter(root, 'start.sh', PARTNER, env)
        if result.returncode:
            raise HxError(f"launch partner: start.sh failed:\n{result.stdout}{result.stderr}")
    else:
        prepare_persona(root, PARTNER, env=env)
    return {"id": PARTNER, "session": "existing" if already else "started"}


def _recover(root: Path, *, env=None) -> dict:
    """Recover root services and existing launch intents, never unlaunched reservations."""
    from . import application_loop
    # Recover worker control even when a Partner/UI bootstrap later reports an
    # unrelated configuration error.
    service = application_loop.ensure(root, env=env)
    partner = None
    if (root / "config" / PARTNER).is_dir():
        partner = ({"id": PARTNER, "session": "existing"} if tmux.has_session(PARTNER, env)
                   else launch(root, PARTNER, env=env))
    ui_started = start_ui(root, env=env)
    # The resident loop owns native progression. Kernel identity plus its root
    # lock prevents heartbeat from starting a second controller after a crash.
    with ContinuityStore(root) as ledger:
        snapshot = application_loop.status(ledger)
    return {"partner": partner, "ui_started": ui_started, "service": service, "status": snapshot}


def up(root: Path, *, env=None) -> dict:
    require_partner_caller("up", env)
    return _recover(root, env=env)


def heartbeat(root: Path, *, env=None, require_go: bool = False) -> dict:
    """Recover the root loop; durable outbox events report meaningful changes."""
    require_partner_caller("heartbeat", env)
    from . import learn
    if require_go and learn.go_line(root) is None:
        return {"gated": True, "ts": timestamps.now()}
    return {"gated": False, **_recover(root, env=env), "ts": timestamps.now()}


def main_launch(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx launch")
    parser.add_argument("id")
    parser.add_argument("--run", help="worker run reserved by hx plan assign")
    parser.add_argument("--request", help="stable launch request; reuse it to reconcile progress")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--drain", action="store_true", help="close request/tool admission while retaining execution leases")
    action.add_argument("--shutdown", action="store_true", help="advance owned-process termination")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    require_partner_caller("launch", env)
    if args.id != PARTNER and not (args.run and args.request):
        parser.error("worker launch requires --run RUN and --request REQUEST from hx plan assign")
    if (args.run is None) != (args.request is None):
        parser.error("--run and --request are required together")
    if args.drain or args.shutdown:
        if args.id == PARTNER or not args.run:
            parser.error("drain/shutdown requires a worker run and its original request")
        from .native_controller import drain
        from .native_shutdown import advance as shutdown
        with ContinuityStore(root) as ledger:
            result = (shutdown if args.shutdown else drain)(ledger, args.id, args.run, args.request, env=env)
    else:
        result = launch(root, args.id, run_id=args.run, request_id=args.request, env=env)
    print(canonical(result))
    return 0


def main_up(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx up")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.parse_args(argv)
    print(canonical(up(root, env=env)))
    print(f"HX-UI {ui_url(root)}")
    return 0


def main_heartbeat(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx heartbeat")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.add_argument("--require-go", action="store_true", help="recover only when PARTNER.md carries a GO line")
    args = parser.parse_args(argv)
    print(canonical(heartbeat(root, env=env, require_go=args.require_go)))
    return 0
