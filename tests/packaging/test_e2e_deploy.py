"""The M10 deploy proof, run as a test (spec 17.2–17.4, 11).

`packaging/e2e-deploy.sh` goes past what `e2e-install.sh` proves — that the wheel builds and
can create a skeleton — to the whole of `hx install`: seeding from a *fake* user Claude home,
mirroring a product repo, cutting a sparse worktree without the product's `.claude/`, launching
an agent into a private tmux server with the fake `claude`, and rendering the boot units into
the machine's own launchd or systemd directory. It ends by proving the real `~/.claude` is
byte-identical to what it was before.

It is a shell script for the same reasons `e2e-install.sh` is: it is what a human runs before a
release, and what it proves is about an *installed* tool, which an in-tree pytest cannot
establish about itself.

Build-4 landed on 2026-09-20 and the proof runs for real. It still skips, with the reason
printed, when the machine cannot host it: no `uv`, `git` or `tmux`, or no `claude` binary on
PATH for the version check `hx install` step 1 performs. Nothing in it ever starts a real
agent — `HX_CLAUDE_BIN` points every launch at the fake `claude`.
"""

import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "packaging" / "e2e-deploy.sh"
#: The script exits 0 with one of these when the machine cannot host the proof.
GATES = ("SKIPPED (waiting on build-4)", "SKIPPED (no claude binary)")


def test_the_script_is_executable_and_shell_clean():
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} is not executable"
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed:\n{r.stderr}"


def test_the_script_refuses_without_a_scratch_directory():
    r = subprocess.run([str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "usage" in (r.stdout + r.stderr).lower()


def test_the_script_seeds_a_token_and_never_reads_a_user_claude_home():
    """Auth is the instance token; hx reads no user Claude home at all (spec 11 Auth). The
    proof writes its own token, and the `~/.claude` it creates exists only to prove that
    nothing in it is ever read."""
    text = SCRIPT.read_text()
    assert "--from-user-config" not in text, (
        "the flag was removed (CONTRACTS.md); the script must not still use it"
    )
    assert "seed/token" in text, "the script does not seed a token"
    assert 'FAKE_USER="$HOME/.claude"' in text, (
        "the planted home must be inside the *fresh* HOME, never the real one"
    )
    for planted in ("USER-CREDENTIALS-LEAKED", "USER-HOOK-LEAKED", "USER-CLAUDE-MD-LEAKED",
                    "USER-SKILL-LEAKED"):
        assert text.count(planted) >= 2, (
            f"{planted} is planted but never asserted against, or vice versa"
        )


def test_the_script_never_launches_a_real_agent():
    """`hx install` step 6 starts the Partner. With a real binary that would be an unattended
    agent burning tokens from a test."""
    text = SCRIPT.read_text()
    assert 'export HX_CLAUDE_BIN="$FAKE_BIN_EARLY"' in text
    assert text.index('export HX_CLAUDE_BIN') < text.index('"$HX" install --root "$ROOT"'), (
        "HX_CLAUDE_BIN must be exported before the first install, which starts the Partner"
    )


def test_end_to_end_deploy(tmp_path, capsys):
    for tool in ("uv", "git", "tmux", "claude"):
        if shutil.which(tool) is None:
            pytest.skip(
                f"{tool} is not on PATH; the deploy proof needs uv, git, tmux, and a claude "
                f"binary for the version check in `hx install` step 1 (no agent is started "
                f"with it — every launch uses the fake claude)"
            )

    scratch = tmp_path / "deploy"
    r = subprocess.run(
        [str(SCRIPT), str(scratch)], capture_output=True, text=True, cwd=str(REPO),
    )
    with capsys.disabled():
        print(r.stdout)
        if r.stderr:
            print(r.stderr)

    for gate in GATES:
        if gate in r.stdout:
            assert r.returncode == 0, "a gate must exit 0, not fail"
            pytest.skip(gate)

    assert r.returncode == 0, f"packaging/e2e-deploy.sh exited {r.returncode}"
    assert "== PASS" in r.stdout, "the script exited 0 without reaching its PASS line"
