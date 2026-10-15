"""Prompt transport never confirms native dialogs or claims task completion."""
from unittest.mock import Mock

import pytest

from hx import goal


MODAL = """Replace goal?
New objective: An unrelated native objective
› 1. Replace current goal  Set the new objective and start it now
  2. Cancel  Keep the current goal
enter select · esc back"""


def test_native_modal_is_recognized_and_never_idle():
    assert goal.replacement_prompt(MODAL) == 'An unrelated native objective'
    assert not goal.pane_is_idle(MODAL)


def test_transport_never_confirms_native_modal(monkeypatch):
    monkeypatch.setattr(goal, 'capture_pane', lambda *a, **kw: MODAL)
    run = Mock(side_effect=AssertionError('must not send Enter to native modal'))
    monkeypatch.setattr(goal.subprocess, 'run', run)
    monkeypatch.setattr(goal, '_POLL_ATTEMPTS', 1)
    assert not goal.submit('session', 'Read the current assignment context', {})
    run.assert_not_called()


def test_codex_input_box_excludes_transcript():
    text = 'Read the task context at /private/current.md once.'
    assert text not in goal.input_box(text + '\n› Ask Codex to do anything')
    assert text in goal.input_box('› ' + text)


@pytest.mark.parametrize('name,target', [('session', '=session:main'), ('session:main', '=session:main'), ('%7', '%7')])
def test_transport_target_is_exact(name, target):
    assert goal.target_of(name) == target


def test_goal_module_has_no_assignment_or_completion_authority():
    for name in ('send_goal', 'task_revision', 'task_completed', 'pointer_text', 'native_goals', 'main'):
        assert not hasattr(goal, name)
