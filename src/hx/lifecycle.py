"""`hx launch`, `hx restart`, `hx up`, `hx heartbeat` — keeping the fleet alive (spec 08, 12).

`hx launch` is idempotent, because `hx up` runs it for every id and the Partner runs it for
an id that may already be running. `hx restart` is the fallback seam: relaunch bare and send
the pointer once the pane is ready. `hx heartbeat` is the human's own cron, if they want one,
and is the only thing outside Claude Code that watches the fleet (17.2: hx ships no units).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import board, compose, flush as flush_mod, goal, store, timestamps, tmux, wake
from .caller import require_partner_caller
from .config_harness import flavor_of, load_harness, resolve_workdir
from .errors import HxError, NotFound
from .ids import ID_RE, PARTNER, sort_key
from .workitems import find_work_item, load_template, render, work_item_path

HEARTBEAT_BOARD = "run/heartbeat-board.txt"

#: The UI runs in its own tmux session, like everything else hx starts (spec 16.1, 17.2).
UI_SESSION = "ui"


def package_skills_dir() -> Path:
    """The package's `skills/`, which `install.sh` copies into `run/<id>/home/skills/`."""
    return Path(__file__).resolve().parent / "skills"


def adapter(root: Path, name: str, item_id: str | None = None, *, flavor: str | None = None) -> Path:
    """`adapters/<flavor>/<name>`. The Companion is always the Claude adapter."""
    chosen = flavor or (flavor_of(root, item_id) if item_id else "claude")
    path = root / "adapters" / chosen / name
    if not path.is_file():
        raise NotFound(f"{path}: missing; `hx install` copies the adapters from the package (spec 17.2)")
    return path


def config_ids(root: Path) -> list[str]:
    directory = root / "config"
    if not directory.is_dir():
        return []
    return sorted(
        (e.name for e in directory.iterdir() if e.is_dir() and ID_RE.match(e.name)), key=sort_key
    )


def ensure_work_item(root: Path, item_id: str) -> Path | None:
    """`hx launch` creates the `-idle` work item when there is none — not for the Partner,
    which has none at all (spec 08, spec 14 D25)."""
    if item_id == PARTNER:
        return None
    existing = find_work_item(root, item_id)
    if existing is not None:
        return existing
    harness = root / "config" / item_id / "harness.json"
    if not harness.is_file():
        raise NotFound(f"{harness}: no config for {item_id}; every id has a config/<id>/ (spec 03)")
    pod = load_harness(harness, check_cross_file=False).pod
    path = work_item_path(root, pod, item_id, "idle")
    store.atomic_write_text(
        path, render(load_template(root), item_id=item_id, pod=pod, dispatched="", goal="")
    )
    return path


def ensure_workdir(root: Path, item_id: str) -> Path | None:
    """The directory `start.sh` runs in: `harness.json.workdir`, whatever the Partner chose.

    hx creates no repository and no branch for it (17.2); it creates the directory if it is
    not there, so a launch is possible, and nothing else. The Partner runs in HARNESS_ROOT.
    """
    if item_id == PARTNER:
        return None
    config = load_harness(root / "config" / item_id / "harness.json", check_cross_file=False)
    if not config.workdir:
        return None
    workdir = resolve_workdir(config.workdir, root)
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


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
    # The Companion session is Claude even when the worker is Pi, Grok, or Meta.
    flavor = "claude" if extra and "--companion" in extra else None
    return subprocess.run(
        ["bash", str(adapter(root, name, item_id, flavor=flavor)), *(extra or []), item_id],
        env=child,
        capture_output=True,
        text=True,
        check=False,
    )


#: Kept for callers written before the runner took extra arguments.
_run_adapter = run_adapter


