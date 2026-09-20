"""Fixtures for the build lane's tests.

Every test runs against a scratch instance: pytest `tmp_path` for the unit tests, and for
the tmux tests a real tmux server on a private socket (`tmux -L hx-test-<pid>`), killed at
teardown. Nothing here ever points HOME, CLAUDE_CONFIG_DIR or HARNESS_ROOT at the user's
own `~/.claude` (ORCHESTRATION.md standing rules).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
FAKE_CLAUDE = REPO / "tests" / "fakeclaude" / "claude"
SKELETON = SRC / "hx" / "skeleton"
ADAPTERS = SKELETON / "adapters" / "claude"

MODELS = {
    "claude-opus-5": {"window": 1000000, "threshold": 500000},
    "claude-sonnet-5": {"window": 1000000, "threshold": 500000},
}

PERSONA = "You are eng-001, an engineer.\nYou commit as you go.\n"
AGENTS_MD = PERSONA + "\n## UPDATES BELOW ONLY\n\nThings I learned: nothing yet.\n"


def clean_env(**extra: str) -> dict[str, str]:
    """A child environment with no inherited HARNESS_* or CLAUDE_* leakage."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HARNESS_", "CLAUDE_", "HX_"))
    }
    env["PYTHONPATH"] = str(SRC)
    env.update(extra)
    return env


@pytest.fixture
def child_env():
    """`clean_env` as a fixture, for tests that drive the adapters through bash."""
    return clean_env


@pytest.fixture
def run_hx(tmp_path):
    """Run `python -m hx …` in a clean environment. Returns the CompletedProcess."""

    def _run(*args: str, env_extra: dict[str, str] | None = None, cwd: Path | None = None):
        env = clean_env(**(env_extra or {}))
        env.setdefault("HOME", str(tmp_path / "fake-home"))
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            [sys.executable, "-m", "hx", *args],
            env=env,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
        )

    return _run


@pytest.fixture
def instance(tmp_path):
    """A scratch HARNESS_ROOT with the layout, the skeleton, and one worker plus the Partner."""
    root = tmp_path / "instance"
    sys.path.insert(0, str(SRC))
    from hx.install import install_skeleton

    install_skeleton(root)
    (root / "config" / "models.json").write_text(json.dumps(MODELS, indent=2) + "\n")

    for item_id, pod, role in (("partner", "partner", "partner"), ("eng-001", "engineers", "engineer")):
        config = root / "config" / item_id
        config.mkdir(parents=True, exist_ok=True)
        harness = {
            "id": item_id,
            "pod": pod,
            "role": role,
            "model": "claude-opus-5",
            "effort": "xhigh",
        }
        if item_id != "partner":
            harness["workdir"] = str(root / "wt" / item_id)
            harness["branch"] = f"agent/{item_id}"
            (root / "wt" / item_id).mkdir(parents=True, exist_ok=True)
        (config / "harness.json").write_text(json.dumps(harness, indent=2) + "\n")
        (config / "AGENTS.md").write_text(
            AGENTS_MD if item_id == "eng-001" else f"You are the Partner.\n\n## UPDATES BELOW ONLY\n"
        )
        (root / "pods" / pod).mkdir(parents=True, exist_ok=True)
        roles = root / "companion" / "roles"
        roles.mkdir(parents=True, exist_ok=True)
        (roles / f"{role}.md").write_text(f"# {role}\n")

    seed_home = root / "seed" / "home"
    seed_home.mkdir(parents=True, exist_ok=True)
    (seed_home / ".credentials.json").write_text('{"fake": "credentials"}\n')

    (root / "config" / "claude.json").write_text(
        json.dumps({"bin": str(FAKE_CLAUDE), "version": "fake-0"}, indent=2) + "\n"
    )
    return root


@pytest.fixture
def work_item(instance):
    """Write a work item for an id in a given state, with its frontmatter."""

    def _write(item_id: str, state: str, *, pod: str | None = None, after=(), outcome=None, dispatched=None):
        pod = pod or ("partner" if item_id == "partner" else "engineers")
        directory = instance / "pods" / pod
        directory.mkdir(parents=True, exist_ok=True)
        for existing in directory.glob(f"{item_id}-*.md"):
            existing.unlink()
        path = directory / f"{item_id}-{state}.md"
        after_text = "[" + ", ".join(after) + "]" if after else "[]"
        path.write_text(
            "---\n"
            f"id: {item_id}\n"
            f"pod: {pod}\n"
            f"after: {after_text}\n"
            f"outcome: {outcome or ''}\n"
            f"dispatched: {dispatched or ''}\n"
            "---\n\n## Order\n\nDo it.\n\n## Tasks\n- [ ] one\n"
        )
        return path

    return _write


@pytest.fixture
def tmux_server(tmp_path):
    """A real tmux server on a private socket, killed at teardown."""
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    socket = f"hx-test-{os.getpid()}-{abs(hash(str(tmp_path))) % 100000}"
    command = ["tmux", "-L", socket]
    yield command
    subprocess.run([*command, "kill-server"], capture_output=True, check=False)


def wait_for(predicate, *, what: str, limit: float = 30.0, interval: float = 0.05):
    """Poll until `predicate` is true. Tests need a bound; hx itself never has a timeout."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out after {limit}s waiting for {what}")
