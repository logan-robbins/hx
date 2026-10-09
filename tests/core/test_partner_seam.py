"""The Partner takes seams like any agent (spec 12, 02 Seams).

Regression: the Partner was exempted from the seam path at three points — the
`log` hard trigger never marked it, `stop` deleted its marker as stale, and
`seam()` refused it for having no work item — so a long Partner turn grew to
native autocompaction, the lossy path seams exist to replace. A Partner seam
is `/clear` + rehydrate from its context file; no goal pointer is ever sent.
"""

from __future__ import annotations

import json
import subprocess

from .test_compose import run_hook
from .test_streams import events, post_tool, transcript_with


def test_the_log_hook_marks_a_partner_seam_at_the_threshold(instance, tmp_path):
    """spec 02, 09.1: the hard trigger covers the Partner too."""
    from hx.hook_log import seam_marker, threshold_for

    threshold = threshold_for(instance, "partner")
    assert threshold == 200_000, "the seam threshold comes from config/models.json"

    run_hook(instance, "partner", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=threshold - 1)))
    assert not seam_marker(instance, "partner").exists()

    run_hook(instance, "partner", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=threshold)))
    assert seam_marker(instance, "partner").is_file()


def test_stop_takes_a_pending_partner_seam(instance, launched, tmux_server):
    """spec 09.2: the marker is consumed via `hx seam`, not deleted as stale."""
    from hx.hook_log import seam_marker

    from .conftest import wait_for
    from .test_transitions import pasted

    launched("partner")
    marker = seam_marker(instance, "partner")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    (instance / "run" / "partner" / "fake-input.log").write_text("")

    result = run_hook(instance, "partner", "stop",
                      {"hook_event_name": "Stop", "background_tasks": []}, tmux=tmux_server)
    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "a taken seam consumes its marker"
    wait_for(lambda: "/clear" in pasted(instance, "partner"), what="the `/clear` hx seam pasted")
    assert events(instance, "partner", "partner-main")[-1] == "seam"


def test_a_partner_seam_rehydrates_without_a_goal_pointer(instance, launched, tmux_server, monkeypatch):
    """The context file carries PARTNER.md, and the pane gets `/clear` only."""
    from hx.seam import seam
    from hx import memory

    def no_archive(*args, **kwargs):
        raise AssertionError("Partner reset must not enqueue a memory episode")

    monkeypatch.setattr(memory, "enqueue_quietly", no_archive)

    from .test_transitions import pasted

    launched("partner")
    (instance / "run" / "partner" / "fake-input.log").write_text("")

    result = seam(instance, "partner", env={"HX_TMUX": " ".join(tmux_server)})
    assert result["outcome"] == "taken"
    context = instance / "run" / "partner" / "partner-main.context.md"
    assert "PARTNER.md" in context.read_text()
    assert "/goal" not in pasted(instance, "partner")


def test_partner_restores_decisions_while_workers_keep_debugging_state(instance, monkeypatch):
    from hx import compose
    state = {'seq': 3, 'goal': 'Ship the parser.',
             'decisions': [{'d': 'Wait for the shared interface.', 'why': 'QA depends on it.'}],
             'open_steps': [{'id': 'assign', 'intent': 'Unblock QA.', 'next': 'Assign QA after eng-001 completes.'}],
             'blockers': ['eng-001 owns the interface.'],
             'working_set': {'last_failure': 'pytest tests/parser -q: expected 2, got 1',
                             'files': [{'path': 'parser.py', 'note': 'The branch at :19 is under repair.'}]},
             'closed_steps': [{'id': 'old', 'outcome': 'INTERMEDIATE EXCHANGE'}],
             'dead_ends': ['Avoid regex parsing; nested input fails.']}
    for item in ('partner', 'eng-001'):
        path = instance / 'state' / item / (item + '-main.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    partner = compose.step_state(instance, 'partner', 'partner-main')[1]
    worker = compose.step_state(instance, 'eng-001', 'eng-001-main')[1]
    assert 'Assign QA after eng-001 completes.' in partner and 'QA depends on it.' in partner
    assert 'pytest tests/parser' not in partner and 'INTERMEDIATE EXCHANGE' not in partner
    assert 'pytest tests/parser' in worker and 'Avoid regex parsing' in worker and 'parser.py' in worker
    monkeypatch.setattr(compose, 'memory_episodes', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('no episode lookup')))
    monkeypatch.setattr(compose, 'memory', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('no personal memory read')))
    text = compose.compose_text(instance, 'partner', 'partner-main', env={})
    assert '## PARTNER.md' in text and '## Board' in text
    assert '## Memory' not in text and 'INTERMEDIATE EXCHANGE' not in text


def test_claude_partner_reset_attaches_state_without_a_read_or_summary(instance, monkeypatch):
    from hx import hook_context, compose
    (instance / 'PARTNER.md').write_text('Ship the parser. eng-001 owns implementation; QA waits.')
    monkeypatch.setattr(compose, 'memory_episodes', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('no episode lookup')))
    code, output = hook_context.handle({'source': 'clear'}, 'partner', instance, env={})
    assert code == 0
    attached = json.loads(output)['hookSpecificOutput']
    assert attached['hookEventName'] == 'SessionStart'
    assert 'QA waits.' in attached['additionalContext']
    assert 'Use the Read tool once' not in output
def test_partner_seam_targets_its_actual_suffixed_tmux_pane(instance, tmux_server):
    """A hook from partner-460 must not look for an absent partner:main pane."""
    from hx.goal import capture_pane, target_of
    from hx.seam import pane_target

    subprocess.run([*tmux_server, "new-session", "-d", "-s", "partner-460",
                    "-n", "claude.exe", "sleep", "30"], check=True)
    pane = subprocess.run([*tmux_server, "display-message", "-p", "-t", "partner-460",
                           "#{pane_id}"], capture_output=True, text=True, check=True).stdout.strip()
    env = {"HX_TMUX": " ".join(tmux_server), "TMUX_PANE": pane}
    assert pane_target("partner", env) == pane
    assert target_of(pane) == pane
    assert capture_pane(pane, env) is not None
    assert pane_target("eng-001", env) == "eng-001", "a foreign pane is never selected"