def launch(root: Path, item_id: str, *, companion: bool = True, env=None) -> dict:
    """Idempotent: a live session is left alone, but a `working` item still gets its goal.

    `companion=False` launches the agent without its Companion window, for a caller that
    starts `hx companion <id>` itself — which is what the suites do, so a test that is not
    about the Companion does not run one.
    """
    require_partner_caller("launch", env)
    from . import envfile

    envfile.sync_seed(root)
    path = ensure_work_item(root, item_id)
    ensure_workdir(root, item_id)

    from . import compile as compile_mod

    compile_mod.compile_agent(root, item_id)

    already = tmux.has_session(item_id, env)
    result = {"id": item_id, "session": "existing" if already else "started", "goal": None}
    no_companion = not companion or _companion_disabled(root, item_id)
    if not already:
        install_extra = (
            ["--no-companion"]
            if no_companion and flavor_of(root, item_id) == "meta"
            else None
        )
        installed = _run_adapter(root, "install.sh", item_id, env, extra=install_extra)
        if installed.returncode != 0:
            raise HxError(f"launch {item_id}: install.sh failed:\n{installed.stdout}{installed.stderr}")
        started = _run_adapter(root, "start.sh", item_id, env)
        if started.returncode != 0:
            raise HxError(f"launch {item_id}: start.sh failed:\n{started.stdout}{started.stderr}")

    if not already and not no_companion:
        start_companion(root, item_id, env=env)

    if path is not None and path.name.endswith("-working.md"):
        result["goal"] = goal.send_goal(root, item_id, wait=True, env=env)
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
    subprocess.run(
        [
            *tmux.tmux_command(env), "new-session", "-d", "-s", UI_SESSION,
            "-c", str(root), *session_env, str(hx_bin), "ui",
        ],
        capture_output=True, text=True, check=False,
    )
    return True


def _companion_disabled(root: Path, item_id: str) -> bool:
    """Best-effort read of `companion.disabled`. Unknown means enabled, as today."""
    from . import companion as companion_mod

    return companion_mod.is_disabled(root, item_id)


def start_companion(root: Path, item_id: str, *, env=None) -> bool:
    """The Companion's own Claude Code session in window `companion` (spec 10).

    `start.sh <id> --companion` launches it, exactly as it launches the agent: there is no
    headless path (spec 02 "Model calls").
    """
    from . import companion as companion_mod

    try:
        return companion_mod.launch(root, item_id, env=env)
    except (HxError, NotFound) as exc:
        # The agent still launches — an instance whose companion prompts are missing is
        # usable — but a Companion that silently fails to start is exactly the kind of thing
        # that should be visible, so it is logged and printed.
        from .hooks import log_error

        log_error(root, item_id, "companion", f"could not start the Companion: {exc}")
        print(f"hx: launch {item_id}: the Companion did not start: {exc}", file=sys.stderr)
        return False


def restart(root: Path, item_id: str, *, env=None) -> dict:
    """The fallback seam (spec 08, 02): flush, compose, relaunch bare, then the pointer."""
    require_partner_caller("restart", env)
    path = find_work_item(root, item_id)

    from . import compile as compile_mod

    compile_mod.compile_agent(root, item_id)

    flush_mod.flush(root, item_id, env=env)
    compose.compose(root, item_id, f"{item_id}-main", env=env)

    # `start.sh` respawns window `main` with `-k`, which is spec 08's "kill window main".
    started = _run_adapter(root, "start.sh", item_id, env)
    if started.returncode != 0:
        raise HxError(f"restart {item_id}: start.sh failed:\n{started.stdout}{started.stderr}")

    sent = None
    if path is not None and path.name.endswith("-working.md"):
        sent = goal.send_goal(root, item_id, wait=True, env=env)
    return {"id": item_id, "goal": sent}


def up(root: Path, *, env=None) -> list[dict]:
    """Boot: `hx launch` for every `config/<id>/`, `partner` first, and the UI (spec 08, 17.4)."""
    launched = [launch(root, item_id, env=env) for item_id in config_ids(root)]
    start_ui(root, env=env)
    return launched


