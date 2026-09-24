"""The live suite: the M6 criteria against the real pinned binary (spec 13 M6, goal build-8).

Skipped unless **both** are true:

  - `HX_LIVE=1`
  - a seed token exists — `$HX_SEED_TOKEN`, else `$HARNESS_ROOT/seed/token` if that is set

Every test builds its own instance under `tmp_path`, runs it on a private tmux server, and
reaps that server in teardown, so a live run touches no other session and nothing under the
user's home. `tests/guard/test_user_home_untouched.py` proves the second half from outside.

These cost real model calls. They are not part of `tools/milestone-check.sh`; the build lane
runs them deliberately with `HX_LIVE=1 .venv/bin/python -m pytest tests/live`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

LIVE = os.environ.get("HX_LIVE") == "1"
#: The pinned binary is whatever `config/claude.json` would record; the suite finds it the
#: same way `hx install` does, so a live run and a real install agree.
CLAUDE_BIN = shutil.which("claude")


def seed_token_path() -> Path | None:
    explicit = os.environ.get("HX_SEED_TOKEN")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    root = os.environ.get("HARNESS_ROOT")
    if root and (Path(root) / "seed" / "token").is_file():
        return Path(root) / "seed" / "token"
    return None


def _why_skipped() -> str | None:
    if not LIVE:
        return "live suite: set HX_LIVE=1 to run it (it makes real model calls)"
    if CLAUDE_BIN is None:
        return "live suite: no `claude` on PATH"
    if seed_token_path() is None:
        return (
            "live suite: no seed token; point HX_SEED_TOKEN at the file `claude setup-token` "
            "produced, or set HARNESS_ROOT to an instance that has seed/token"
        )
    return None


LIVE_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    # A conftest hook sees every item in the session, not just this directory's: without
    # the filter, `pytest tests` skipped the whole suite whenever HX_LIVE was unset.
    reason = _why_skipped()
    if reason:
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if Path(str(item.path)).resolve().is_relative_to(LIVE_DIR):
                item.add_marker(skip)


MODELS = {
    "claude-haiku-4-5-20251001": {"window": 200000, "seam_threshold": 100000},
    "claude-sonnet-5": {"window": 1000000, "seam_threshold": 500000},
    "claude-opus-5": {"window": 1000000, "seam_threshold": 500000},
}

#: Every live test here is about the harness, never about the model's answer. The user's
#: standing rule (2026-09-21): tests run Sonnet at medium effort, for agents and Companions.
LIVE_MODEL = "claude-sonnet-5"
LIVE_EFFORT = "medium"


@pytest.fixture
def tmux_server():
    """A private tmux server, killed in teardown whatever the test did."""
    socket = f"hx-live-{uuid.uuid4().hex[:10]}"
    command = ["tmux", "-L", socket]
    yield command
    subprocess.run([*command, "kill-server"], capture_output=True, check=False)


def claude_version() -> str:
    from hx.doctor import bare_claude_version

    result = subprocess.run([CLAUDE_BIN, "--version"], capture_output=True, text=True, check=False)
    return bare_claude_version(result.stdout or "")


@pytest.fixture
def live_root(tmp_path, tmux_server):
    """A real instance with a real token, one worker, and the pinned binary."""
    from hx.install import install_skeleton

    root = tmp_path / "instance"
    install_skeleton(root)
    (root / "config" / "models.json").write_text(json.dumps(MODELS, indent=2) + "\n")
    (root / "config" / "claude.json").write_text(
        json.dumps({"bin": CLAUDE_BIN, "version": claude_version()}, indent=2) + "\n"
    )
    token = root / "seed" / "token"
    token.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(seed_token_path(), token)
    token.chmod(0o600)
    return root


@pytest.fixture
def live_env(live_root, tmux_server):
    return {
        **os.environ,
        "HARNESS_ROOT": str(live_root),
        "HX_TMUX": " ".join(tmux_server),
        "HX_SKILLS_DIR": str(SRC / "hx" / "skills"),
        "HARNESS_ID": "partner",
    }


@pytest.fixture
def worker(live_root, live_env):
    """Add a worker to the live instance. Returns `(id, workdir)`."""

    def _add(item_id="be-001", *, workdir: Path | None = None, pod="engineers",
             role="backend-engineer", effort=LIVE_EFFORT):
        workdir = workdir or (live_root / "work" / item_id)
        workdir.mkdir(parents=True, exist_ok=True)
        config = live_root / "config" / item_id
        config.mkdir(parents=True, exist_ok=True)
        (config / "harness.json").write_text(json.dumps({
            "id": item_id, "pod": pod, "role": role, "model": LIVE_MODEL, "effort": effort,
            "workdir": str(workdir),
            "companion": {"provider": "claude-cli", "model": LIVE_MODEL, "batch_records": 5,
                          "state_budget_tokens": 4000, "seam_min_context_tokens": 1,
                          "seam_min_interval_s": 0},
        }, indent=2) + "\n")
        (config / "AGENTS.md").write_text(
            f"You are {item_id}, a backend engineer.\n\n## UPDATES BELOW ONLY\n\nnothing yet.\n"
        )
        (config / "SUBAGENTS.md").write_text(f"You are a subagent of {item_id}.\n")
        (live_root / "pods" / pod).mkdir(parents=True, exist_ok=True)
        return item_id, workdir

    return _add


def work_item(live_root, item_id, state="idle", pod="engineers", goal="none."):
    path = live_root / "pods" / pod / f"{item_id}-{state}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\npod: {pod}\noutcome:\ndispatched:\n---\n\n"
        f"## Goal\n\n{order}\n\n## Tasks\n- [ ] …\n"
    )
    return path


def wait_for(predicate, *, what, limit=240.0, interval=1.0):
    """Poll until true. Live panes are slow; the limit is generous and the message is useful."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out after {limit}s waiting for {what}")


def pane_text(item_id, env, lines=400):
    from hx import goal

    return goal.capture_pane(item_id, env, lines=lines) or ""


def transcript_of(item_id, env, lines=400):
    """Everything above the input box: what the session has actually done."""
    from hx import goal

    text = pane_text(item_id, env, lines)
    box = goal.input_box(text)
    return text[: len(text) - len(box)]
