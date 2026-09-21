"""`hx launch`, `hx restart`, `hx up`, `hx heartbeat` — keeping the fleet alive (spec 08, 12).

`hx launch` is idempotent, because `hx up` runs it for every id at every boot and the Partner
runs it for an id that may already be running. `hx restart` is the fallback seam: relaunch
bare and send the pointer once the pane is ready. `hx heartbeat` is a system cron, the only
thing outside Claude Code that watches the fleet.
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
from .config_harness import load_harness, resolve_workdir
from .errors import HxError, NotFound
from .ids import ID_RE, PARTNER, sort_key
from .workitems import find_work_item, load_template, render, work_item_path

HEARTBEAT_BOARD = "run/heartbeat-board.txt"


def package_skills_dir() -> Path:
    """The package's `skills/`, which `install.sh` copies into `run/<id>/home/skills/`."""
    return Path(__file__).resolve().parent / "skills"


def adapter(root: Path, name: str) -> Path:
    path = root / "adapters" / "claude" / name
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


def ensure_work_item(root: Path, item_id: str) -> Path:
    """`hx launch` creates the `-idle` work item when there is none (spec 08)."""
    existing = find_work_item(root, item_id)
    if existing is not None:
        return existing
    harness = root / "config" / item_id / "harness.json"
    if not harness.is_file():
        raise NotFound(f"{harness}: no config for {item_id}; every id has a config/<id>/ (spec 03)")
    pod = load_harness(harness, check_cross_file=False).pod
    path = work_item_path(root, pod, item_id, "idle")
    store.atomic_write_text(
        path, render(load_template(root), item_id=item_id, pod=pod, after=[], dispatched="", order="")
    )
    return path


def ensure_workdir(root: Path, item_id: str) -> Path | None:
    """The worktree `start.sh` runs in. The Partner has none (spec 05, 17.4).

    With a mirror, this is a sparse worktree cut from it on the agent's own branch, without
    the product's `.claude/` (spec 17.3). Without one — an instance whose human has not run
    `hx repo add` yet — the directory is created bare so a launch is still possible.
    """
    from . import repo as repo_mod

    if item_id == PARTNER:
        return None
    harness = root / "config" / item_id / "harness.json"
    config = load_harness(harness, check_cross_file=False)
    workdir = resolve_workdir(config.workdir, root) if config.workdir else root / "wt" / item_id

    if workdir.is_dir() and any(workdir.iterdir()):
        return workdir
    if repo_mod.load_repo(root) is not None:
        return repo_mod.create_worktree(root, item_id, workdir, env=None)
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _run_adapter(root: Path, name: str, item_id: str, env=None) -> subprocess.CompletedProcess:
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
        ["bash", str(adapter(root, name)), item_id],
        env=child,
        capture_output=True,
        text=True,
        check=False,
    )


def launch(root: Path, item_id: str, *, env=None) -> dict:
    """Idempotent: a live session is left alone, but a `working` item still gets its goal."""
    require_partner_caller("launch", env)
    path = ensure_work_item(root, item_id)
    ensure_workdir(root, item_id)

    already = tmux.has_session(item_id, env)
    result = {"id": item_id, "session": "existing" if already else "started", "goal": None}
    if not already:
        installed = _run_adapter(root, "install.sh", item_id, env)
        if installed.returncode != 0:
            raise HxError(f"launch {item_id}: install.sh failed:\n{installed.stdout}{installed.stderr}")
        started = _run_adapter(root, "start.sh", item_id, env)
        if started.returncode != 0:
            raise HxError(f"launch {item_id}: start.sh failed:\n{started.stdout}{started.stderr}")

    if not already:
        start_companion(root, item_id, env=env)

    if path.name.endswith("-working.md"):
        result["goal"] = goal.send_goal(root, item_id, wait=True, env=env)
    return result


def start_companion(root: Path, item_id: str, *, env=None) -> bool:
    """`hx companion <id>` in window `companion` — the other half of launch (spec 08)."""
    import shlex

    hx_bin = None
    hx_json = root / "config" / "hx.json"
    if hx_json.is_file():
        try:
            hx_bin = json.loads(hx_json.read_text()).get("hx_bin")
        except json.JSONDecodeError:
            hx_bin = None
    if not hx_bin:
        hx_bin = shutil.which("hx")
    if not hx_bin:
        # Without a recorded entry point there is nothing to run; the agent still launches.
        return False

    command = f"{shlex.quote(hx_bin)} companion {shlex.quote(item_id)}"
    result = subprocess.run(
        [*tmux.tmux_command(env), "new-window", "-d", "-t", f"={item_id}:", "-n", "companion",
         "-c", str(root), command],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def restart(root: Path, item_id: str, *, env=None) -> dict:
    """The fallback seam (spec 08, 02): flush, compose, relaunch bare, then the pointer."""
    require_partner_caller("restart", env)
    path = find_work_item(root, item_id)
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
    """Boot: `hx launch` for every `config/<id>/`, `partner` first (spec 08, 17.4)."""
    return [launch(root, item_id, env=env) for item_id in config_ids(root)]


def heartbeat(root: Path, *, env=None) -> dict:
    """System cron, every 15 minutes (spec 08, 12 step 4)."""
    view = board.collect(root, env=env)
    text = board.render_text(view)

    restarted = []
    for item in view["items"]:
        if item["state"] == "working" and not item["session_alive"]:
            restart(root, item["id"], env=env)
            restarted.append(item["id"])

    after = board.collect(root, env=env) if restarted else view
    text = board.render_text(after)

    previous_path = root / HEARTBEAT_BOARD
    previous = previous_path.read_text() if previous_path.is_file() else None
    store.atomic_write_text(previous_path, text + "\n")

    active = any(item["state"] in ("working", "queued") for item in after["items"])
    changed = previous is not None and previous.strip() != text.strip()
    woke = None
    if active and changed:
        woke = wake.wake_partner_status(root, f"check on each HarnessAgent: {_diff(previous, text)}")

    return {"restarted": restarted, "changed": changed, "woke_partner": woke, "ts": timestamps.now()}


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
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = launch(root, args.id, env=env)
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
        f"changed={str(result['changed']).lower()} woke={result['woke_partner'] or 'not-needed'}"
    )
    return 0
