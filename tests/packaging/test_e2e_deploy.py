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

While the full `hx install` is still build-4, the script prints its gate and exits 0 with
`SKIPPED (waiting on build-4)`, and this test skips with that reason rather than passing
silently — a green test for a proof that did not run is worse than no test.
"""

import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "packaging" / "e2e-deploy.sh"
GATE = "SKIPPED (waiting on build-4)"


def test_the_script_is_executable_and_shell_clean():
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} is not executable"
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed:\n{r.stderr}"


def test_the_script_refuses_without_a_scratch_directory():
    r = subprocess.run([str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "usage" in (r.stdout + r.stderr).lower()


def test_the_script_never_reads_the_real_claude_home_for_seeding():
    """`--from-user-config` is pointed at a directory the script builds. If this ever names
    `$HOME/.claude` as the seed source, the proof would be reading the user's real
    credentials — which is exactly what the flag exists to make unnecessary here."""
    text = SCRIPT.read_text()
    assert "--from-user-config" in text
    seed_line = next(line for line in text.splitlines() if "--from-user-config" in line and "$HX" in line)
    assert "$FAKE_USER" in seed_line, seed_line
    # The real home appears only as the before/after manifest subject.
    for line in text.splitlines():
        if "REAL_CLAUDE_HOME" in line and "from-user-config" in line:
            pytest.fail(f"the real Claude home is used as a seed source: {line}")


def test_end_to_end_deploy(tmp_path, capsys):
    for tool in ("uv", "git", "tmux"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH; the deploy proof needs uv, git and tmux")

    scratch = tmp_path / "deploy"
    r = subprocess.run(
        [str(SCRIPT), str(scratch)], capture_output=True, text=True, cwd=str(REPO),
    )
    with capsys.disabled():
        print(r.stdout)
        if r.stderr:
            print(r.stderr)

    if GATE in r.stdout:
        assert r.returncode == 0, "the gate must exit 0, not fail"
        gate_line = next(
            (line.strip() for line in r.stdout.splitlines() if line.strip().startswith("gate")),
            "",
        )
        pytest.skip(f"{GATE}: {gate_line or 'hx install (full) is not built yet'}")

    assert r.returncode == 0, f"packaging/e2e-deploy.sh exited {r.returncode}"
    assert "== PASS" in r.stdout, "the script exited 0 without reaching its PASS line"
