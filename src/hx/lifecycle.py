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
        path, render(load_template(root), item_id=item_id, pod=pod, dispatched="", order="")
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
    return subprocess.run(
        ["bash", str(adapter(root, name)), *(extra or []), item_id],
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

    if not already and companion:
        start_companion(root, item_id, env=env)

    if path is not None and path.name.endswith("-working.md"):
        result["goal"] = goal.send_goal(root, item_id, wait=True, env=env)
    return result


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
    """The human's own cron, if they want one (spec 08, 12 step 4, 17.2)."""
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

    active = any(item["state"] == "working" for item in after["items"])
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
