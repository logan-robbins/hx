"""Fixtures for the core tests.

Every test runs against a scratch instance: pytest `tmp_path` for the unit tests, and for
the tmux tests a real tmux server on a private socket (`tmux -L hx-test-<pid>`), killed at
teardown. Nothing here ever points HOME, CLAUDE_CONFIG_DIR or HARNESS_ROOT at the user's
own `~/.claude`.
"""

from __future__ import annotations

import hashlib
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

#: The shipped `config/models.json`: a 1M window, native autocompaction pulled down to 250k by
#: `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, and hx's own seam threshold below that so the seam always
#: lands first (`config_models.py`).
MODELS = {
    "claude-opus-5": {"window": 1000000, "autocompact_window": 250000, "threshold": 200000},
    "claude-sonnet-5": {"window": 1000000, "autocompact_window": 250000, "threshold": 200000},
}

#: eng-001's `purpose`: the one per-id paragraph its persona is rendered with (`hx.persona`).
PERSONA = "You are eng-001, an engineer.\nYou commit as you go.\n"


def manifest_skills(root: Path, item_id: str, **env: str) -> set[str]:
    """The skills `src/hx/skills/manifest.json` gives `<id>`'s home."""
    sys.path.insert(0, str(SRC))
    from hx import skill_manifest

    return set(skill_manifest.for_agent(SRC / "hx" / "skills", root, item_id, env=env))


