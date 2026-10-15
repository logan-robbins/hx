"""Only admitted dependency packets may enter worker execution."""
import importlib.util
import pytest
from hx.cli import COMMANDS


def pasted(root, item_id):
    path = root / "run" / item_id / "fake-input.log"
    return path.read_text() if path.is_file() else ""


@pytest.mark.parametrize("command", ["dispatch", "goal", "resume", "amend", "bench", "distill", "archive", "recall", "memory", "restart"])
def test_removed_lifecycle_commands_cannot_be_invoked(command, run_hx, tmp_path):
    assert command not in COMMANDS
    result = run_hx(command, env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 2
    assert "unknown command" in result.stderr


@pytest.mark.parametrize("module", ["dispatch", "resume", "amend", "bench", "distill", "archive", "recall", "memory", "tasks", "workitems"])
def test_old_task_authority_has_no_importable_implementation(module):
    assert importlib.util.find_spec("hx." + module) is None
