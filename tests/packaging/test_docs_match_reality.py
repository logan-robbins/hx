"""`docs/deploy.md`'s command output, checked against the commands.

A doc that quotes real output is worth much more than one that describes it — and goes stale
in a way nobody notices, because prose that was true in September still reads fine in March.
This module builds a real instance and compares what `hx doctor` actually prints against the
block in the doc.

**What is compared, and why not the whole text.** Every line of `hx doctor` is
`<status>  <name>  <detail>`, and the detail is full of absolute paths, versions and commit
shas that differ per machine and per run. Comparing those would make the test fail for reasons
that are not staleness, and the pressure would be to loosen it until it checked nothing. So the
comparison is the ordered sequence of `(status, name)` pairs: that catches a check being added,
removed, renamed, or flipping between `ok` and `warn` — which is every way this doc has
actually gone out of date — and ignores the parts that legitimately vary.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
DEPLOY_MD = REPO / "docs" / "deploy.md"
FAKE_CLAUDE = REPO / "tests" / "fakeclaude" / "claude"

#: `ok    token         seed/token present, mode 0600`
DOCTOR_LINE = re.compile(r"^(ok|warn|fail)\s+(\S+)\s*(.*)$")


def doctor_rows(text: str) -> list[tuple[str, str]]:
    rows = []
    for line in text.splitlines():
        m = DOCTOR_LINE.match(line.rstrip())
        if m:
            rows.append((m.group(1), m.group(2)))
    return rows


def fenced_blocks(text: str) -> list[str]:
    """Fenced blocks, scanned line by line.

    Not a regex: a pattern like ```` ```\n(.*?)\n``` ```` skips a ```` ```bash ```` opener and
    then pairs that block's *closing* fence with the next opener, silently returning prose as
    if it were a code block. That cost an hour once; it is worth eight lines.
    """
    blocks, current = [], None
    for line in text.splitlines():
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    assert current is None, "unbalanced code fence in the document"
    return blocks


def documented_rows() -> list[tuple[str, str]]:
    """The `$ hx doctor` block in docs/deploy.md."""
    blocks = [b for b in fenced_blocks(DEPLOY_MD.read_text()) if b.startswith("$ hx doctor")]
    assert len(blocks) == 1, (
        f"expected exactly one `$ hx doctor` block in docs/deploy.md, found {len(blocks)}"
    )
    return doctor_rows(blocks[0])


def test_the_doc_has_a_doctor_block_worth_checking():
    rows = documented_rows()
    assert len(rows) >= 10, f"only {len(rows)} doctor lines documented; that is not the output"
    assert all(status == "ok" for status, _ in rows), (
        "the documented block is meant to be a healthy instance: every line ok"
    )


def build_instance(root: pathlib.Path, scratch: pathlib.Path) -> dict:
    """A complete instance, the way `hx install` makes one.

    `HX_CLAUDE_BIN` is the fake `claude`, so `hx install` step 6 launches a fake Partner rather
    than a real agent, and `HX_TMUX` keeps that session on a private server this test kills.
    The version check in step 1 needs a *real* binary, so the real one is pinned with `--claude`
    and never launched.
    """
    src = scratch / "product-src"
    shutil.copytree(REPO / "tests" / "scenario" / "m8" / "repo", src)
    git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    subprocess.run([*git, "-C", str(src), "add", "-A"], check=True, capture_output=True)
    subprocess.run([*git, "-C", str(src), "commit", "-qm", "p"], check=True, capture_output=True)
    product = scratch / "product.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(product)], check=True)

    fake = scratch / "bin" / "claude"
    fake.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FAKE_CLAUDE, fake)
    fake.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(scratch / "home"),
        "PYTHONPATH": str(REPO / "src"),
        "HX_CLAUDE_BIN": str(fake),
        "HX_TMUX": f"tmux -L hx-docs-{os.getpid()}",
        "HX_SKILLS_DIR": str(REPO / "src" / "hx" / "skills"),
    }
    env.pop("HARNESS_ROOT", None)
    env.pop("HARNESS_ID", None)
    (scratch / "home").mkdir(parents=True, exist_ok=True)

    # Step 3 stops with exit 4 until the token exists; that is asserted properly by
    # packaging/e2e-deploy.sh, so here it is just the first half of the install.
    first = subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root)],
        capture_output=True, text=True, env=env,
    )
    assert first.returncode == 4, f"expected the seed-token stop, got {first.returncode}:\n{first.stdout}{first.stderr}"

    (root / "seed").mkdir(parents=True, exist_ok=True)
    token = root / "seed" / "token"
    token.write_text("docs-test-token-not-a-real-credential\n")
    token.chmod(0o600)

    second = subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root), "--repo", str(product)],
        capture_output=True, text=True, env=env,
    )
    assert second.returncode == 0, f"{second.stdout}\n{second.stderr}"
    return env


def test_the_documented_doctor_output_is_what_hx_doctor_prints(tmp_path):
    for tool in ("git", "tmux", "claude"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH; a real instance cannot be built here")

    scratch = tmp_path / "s"
    scratch.mkdir()
    root = scratch / "hx"
    env = build_instance(root, scratch)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "hx", "doctor"],
            capture_output=True, text=True, env={**env, "HARNESS_ROOT": str(root)},
        )
    finally:
        subprocess.run(env["HX_TMUX"].split() + ["kill-server"], capture_output=True)

    assert result.returncode == 0, f"hx doctor exited {result.returncode}:\n{result.stdout}"
    actual = doctor_rows(result.stdout)
    documented = documented_rows()

    assert actual == documented, (
        "docs/deploy.md's `$ hx doctor` block no longer matches what hx doctor prints.\n"
        "The doc is the thing to update, not this test.\n\n"
        "real:\n  " + "\n  ".join(f"{s:5} {n}" for s, n in actual)
        + "\n\ndocumented:\n  " + "\n  ".join(f"{s:5} {n}" for s, n in documented)
        + f"\n\nfull output:\n{result.stdout}"
    )


def test_the_documented_install_transcript_names_the_real_steps(tmp_path):
    """The six numbered steps `hx install` prints are quoted in the doc. If one is renamed or
    renumbered, the walkthrough stops matching what the reader sees."""
    text = DEPLOY_MD.read_text()
    for step in ("1. claude ", "2. instance at ", "3. seed token at ", "4. mirrored ",
                 "5. units written to ", "6. partner started"):
        assert step in text, f"docs/deploy.md does not quote the install step {step!r}"


def test_the_documented_exit_code_for_the_seed_stop_is_right():
    text = DEPLOY_MD.read_text()
    assert "exit 4" in text, "the doc does not say the seed-token stop exits 4"
    assert "claude setup-token" in text


def test_the_companion_is_described_as_toolless_everywhere_it_is_described():
    """The Companion reads every agent's stream, so "it has no tools" is the load-bearing
    claim about it. Both pages that introduce it have to carry that, not just one."""
    for path in (REPO / "docs" / "deploy.md", REPO / "docs" / "two-worlds.md", REPO / "README.md"):
        text = path.read_text()
        if "Companion" not in text:
            continue
        assert "claude -p" in text, f"{path.name} describes the Companion without saying how it runs"
        assert "no tools" in text, f"{path.name} does not say the Companion has no tools"


def test_the_agent_branch_is_named_consistently():
    """Settled 2026-09-20: `agent/<id>` everywhere. `hx push` is the one command that reaches a
    real remote, so a doc naming a different branch would be actively misleading."""
    worker = json.loads(
        (REPO / "src" / "hx" / "skeleton" / "templates" / "worker" / "harness.json").read_text()
    )
    assert worker["branch"] == "agent/{{id}}", worker["branch"]
    for path in (REPO / "docs" / "two-worlds.md", REPO / "docs" / "deploy.md", REPO / "README.md"):
        text = path.read_text()
        assert "hx/<id>" not in text, f"{path.name} still names the old hx/<id> branch"
