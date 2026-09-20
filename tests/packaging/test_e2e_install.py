"""The end-to-end packaging check, run as a test (spec 17, M10 part 1).

`packaging/e2e-install.sh` builds the wheel, installs it as a uv tool into a HOME that did not
exist a moment ago, and runs the installed `hx` against a fresh instance. It is a shell script
rather than a pytest body because it is also the thing a human runs by hand before a release,
and because what it proves — that an *installed* tool works — is exactly what an in-tree
`pytest` cannot prove about itself.

This wrapper runs it under `tmp_path` and surfaces its output. It is skipped only when `uv` is
absent, with the reason printed.
"""

import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "packaging" / "e2e-install.sh"


def test_the_script_is_executable_and_shell_clean():
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} is not executable"
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed:\n{r.stderr}"


def test_the_script_refuses_without_a_scratch_directory():
    r = subprocess.run([str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "usage" in (r.stdout + r.stderr).lower()


def test_end_to_end_install(tmp_path, capsys):
    if shutil.which("uv") is None:
        pytest.skip(
            "uv is not on PATH, so the wheel cannot be built or installed as a tool; "
            "install it with `curl -LsSf https://astral.sh/uv/install.sh | sh` "
            "(the packaging milestone M10 requires it, and CI installs it)"
        )

    scratch = tmp_path / "e2e"
    r = subprocess.run(
        [str(SCRIPT), str(scratch)], capture_output=True, text=True, cwd=str(REPO),
    )
    # The script names its own failing step; show the whole transcript either way, because a
    # packaging failure is never diagnosable from an assertion message alone.
    with capsys.disabled():
        print(r.stdout)
        if r.stderr:
            print(r.stderr)
    assert r.returncode == 0, f"packaging/e2e-install.sh exited {r.returncode}"
    assert "== PASS" in r.stdout, "the script exited 0 without reaching its PASS line"
