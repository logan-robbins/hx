"""Standing rule (spec 13 M10, applied at every milestone): the build never touches the
user's own ~/.claude. Compares the configuration manifest of ~/.claude against the
baseline recorded before the build started (.baseline/, written by the orchestrator).

Session-volatile paths (transcripts, caches, history) are excluded by tools/claude-home-hash.sh.
Do not edit the exclusion list to make this pass; a mismatch means something wrote to the
user's Claude home. Report it in handoff/to-orchestrator.md instead.
"""
import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
BASELINE_DIR = pathlib.Path(os.environ.get("HX_CLAUDE_HOME_BASELINE", REPO / ".baseline"))
HOME_CLAUDE = pathlib.Path(os.environ.get("HX_USER_CLAUDE_HOME", pathlib.Path.home() / ".claude"))


def current_manifest() -> str:
    return subprocess.run(
        [str(REPO / "tools" / "claude-home-hash.sh"), str(HOME_CLAUDE)],
        check=True, capture_output=True, text=True,
    ).stdout


def test_user_claude_home_matches_pre_build_baseline():
    baseline_file = BASELINE_DIR / "claude-home.manifest"
    assert baseline_file.exists(), (
        f"no baseline at {baseline_file}; the orchestrator records it before the build starts"
    )
    baseline = baseline_file.read_text()
    now = current_manifest()
    if now != baseline:
        b = set(baseline.splitlines())
        n = set(now.splitlines())
        added = sorted(n - b)
        removed = sorted(b - n)
        raise AssertionError(
            "user ~/.claude changed since the pre-build baseline\n"
            + "".join(f"  + {l}\n" for l in added)
            + "".join(f"  - {l}\n" for l in removed)
        )
