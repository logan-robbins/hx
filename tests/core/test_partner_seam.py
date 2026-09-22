"""The Partner takes seams like any agent (spec 12, 02 Seams).

Regression: the Partner was exempted from the seam path at three points — the
`log` hard trigger never marked it, `stop` deleted its marker as stale, and
`seam()` refused it for having no work item — so a long Partner turn grew to
native autocompaction, the lossy path seams exist to replace. A Partner seam
is `/clear` + rehydrate from its context file; no goal pointer is ever sent.
"""

from __future__ import annotations

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


def test_a_partner_seam_rehydrates_without_a_goal_pointer(instance, launched, tmux_server):
    """The context file carries PARTNER.md, and the pane gets `/clear` only."""
    from hx.seam import seam

    from .test_transitions import pasted

    launched("partner")
    (instance / "run" / "partner" / "fake-input.log").write_text("")

    result = seam(instance, "partner", env={"HX_TMUX": " ".join(tmux_server)})
    assert result["outcome"] == "taken"
    context = instance / "run" / "partner" / "partner-main.context.md"
    assert "PARTNER.md" in context.read_text()
    assert "/goal" not in pasted(instance, "partner")
