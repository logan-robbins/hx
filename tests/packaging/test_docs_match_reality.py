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
import time

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
    """A complete instance, the way `hx install` makes one: four steps, partner only.

    `HX_CLAUDE_BIN` is the fake `claude`, so `hx install` step 6 launches a fake Partner rather
    than a real agent, and `HX_TMUX` keeps that session on a private server this test kills.
    The version check in step 1 needs a *real* binary, so the real one is pinned with `--claude`
    and never launched.
    """
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
        [sys.executable, "-m", "hx", "install", "--root", str(root)],
        capture_output=True, text=True, env=env,
    )
    assert second.returncode == 0, f"{second.stdout}\n{second.stderr}"
    wait_for_the_pane_to_exec(env)
    return env


def wait_for_the_pane_to_exec(env: dict, seconds: float = 15.0) -> None:
    """Wait until the Partner's pane is the agent rather than the launcher.

    `hx install` step 6 returns as soon as tmux has the session, but `start.sh` then does its
    refusals and derives the persona before it `exec`s the binary. For that moment the pane's
    argv is the launcher's, so `hx doctor`'s live-agent check reads no
    `--dangerously-skip-permissions` and reports a `fail` that resolves itself a moment later.
    This test documents a settled instance, so it waits. (Reported to the build lane: a check
    that fails on a race is worse than no check, because it teaches people to re-run.)
    """
    deadline = time.monotonic() + seconds
    last = ""
    while time.monotonic() < deadline:
        panes = subprocess.run(
            [*env["HX_TMUX"].split(), "list-panes", "-t", "=partner:main", "-F", "#{pane_pid}"],
            capture_output=True, text=True, env=env,
        )
        pid = panes.stdout.strip().splitlines()[0] if panes.stdout.strip() else ""
        if pid:
            listing = subprocess.run(
                ["ps", "-o", "command=", "-p", pid], capture_output=True, text=True
            )
            last = listing.stdout.strip()
            if "--dangerously-skip-permissions" in last:
                return
        time.sleep(0.2)
    raise AssertionError(
        f"the Partner's pane never exec'd the binary within {seconds}s; last argv was: {last!r}"
    )


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
    """The four numbered steps `hx install` prints are quoted in the doc. If one is renamed or
    renumbered, the walkthrough stops matching what the reader sees.

    Four, not six: spec 14 D25 cut the mirror and the shipped unit files, and 17.2 renumbered
    what was left. A doc showing six steps is describing a version that no longer exists.
    """
    text = DEPLOY_MD.read_text()
    for step in ("1. claude ", "2. instance at ", "3. seed token at ", "4. partner started"):
        assert step in text, f"docs/deploy.md does not quote the install step {step!r}"
    for gone in ("mirrored ", "units written to ", "6. partner"):
        assert gone not in text, f"docs/deploy.md still quotes the cut install step {gone!r}"


def test_the_documented_exit_code_for_the_seed_stop_is_right():
    text = DEPLOY_MD.read_text()
    assert "exit 4" in text, "the doc does not say the seed-token stop exits 4"
    assert "claude setup-token" in text


def test_the_companion_is_described_as_a_near_toolless_session_everywhere():
    """The Companion reads every agent's stream, so what it *cannot* do is the load-bearing
    claim about it, and every page that introduces it has to carry that.

    It is a tmux Claude Code session now, not a `claude -p` call (CONTRACTS.md, "The Companion
    is a tmux session"), with Read and Write and nothing else — so "no tools" is no longer the
    right phrase, and a doc still using it is describing the headless design.
    """
    for name in ("deploy.md", "two-worlds.md", "operating.md", "getting-started.md"):
        path = REPO / "docs" / name
        if not path.is_file():
            continue
        text = path.read_text()
        assert "claude -p" not in text, (
            f"docs/{name} still describes the Companion as a headless `claude -p` call"
        )
    readme = (REPO / "README.md").read_text()
    assert "claude -p" not in readme, "README.md still describes a headless Companion"
    assert "two tools and nothing else" in readme, (
        "README.md does not say what the Companion can and cannot do"
    )


def test_hx_manages_no_git_anywhere_in_the_docs_or_the_template():
    """Spec 14 D25 cut git management entirely: no mirror, no worktree, no branch, no push.
    The branch name was argued over for two goals and then the whole question went away —
    which is exactly the kind of thing that survives in prose long after it stops being true."""
    worker = json.loads(
        (REPO / "src" / "hx" / "skeleton" / "templates" / "worker" / "harness.json").read_text()
    )
    assert "branch" not in worker, "the worker template still names a branch"
    for name in ("two-worlds.md", "deploy.md", "operating.md", "getting-started.md"):
        path = REPO / "docs" / name
        if not path.is_file():
            continue
        text = path.read_text()
        for cut in ("hx push", "hx repo add", "sparse checkout", "bare mirror", "agent/<id>"):
            assert cut not in text, f"docs/{name} still describes {cut!r} (cut, spec 14 D25)"
    readme = (REPO / "README.md").read_text()
    for cut in ("hx push", "bare mirror", "sparse worktree"):
        assert cut not in readme, f"README.md still describes {cut!r} (cut, spec 14 D25)"


# --------------------------------------------------- docs/operating.md and the /goal pointer

OPERATING_MD = REPO / "docs" / "operating.md"
SPEC_06 = REPO / "spec" / "06-work-items.md"


def spec_06_goal_pointer() -> str:
    """The one `/goal …` line spec 06 fixes, taken from the spec rather than retyped."""
    lines = [l for l in SPEC_06.read_text().splitlines() if l.startswith("/goal The goal for")]
    assert len(lines) == 1, f"spec/06-work-items.md has {len(lines)} `/goal` lines, expected 1"
    return lines[0]


