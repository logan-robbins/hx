"""Fixtures for the build lane's tests.

Every test runs against a scratch instance: pytest `tmp_path` for the unit tests, and for
the tmux tests a real tmux server on a private socket (`tmux -L hx-test-<pid>`), killed at
teardown. Nothing here ever points HOME, CLAUDE_CONFIG_DIR or HARNESS_ROOT at the user's
own `~/.claude` (ORCHESTRATION.md standing rules).
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

    # Auth is one long-lived token per instance (spec 11 Auth, CONTRACTS.md `seed/token`).
    token = root / "seed" / "token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("sk-ant-oat-fake-token\n")
    token.chmod(0o600)

    tested = json.loads(
        (SRC / "hx" / "packaging" / "tested-claude-versions.json").read_text()
    )["versions"][0]
    (root / "config" / "claude.json").write_text(
        json.dumps({"bin": str(FAKE_CLAUDE), "version": tested}, indent=2) + "\n"
    )

    # A real git worktree, so `hx complete done`'s clean-worktree check is exercised rather
    # than skipped (spec 06).
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


ORDER = """---
after: [{after}]
---
## Order

{order}

## Definition of done

- [ ] it is done

### Checks

```bash
{checks}
```
"""


def write_order(root: Path, item_id: str, *, order="Do the thing.", after=(), checks="true") -> Path:
    """An order file that passes spec 06, written where `hx dispatch` expects it."""
    path = root / "orders" / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ORDER.format(after=", ".join(after), order=order, checks=checks))
    return path


def manifest(root: Path, *, skip=()) -> dict[str, str]:
    """sha256 of every file under `root`, for "changes nothing" assertions (spec 13 M1)."""
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
    """Add another worker id to the scratch instance: config, persona, worktree."""

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
                    "branch": f"agent/{item_id}",
                },
                indent=2,
            )
            + "\n"
        )
        (config / "AGENTS.md").write_text(
            f"You are {item_id}.\n\n## UPDATES BELOW ONLY\n\nnothing yet.\n"
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
def orders(instance):
    """Write an order file for an id."""

    def _write(item_id: str, **kwargs):
        return write_order(instance, item_id, **kwargs)

    return _write


@pytest.fixture
def hx(instance, tmux_server):
    """Run `hx <args>` against the scratch instance, on the private tmux server."""

    def _run(*args: str, harness_id: str | None = "partner", cwd: Path | None = None, **env_extra):
        env = clean_env(HARNESS_ROOT=str(instance), HX_TMUX=" ".join(tmux_server), **env_extra)
        if harness_id is not None:
            env["HARNESS_ID"] = harness_id
        env["HOME"] = str(instance.parent / "fake-home")
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
def launched(instance, hx, tmux_server):
    """Launch ids on the private tmux server with the fake `claude`, and wait for the prompt."""

    def _launch(*ids: str, companion: bool = False):
        # Without `--no-companion` every launch starts a Companion loop that polls and calls
        # the model; the Companion has its own tests, which start it deliberately.
        flags = [] if companion else ["--no-companion"]
        for item_id in ids:
            result = hx("launch", *flags, item_id)
            assert result.returncode == 0, f"launch {item_id}: {result.stdout}{result.stderr}"
            wait_for(
                (instance / "run" / item_id / "fake-ready").is_file,
                what=f"{item_id}'s fake pane to come up",
            )
        return instance

    return _launch


@pytest.fixture
def tmux_server(tmp_path):
    """A real tmux server on a private socket, killed and cleaned up at teardown.

    Every live check ends by killing what it launched (goal build-4 item 7). The teardown
    asserts it: a session that outlives its test is a leaked agent, and one of those once
    ended up on the machine's default tmux server, where the lanes themselves run.
    """
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    socket = f"hx-test-{os.getpid()}-{abs(hash(str(tmp_path))) % 100000}"
    command = ["tmux", "-L", socket]

    yield command

    # Kill what the test launched, then prove it is gone. A session that survives its own
    # teardown is a leaked agent; one of those once ended up on the machine's default tmux
    # server, where the lanes themselves run.
    subprocess.run([*command, "kill-server"], capture_output=True, check=False)
    listed = subprocess.run(
        [*command, "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True, check=False,
    )
    survivors = [
        name for name in listed.stdout.split()
        if name == "partner" or name.startswith(("eng-", "rev-", "qa-"))
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


#: A stand-in for the real `claude` during install tests: it answers `--version` with a bare
#: version from the package's tested list and does nothing else.
FAKE_CLAUDE_BIN = """#!/usr/bin/env bash
if [ "$1" = "--version" ]; then echo "{version} (Claude Code)"; exit 0; fi
exec {real} "$@"
"""


@pytest.fixture
def fake_claude_on_path(tmp_path):
    """Put a fake `claude` first on PATH, pinned to a version the package has tested."""
    import json as _json

    tested = _json.loads(
        (SRC / "hx" / "packaging" / "tested-claude-versions.json").read_text()
    )["versions"][0]
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    binary = bindir / "claude"
    binary.write_text(FAKE_CLAUDE_BIN.format(version=tested, real=FAKE_CLAUDE))
    binary.chmod(0o755)
    return {"PATH": f"{bindir}:{os.environ.get('PATH', '')}", "HX_FAKE_CLAUDE_VERSION": tested}


#: A stand-in for `claude -p` in Companion tests: it reads the payload on stdin and prints the
#: `--output-format json` envelope, with a scripted step state in `structured_output`. The
#: script is a JSON list of responses, consumed one per call.
FAKE_COMPANION = '''#!/usr/bin/env python3
import json, os, pathlib, sys

script_path = pathlib.Path(os.environ["HX_FAKE_COMPANION_SCRIPT"])
calls_path = script_path.with_suffix(".calls.jsonl")
payload = sys.stdin.read()

with calls_path.open("a") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "payload": payload}) + "\\n")

responses = json.loads(script_path.read_text())
index = sum(1 for _ in calls_path.read_text().splitlines()) - 1
response = responses[min(index, len(responses) - 1)]

envelope = {
    "type": "result",
    "session_id": "fake",
    "usage": response.get("usage", {"input_tokens": 100, "cache_read_input_tokens": 0 if index == 0 else 4096}),
    "total_cost_usd": 0.0,
}
if "raw_result" in response:
    envelope["result"] = response["raw_result"]
elif "state" in response:
    envelope["structured_output"] = response["state"]
else:
    envelope["result"] = response.get("text", "")
print(json.dumps(envelope))
'''


@pytest.fixture
def fake_companion(tmp_path):
    """Script the Companion's model. Returns (set_responses, calls) helpers."""
    binary = tmp_path / "fake-companion"
    script = tmp_path / "companion-script.json"
    binary.write_text(FAKE_COMPANION)
    binary.chmod(0o755)
    script.write_text("[]")

    class Fake:
        env = {"HX_COMPANION_BIN": str(binary), "HX_FAKE_COMPANION_SCRIPT": str(script)}

        def responses(self, *items):
            script.write_text(json.dumps(list(items)))
            calls = script.with_suffix(".calls.jsonl")
            calls.unlink(missing_ok=True)

        def calls(self):
            path = script.with_suffix(".calls.jsonl")
            if not path.is_file():
                return []
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    return Fake()