def goal_was_lost(root: Path, item_id: str, *, env=None) -> bool:
    """A `working` agent that is alive, idle, and has not completed (build-8 item 12).

    Seen live 2026-09-20: a `/goal` evaluator can clear itself, and the agent then sits at
    its prompt with nothing to do and no way to say so. The board shows exactly this — a
    live session on a `working` item — so the heartbeat re-sends the pointer. It is
    idempotent for an agent that is merely between turns: the pointer is the same one it
    already has.
    """
    from . import streams

    pane = goal.capture_pane(item_id, env)
    if pane is None or not goal.pane_is_idle(pane):
        return False
    for record in streams.iter_records(streams.main_stream(root, item_id)):
        if "HX-COMPLETE" in json.dumps(record, default=str):
            return False
    return True


def heartbeat(root: Path, *, env=None) -> dict:
    """The human's own cron, if they want one (spec 08, 12 step 4, 17.2)."""
    view = board.collect(root, env=env)

    # The Partner is not a board item, so it is checked on its own (spec 08, build-8 item 9).
    partner_launched = False
    if not tmux.has_session(PARTNER, env) and (root / "config" / PARTNER).is_dir():
        launch(root, PARTNER, env=env)
        partner_launched = True

    ui_started = start_ui(root, env=env)

    restarted = []
    regoaled = []
    for item in view["items"]:
        if item["state"] != "working":
            continue
        if not item["session_alive"]:
            restart(root, item["id"], env=env)
            restarted.append(item["id"])
        elif goal_was_lost(root, item["id"], env=env):
            goal.send_goal(root, item["id"], env=env)
            regoaled.append(item["id"])

    after = board.collect(root, env=env) if (restarted or regoaled) else view
    text = board.render_text(after)

    previous_path = root / HEARTBEAT_BOARD
    previous = previous_path.read_text() if previous_path.is_file() else None
    store.atomic_write_text(previous_path, text + "\n")

    active = any(item["state"] == "working" for item in after["items"])
    changed = previous is not None and previous.strip() != text.strip()
    woke = None
    if active and changed:
        woke = wake.wake_partner_status(root, f"check on each HarnessAgent: {_diff(previous, text)}")

    return {
        "restarted": restarted,
        "regoaled": regoaled,
        "partner_launched": partner_launched,
        "ui_started": ui_started,
        "changed": changed,
        "woke_partner": woke,
        "ts": timestamps.now(),
    }


def _diff(previous: str | None, current: str) -> str:
    """A one-line summary of what moved on the board, for the wake text (spec 08)."""
    before = set((previous or "").strip().split("\n"))
    after = set(current.strip().split("\n"))
    gained = sorted(line for line in after - before if line.strip())
    lost = sorted(line for line in before - after if line.strip())
    parts = [f"+ {line}" for line in gained] + [f"- {line}" for line in lost]
    return "; ".join(parts) if parts else "no change"


def main_launch(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx launch", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--no-companion", action="store_true",
                        help="launch the agent without its Companion window")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = launch(root, args.id, companion=not args.no_companion, env=env)
    print(f"HX-LAUNCH {result['id']} {result['session']} goal={result['goal'] or 'none'}")
    return 0


def main_restart(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx restart", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = restart(root, args.id, env=env)
    print(f"HX-RESTART {result['id']} goal={result['goal'] or 'none'}")
    return 0


def main_up(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx up", add_help=True)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.parse_args(argv)
    for result in up(root, env=env):
        print(f"HX-LAUNCH {result['id']} {result['session']} goal={result['goal'] or 'none'}")
    print(f"HX-UI {ui_url(root)}")
    return 0


def main_heartbeat(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx heartbeat", add_help=True)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.parse_args(argv)
    result = heartbeat(root, env=env)
    if result["woke_partner"] not in (None, wake.ACCEPTED):
        # A warning: the next heartbeat compares against the same board and tries again.
        print(
            f"hx: heartbeat: the Partner was not woken ({result['woke_partner']})",
            file=sys.stderr,
        )
    print(
        f"HX-HEARTBEAT restarted={','.join(result['restarted']) or 'none'} "
        f"regoaled={','.join(result['regoaled']) or 'none'} "
        f"partner={'launched' if result['partner_launched'] else 'alive'} "
        f"ui={'started' if result['ui_started'] else 'alive'} "
        f"changed={str(result['changed']).lower()} woke={result['woke_partner'] or 'not-needed'}"
    )
    return 0
