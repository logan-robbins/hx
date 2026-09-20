"""Standing rule: hx refuses any HARNESS_ROOT that is, or lies inside, the user's ~/.claude
(directly or through a symlink), and never derives an instance path from the user's Claude
home. `python -m hx` must exist (src/hx/__main__.py) and every command must resolve
HARNESS_ROOT through one function that applies this check before doing anything else.
"""
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]


def run_hx(args, env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("HARNESS_ROOT", "HARNESS_ID")}
    env.update(env_extra)
    env["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run([sys.executable, "-m", "hx", *args], env=env, capture_output=True, text=True)


def test_refuses_root_equal_to_user_claude_home(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    r = run_hx(["doctor"], {"HOME": str(home), "HARNESS_ROOT": str(home / ".claude")})
    assert r.returncode != 0
    assert "refuse" in (r.stdout + r.stderr).lower()


def test_refuses_root_inside_user_claude_home(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "hx").mkdir(parents=True)
    r = run_hx(["doctor"], {"HOME": str(home), "HARNESS_ROOT": str(home / ".claude" / "hx")})
    assert r.returncode != 0
    assert "refuse" in (r.stdout + r.stderr).lower()


def test_refuses_root_symlinked_into_user_claude_home(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "hx").mkdir(parents=True)
    link = tmp_path / "hx-link"
    link.symlink_to(home / ".claude" / "hx")
    r = run_hx(["doctor"], {"HOME": str(home), "HARNESS_ROOT": str(link)})
    assert r.returncode != 0
    assert "refuse" in (r.stdout + r.stderr).lower()


def test_refuses_the_real_user_claude_home():
    real = pathlib.Path.home() / ".claude"
    r = run_hx(["doctor"], {"HARNESS_ROOT": str(real / "hx")})
    assert r.returncode != 0
    assert "refuse" in (r.stdout + r.stderr).lower()