def test_operating_quotes_the_goal_pointer_exactly_as_spec_06_fixes_it():
    """The pointer is a contract, not a paraphrase: hx pastes this text byte for byte at every
    conversation start of a working item, and the operator page is where a human meets it.

    Quoting it is the whole point — a reader who sees it in a pane needs to recognise it, and
    a reader who sees something *else* in a pane needs to know that is wrong. Retyped, it would
    drift from the spec the first time either side was reworded, and nothing would notice.
    """
    pointer = spec_06_goal_pointer()
    text = OPERATING_MD.read_text()
    assert pointer in text, (
        "docs/operating.md does not quote spec 06's `/goal` pointer verbatim.\n"
        f"  spec 06: {pointer}\n"
        "  Quote that line exactly, inside a fenced block."
    )
    assert "pointer" in text, (
        "docs/operating.md quotes the line but never says it is a pointer rather than the task"
    )


def test_the_goal_pointer_is_quoted_the_same_way_in_the_worker_skill():
    """Two places carry it — the operator page and the skill the worker reads. They are written
    for different readers, so they drift independently unless something holds them together."""
    pointer = spec_06_goal_pointer()
    skill = (REPO / "src" / "hx" / "skills" / "hx-worker" / "SKILL.md").read_text()
    assert pointer in skill, "hx-worker/SKILL.md no longer quotes spec 06's `/goal` pointer"


# ------------------------------------------------ docs/getting-started.md against the real CLI

GETTING_STARTED_MD = REPO / "docs" / "getting-started.md"


def test_getting_started_quotes_commands_that_exist():
    """Every hx command the first-run page tells a human to type, checked against the CLI.

    This page is the one a person follows with a terminal open, so a flag that was renamed
    costs them the install. `--repo` is the example: it was on this page, and `hx install`
    stopped accepting it when D25 cut git management.
    """
    text = GETTING_STARTED_MD.read_text()
    assert "hx install --root ~/hx" in text
    assert "--repo" not in text, "getting-started still passes --repo to hx install (cut, D25)"

    help_text = subprocess.run(
        [sys.executable, "-m", "hx", "install", "--help"],
        capture_output=True, text=True, cwd=str(REPO),
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    ).stdout
    for flag in ("--root", "--claude"):
        assert flag in help_text, f"hx install no longer accepts {flag}"


def test_getting_started_matches_what_install_really_prints():
    """The numbers, the exit code and the last line, from `hx install` itself."""
    text = GETTING_STARTED_MD.read_text()
    assert "exit 4" in text, "the page does not say the first run stops with exit 4"
    assert "claude setup-token" in text
    assert "chmod 600" in text, "the page does not set the token file's mode"
    assert "tmux attach -t partner" in text, (
        "the page does not end where `hx install` ends, at the Partner's session"
    )
    from hx import install as install_mod

    assert install_mod.TOKEN_WAIT_EXIT == 4, (
        f"hx install now stops with {install_mod.TOKEN_WAIT_EXIT}, not 4; the page says 4"
    )


def test_getting_started_pins_the_versions_the_package_really_carries():
    text = GETTING_STARTED_MD.read_text()
    tested = json.loads(
        (REPO / "src" / "hx" / "packaging" / "tested-claude-versions.json").read_text()
    )["versions"]
    for version in tested:
        assert version in text, f"getting-started does not name the tested claude {version}"
    pyproject = (REPO / "pyproject.toml").read_text()
    wheel_version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert f"hx_harness-{wheel_version}-py3-none-any.whl" in text, (
        f"getting-started installs a wheel that is not {wheel_version}"
    )
    assert "--python 3.14" in text, "the page does not pin the Python hx requires"


# ---------------------------------------------- the personas and their Companion role files

PERSONAS = REPO / "src" / "hx" / "skeleton" / "personas"
ROLES = REPO / "src" / "hx" / "skeleton" / "companion" / "roles"
SHIPPED_ROLES = ("backend-engineer", "frontend-engineer", "release-engineer")


def test_every_shipped_role_has_both_halves():
    """A role is a pair: the persona the agent runs as, and the retention rules its Companion
    keeps. `hx launch` refuses a role with no role file, so a persona shipped without one is a
    worker that cannot start."""
    for role in (*SHIPPED_ROLES, "partner"):
        assert (PERSONAS / role / "AGENTS.md").is_file(), f"no persona for {role}"
        assert (ROLES / f"{role}.md").is_file(), f"no companion role file for {role}"


def test_no_persona_is_shipped_without_a_role_file_or_the_other_way_round():
    personas = sorted(p.name for p in PERSONAS.iterdir() if p.is_dir())
    roles = sorted(p.stem for p in ROLES.glob("*.md"))
    assert personas == roles, (personas, roles)


def test_the_worker_template_defaults_to_a_role_that_ships():
    worker = json.loads(
        (REPO / "src" / "hx" / "skeleton" / "templates" / "worker" / "harness.json").read_text()
    )
    assert worker["role"] in SHIPPED_ROLES, worker["role"]
    assert (PERSONAS / worker["role"] / "AGENTS.md").is_file()


def test_the_partner_config_is_the_partner_persona():
    """`config/partner/AGENTS.md` is what the Partner launches with; `personas/partner/AGENTS.md`
    is what the Partner would copy to rebuild it. Two texts would mean two Partners."""
    config = (REPO / "src" / "hx" / "skeleton" / "config" / "partner" / "AGENTS.md").read_text()
    persona = (PERSONAS / "partner" / "AGENTS.md").read_text()
    assert config == persona, (
        "config/partner/AGENTS.md has drifted from personas/partner/AGENTS.md"
    )
