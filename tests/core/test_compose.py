"""Partner orchestration context and mandatory native identity for worker hooks."""
import json
import subprocess
import sys
import uuid
import pytest
from hx import compose
from hx.errors import Refused
from .conftest import clean_env

def run_hook(instance, item_id, event, payload=None, *, tmux=None, harness_id=None, **env_extra):
    env = clean_env(HARNESS_ROOT=str(instance), **env_extra)
    env["HX_TMUX"] = " ".join(tmux or ["tmux", "-L", "hx-hook-test-" + uuid.uuid4().hex])
    if harness_id:
        env["HARNESS_ID"] = harness_id
    return subprocess.run(
        [sys.executable, "-m", "hx.hooks", "--id", item_id, event],
        input=json.dumps(payload or {}),
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
def test_partner_context_preserves_notes_and_updates_current_state(instance, source):
    path = instance / "PARTNER.md"
    path.write_text("# Partner\n\nCurrent directive: ship lookup.\n\n## Status\n\nstale\n\n## Decisions\n\nPublish API before consumers.\n")
    result = run_hook(instance, "partner", "context", {"source": source})
    assert result.returncode == 0, result.stderr
    handed = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
    assert 'Current directive: ship lookup.' in handed
    assert 'Publish API before consumers.' in handed
    assert 'Use the Read tool once' not in handed
    assert "Current directive: ship lookup." in path.read_text()
    assert "Publish API before consumers." in path.read_text()
    assert "stale" not in path.read_text()
    assert "Memory episodes" not in path.read_text()


@pytest.mark.parametrize("event", ["context", "request", "tool-start"])
def test_worker_hooks_require_native_run_identity(instance, event):
    result = run_hook(instance, "eng-001", event)
    assert result.returncode == 2
    assert "admitted run" in result.stderr
    assert not (instance / "tasks.json").exists()


def test_worker_compose_cannot_build_context_from_file_backed_task(instance):
    with pytest.raises(Refused, match="immutable checkpoint"):
        compose.compose(instance, "eng-001")


def test_partner_status_does_not_treat_fenced_heading_as_section(instance):
    text = "# Notes\n\n```md\n## Status\ninside example\n```\n\n## Status\nold\n\n## Decisions\nkeep\n"
    result = compose.with_status(text, "current")
    assert "inside example" in result and "## Decisions\nkeep" in result
    assert "## Status\n\ncurrent" in result and "\nold\n" not in result


def test_file_status_writer_cannot_mutate_worker_checkpoint(instance):
    with pytest.raises(Refused, match="immutable"):
        compose.write_status(instance, "eng-001", [])