def installed_skills(path: Path) -> set[str]:
    """The skill directories actually installed under one home's skills directory."""
    return {p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")}


def fake_home(path: Path) -> Path:
    """A scratch HOME for a child hx. Nothing of the operator's `~/.claude` reaches it; the
    embedding model cache `~/.cache/chroma` is shared by symlink when the operator has one, so
    no test fetches the 80 MB model, and without one `hx memory` reports unavailable (it never
    downloads: `hx.memory._embedding_function`)."""
    path.mkdir(parents=True, exist_ok=True)
    real = Path(os.environ.get("HOME", "")) / ".cache" / "chroma"
    link = path / ".cache" / "chroma"
    if real.is_dir() and not link.exists():
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(real)
    return path


def clean_env(**extra: str) -> dict[str, str]:
    """A child environment with no inherited HARNESS_* or CLAUDE_* leakage."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HARNESS_", "CLAUDE_", "HX_"))
    }
    env["PYTHONPATH"] = str(SRC)
    # hx runs every adapter with the interpreter it runs on (`lifecycle.run_adapter`).
    env["HX_PYTHON"] = sys.executable
    env.update(extra)
    return env


@pytest.fixture(autouse=True)
def host_jobs_ledger(tmp_path, monkeypatch):
    """Each test joins equal executions only within its own host job ledger (`hx.host_jobs`)."""
    from hx import host_jobs

    monkeypatch.setattr(host_jobs, "directory", lambda: tmp_path / "host-jobs")


@pytest.fixture(autouse=True)
def search_ledger(tmp_path, monkeypatch):
    """Each test reuses searches only from its own host search ledger (`hx.search_coverage`)."""
    from hx import search_coverage

    monkeypatch.setattr(search_coverage, "directory", lambda: tmp_path / "host-search")


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
        fake_home(Path(env["HOME"]))
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
            harness["purpose"] = PERSONA.strip("\n")
            (root / "wt" / item_id).mkdir(parents=True, exist_ok=True)
        (config / "harness.json").write_text(json.dumps(harness, indent=2) + "\n")
        if item_id == "partner":
            (root / "personas" / "partner.md").write_text("You are the Partner.\n")
        (root / "pods" / pod).mkdir(parents=True, exist_ok=True)
        roles = root / "companion" / "roles"
        roles.mkdir(parents=True, exist_ok=True)
        (roles / f"{role}.md").write_text(f"# {role}\n")

    # Auth is one long-lived token per instance (CONTRACTS.md `seed/token`).
    token = root / "seed" / "token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("sk-ant-oat-fake-token\n")
    token.chmod(0o600)

    # Record the fake as the instance's binary, as `hx install` records the real one.
    (root / "config" / "claude.json").write_text(json.dumps({"bin": str(FAKE_CLAUDE)}, indent=2) + "\n")

    # A real git worktree, so `hx complete done`'s clean-worktree check is exercised rather
    # than skipped.
    workdir = root / "wt" / "eng-001"
    subprocess.run(["git", "init", "-q", str(workdir)], check=True, capture_output=True)
    for key, value in (("user.email", "hx@example.invalid"), ("user.name", "hx test")):
        subprocess.run(["git", "-C", str(workdir), "config", key, value], check=True)
    (workdir / "README.md").write_text("scratch worktree\n")
    subprocess.run(["git", "-C", str(workdir), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(workdir), "commit", "-qm", "initial"], check=True, capture_output=True
    )
    return root


@pytest.fixture
def work_item(instance):
    """Write a work item for an id in a given state, with its frontmatter."""

    def _write(item_id: str, state: str, *, pod: str | None = None, outcome=None, dispatched=None):
        pod = pod or ("partner" if item_id == "partner" else "engineers")
        directory = instance / "pods" / pod
        directory.mkdir(parents=True, exist_ok=True)
        for existing in directory.glob(f"{item_id}-*.md"):
            existing.unlink()
        path = directory / f"{item_id}-{state}.md"
        path.write_text(
            "---\n"
            f"id: {item_id}\n"
            f"pod: {pod}\n"
            f"outcome: {outcome or ''}\n"
            f"dispatched: {dispatched or ''}\n"
            "---\n\n## Goal\n\nDo it.\n\n## Tasks\n- [ ] one\n"
        )
        return path

    return _write


#: A goal is one `## Goal` paragraph and nothing else.
GOAL = """## Goal

{goal}
"""

#: What a worker leaves in `## Evidence` before `hx complete done`, which refuses it empty.
EVIDENCE = "- the change is committed: `git log --oneline -1` in the workdir (fixture)"


def write_goal(root: Path, item_id: str, *, goal="Do the thing.") -> Path:
    """A goal file that passes the goal validator. Any path will do; `hx dispatch` deletes it."""
    path = root / "run" / f"goal-{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(GOAL.format(goal=goal))
    return path


def write_evidence(root: Path, item_id: str, text: str = EVIDENCE) -> Path:
    """The worker's half of `hx complete done`: a written `## Evidence` in its work item."""
    from hx.workitems import SECTION_EVIDENCE, find_work_item, replace_section, section_bounds

    path = find_work_item(root, item_id)
    if section_bounds(path.read_text(), SECTION_EVIDENCE) is None:
        path.write_text(path.read_text().rstrip("\n") + f"\n\n{SECTION_EVIDENCE}\n{text}\n")
    else:
        replace_section(path, SECTION_EVIDENCE, text)
    return path


def manifest(root: Path, *, skip=()) -> dict[str, str]:
    """sha256 of every file under `root`, for "changes nothing" assertions."""
    import hashlib

    found = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if any(relative == s or relative.startswith(s.rstrip("/") + "/") for s in skip):
            continue
        if path.is_symlink():
            found[relative] = "link:" + os.readlink(path)
        elif path.is_file():
            found[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            found[relative] = "dir"
    return found


@pytest.fixture
def agent(instance):
    """Add another worker id to the scratch instance: config (with its purpose), worktree."""

    def _add(item_id: str, *, pod: str = "engineers", role: str = "engineer", git: bool = False):
        config = instance / "config" / item_id
        config.mkdir(parents=True, exist_ok=True)
        (config / "harness.json").write_text(
            json.dumps(
                {
                    "id": item_id,
                    "pod": pod,
                    "role": role,
                    "model": "claude-opus-5",
                    "effort": "high",
                    "workdir": f"wt/{item_id}",
                    "purpose": f"You are {item_id}.",
                },
                indent=2,
            )
            + "\n"
        )
        workdir = instance / "wt" / item_id
        workdir.mkdir(parents=True, exist_ok=True)
        (instance / "pods" / pod).mkdir(parents=True, exist_ok=True)
        roles = instance / "companion" / "roles"
        roles.mkdir(parents=True, exist_ok=True)
        (roles / f"{role}.md").write_text(f"# {role}\n")
        if git:
            subprocess.run(["git", "init", "-q", str(workdir)], check=True, capture_output=True)
            for key, value in (("user.email", "hx@example.invalid"), ("user.name", "hx test")):
                subprocess.run(["git", "-C", str(workdir), "config", key, value], check=True)
            (workdir / "README.md").write_text("scratch worktree\n")
            subprocess.run(["git", "-C", str(workdir), "add", "README.md"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(workdir), "commit", "-qm", "initial"], check=True, capture_output=True
            )
        return item_id

    return _add


@pytest.fixture
def goals(instance):
    """Write a goal file for an id."""

    def _write(item_id: str, **kwargs):
        return write_goal(instance, item_id, **kwargs)

    return _write


@pytest.fixture
def hx(instance, tmux_server):
    """Run `hx <args>` against the scratch instance, on the private tmux server."""

    def _run(*args: str, harness_id: str | None = "partner", cwd: Path | None = None, **env_extra):
        # `start.sh` forwards every HX_* variable onto the tmux session, so the fake claude
        # in the pane can find the hook binary.
        env = clean_env(
            HARNESS_ROOT=str(instance),
            HX_TMUX=" ".join(tmux_server),
            HX_HOOK_BIN=f"{sys.executable} -m hx.hooks",
            **env_extra,
        )
        if harness_id is not None:
            env["HARNESS_ID"] = harness_id
        env["HOME"] = str(fake_home(instance.parent / "fake-home"))
        return subprocess.run(
            [sys.executable, "-m", "hx", *args],
            env=env,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
        )

    return _run


@pytest.fixture
def launched(instance, hx, tmux_server):
    """Bootstrap the Partner. Worker tests use explicit admitted native runs."""

    def _launch(*ids: str, companion: bool = False):
        for item_id in ids:
            if item_id != "partner":
                raise AssertionError("worker tests must admit a dependency packet and launch its run")
            result = hx("launch", item_id)
            assert result.returncode == 0, f"launch {item_id}: {result.stdout}{result.stderr}"
            wait_for(
                (instance / "run" / item_id / "fake-ready").is_file,
                what=f"{item_id}'s fake pane to come up",
            )
            if companion:
                result = hx("companion", item_id)
                assert result.returncode == 0, result.stdout + result.stderr
        return instance

    return _launch


@pytest.fixture
def tmux_server(tmp_path):
    """A real tmux server on a private socket, killed and cleaned up at teardown.

    Every live check ends by killing what it launched, and the teardown asserts it: a session
    that outlives its test is a leaked agent.
    """
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    socket = f"hx-test-{os.getpid()}-{abs(hash(str(tmp_path))) % 100000}"
    command = ["tmux", "-L", socket]

    yield command

    # Kill what the test launched, then prove it is gone. A session that survives its own
    # teardown is a leaked agent.
    subprocess.run([*command, "kill-server"], capture_output=True, check=False)
    listed = subprocess.run(
        [*command, "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True, check=False,
    )
    survivors = [
        name for name in listed.stdout.split()
        if name == "partner" or name.startswith(("eng-", "rev-", "qa-", "hx-"))
    ]
    _remove_socket_file(socket)
    assert not survivors, (
        f"these sessions outlived the teardown: {survivors}. A live check kills what it "
        f"launched (goal build-4 item 7)."
    )


def _remove_socket_file(socket: str) -> None:
    """tmux leaves the socket behind when the server exits; do not litter `/tmp`."""
    for base in (os.environ.get("TMUX_TMPDIR"), f"/private/tmp/tmux-{os.getuid()}",
                 f"/tmp/tmux-{os.getuid()}"):
        if not base:
            continue
        try:
            Path(base, socket).unlink(missing_ok=True)
        except OSError:
            pass


def wait_for(predicate, *, what: str, limit: float = 30.0, interval: float = 0.05):
    """Poll until `predicate` is true. Tests need a bound; hx itself never has a timeout."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out after {limit}s waiting for {what}")


@pytest.fixture
def fake_claude_on_path():
    """Put the fake `claude` first on PATH, where `hx install` step 1 finds it."""
    return {"PATH": f"{FAKE_CLAUDE.parent}:{os.environ.get('PATH', '')}"}
