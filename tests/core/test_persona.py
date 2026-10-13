"""`hx persona` — the system prompt rendered from its sources at every launch.

docs/prompt-runtime.md § Prompt layers: the persona is the role persona
(`personas/<role>/AGENTS.md`), the per-id `purpose` from `harness.json`, the invariants only
where the persona is their channel (Pi, Grok), and the learnings region. Nothing is copied per
id; `start.sh --exec` renders `run/<id>/persona.md` immediately before it execs. `migrate`
moves an instance off the per-id `config/<id>/AGENTS.md` it had before.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from hx import learn, persona
from hx.compile import GLOBAL_BEGIN, LEARNINGS_BEGIN, LEARNINGS_END
from hx.errors import ValidationError

from .conftest import PERSONA, clean_env


def _harness(instance, item_id="eng-001"):
    return json.loads((instance / "config" / item_id / "harness.json").read_text())


def _set(instance, item_id="eng-001", **fields):
    path = instance / "config" / item_id / "harness.json"
    body = json.loads(path.read_text())
    body.update(fields)
    path.write_text(json.dumps(body, indent=2) + "\n")


def _role(instance, role="engineer", text="# {{id}}: Engineer\n\nYou are `{{id}}`, an engineer in pod `{{pod}}`.\n\n## How you work\n\nCommit as you go.\n"):
    path = instance / "personas" / role / "AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_the_persona_is_the_role_persona_with_the_purpose_after_its_opening(instance):
    _role(instance)
    text = persona.render(instance, "eng-001")
    assert text.startswith("# eng-001: Engineer\n\nYou are `eng-001`, an engineer in pod `engineers`.")
    purpose = PERSONA.strip("\n")
    assert purpose in text
    assert text.index("You are `eng-001`") < text.index(purpose) < text.index("## How you work")
    assert "{{" not in text


def test_the_learnings_region_is_last_and_carries_every_rule_whole(instance):
    _role(instance)
    assert persona.render(instance, "eng-001").rstrip().endswith(LEARNINGS_END)
    assert "0 rule(s)" in persona.render(instance, "eng-001")
    proposal = learn.propose(instance, signal="a count quoted without its scope", check="every count carries its scope",
                             evidence="FLAWS_3", category="evidence", env={"HARNESS_ID": "eng-001"})
    learn.accept(instance, proposal["id"], env={"HARNESS_ID": "partner"})
    text = persona.render(instance, "eng-001")
    assert "1 rule(s), last L1" in text and "### evidence — " in text
    assert learn.read_rules(instance / "LEARNINGS")[0]["line"] in text
    assert str(instance / "LEARNINGS") in text and "hx learn propose --category" in text


def test_the_region_carries_the_agents_roles_rules_and_no_other_roles(instance):
    _role(instance)
    partner = {"HARNESS_ID": "partner"}
    for signal, role in (("for everyone", None), ("for engineers", "engineer"), ("for qa", "qa-engineer")):
        proposal = learn.propose(instance, signal=signal, check="c", evidence="e", category="build",
                                 role=role, env={"HARNESS_ID": "eng-001"})
        learn.accept(instance, proposal["id"], env=partner)
    text = persona.render(instance, "eng-001")
    assert "for everyone" in text and "for engineers" in text and "for qa" not in text
    assert text.index("for everyone") < text.index("### engineer · build") < text.index("for engineers")
    assert "for engineers" not in persona.render(instance, "partner")


def test_a_role_without_a_persona_gets_one_identity_line(instance):
    text = persona.render(instance, "eng-001")
    assert text.startswith(persona.FALLBACK.format(id="eng-001", role="engineer", pod="engineers"))
    assert PERSONA.strip("\n") in text


@pytest.mark.parametrize("value", [None, "", "{{purpose}}"])
def test_no_purpose_until_the_partner_writes_one(instance, value):
    _role(instance)
    body = _harness(instance)
    if value is None:
        body.pop("purpose")
    else:
        body["purpose"] = value
    (instance / "config" / "eng-001" / "harness.json").write_text(json.dumps(body))
    text = persona.render(instance, "eng-001")
    assert "{{purpose}}" not in text and PERSONA.strip("\n") not in text
    from hx.doctor import run_checks
    rows = [row for row in run_checks(instance) if row[1] == "purpose:eng-001"]
    assert rows and rows[0][0] == "warn"


def test_a_written_purpose_clears_the_doctor_row(instance):
    from hx.doctor import run_checks
    assert not [row for row in run_checks(instance) if row[1] == "purpose:eng-001"]


def test_purpose_must_be_a_string(instance):
    from hx.config_harness import load_harness
    _set(instance, purpose=["not", "a", "string"])
    with pytest.raises(ValidationError):
        load_harness(instance / "config" / "eng-001" / "harness.json", check_cross_file=False)


def test_no_invariants_in_a_claude_persona(instance):
    assert GLOBAL_BEGIN not in persona.render(instance, "eng-001")


@pytest.mark.parametrize("flavor", ["pi", "grok"])
def test_the_invariants_ride_first_where_the_persona_is_their_channel(instance, flavor):
    _set(instance, flavor=flavor)
    text = persona.render(instance, "eng-001")
    assert text.startswith(GLOBAL_BEGIN) and text.count(GLOBAL_BEGIN) == 1
    assert (instance / "config" / "CLAUDE.md").read_text().strip() in text
    assert text.index(GLOBAL_BEGIN) < text.index(PERSONA.strip("\n")) < text.index(LEARNINGS_BEGIN)


@pytest.mark.parametrize("flavor", ["meta", "codex"])
def test_no_invariants_where_the_context_file_carries_them(instance, flavor):
    _set(instance, flavor=flavor)
    assert GLOBAL_BEGIN not in persona.render(instance, "eng-001")


def test_the_partner_keeps_its_persona_file_plus_the_learnings(instance):
    before = (instance / "config" / "partner" / "AGENTS.md").read_text()
    text = persona.render(instance, "partner")
    assert text.startswith("You are the Partner.")
    assert text.count(LEARNINGS_BEGIN) == 1 and GLOBAL_BEGIN not in text
    assert "UPDATES BELOW ONLY" not in text
    assert (instance / "config" / "partner" / "AGENTS.md").read_text() == before, "a source is never written"


def test_write_renders_run_persona_md_and_rewrites_only_on_change(instance):
    first = persona.write(instance, "eng-001")
    assert first["written"] and first["path"] == "run/eng-001/persona.md"
    assert (instance / first["path"]).read_text() == persona.render(instance, "eng-001")
    assert persona.write(instance, "eng-001")["written"] is False
    _role(instance)
    assert persona.write(instance, "eng-001")["written"] is True
    assert "## How you work" in (instance / "run" / "eng-001" / "persona.md").read_text()


def test_hx_persona_prints_its_line_and_the_module_form_is_what_start_sh_runs(instance, run_hx):
    result = run_hx("persona", "eng-001", "--root", str(instance))
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("HX-PERSONA eng-001 run/eng-001/persona.md bytes=")
    shown = run_hx("persona", "eng-001", "--print", "--root", str(instance))
    assert shown.stdout == (instance / "run" / "eng-001" / "persona.md").read_text()
    module = subprocess.run([sys.executable, "-m", "hx.persona", "--root", str(instance), "eng-001"],
                            env=clean_env(), capture_output=True, text=True)
    assert module.returncode == 0, module.stderr
    assert module.stdout.endswith("unchanged\n")


def test_plain_compile_points_at_hx_persona(instance, run_hx):
    result = run_hx("compile", "eng-001", "--root", str(instance))
    assert result.returncode == 2 and "hx persona eng-001" in result.stderr


def test_launch_renders_the_persona_and_delivers_an_invariants_edit_through_the_home(instance, hx, launched):
    """On Claude the invariants' one channel is the home's native CLAUDE.md, refreshed at
    launch; the persona does not carry a second copy."""
    clauses = instance / "config" / "CLAUDE.md"
    clauses.write_text(clauses.read_text() + "\nLaunched invariant.\n")
    launched("eng-001")
    assert "Launched invariant." in (instance / "run" / "eng-001" / "home" / "CLAUDE.md").read_text()
    rendered = (instance / "run" / "eng-001" / "persona.md").read_text()
    assert "Launched invariant." not in rendered
    assert PERSONA.strip("\n") in rendered
    assert not (instance / "config" / "eng-001" / "AGENTS.md").exists()


# --- migration from config/<id>/AGENTS.md -------------------------------------------------------


TEMPLATE_UPPER = (
    "# eng-001\n\n<!--\n  Template. Copy me.\n\n  More template notes.\n-->\n\n"
    "You are `eng-001`, an engineer in the `engineers` pod.\n\n"
)


def _legacy(instance, upper, lower="", item_id="eng-001"):
    path = instance / "config" / item_id / "AGENTS.md"
    path.write_text(upper + "## UPDATES BELOW ONLY\n" + lower)
    return path


def test_migration_moves_the_per_id_paragraph_into_purpose_and_memory_into_a_proposal(instance):
    _set(instance, purpose="{{purpose}}")
    path = _legacy(instance, "You own the importer: src/importer/** and its tests.\n\n",
                   "\nThe importer's fixtures live in tests/data; `make fixtures` rebuilds them.\n")
    result = persona.migrate(instance, "eng-001")
    assert result["purpose_moved"] is True and result["proposal"]
    assert _harness(instance)["purpose"] == "You own the importer: src/importer/** and its tests."
    assert not path.exists()
    proposal = learn.load_proposal(instance / "LEARNINGS", result["proposal"])
    assert proposal["kind"] == "text" and proposal["author"] == "eng-001"
    assert proposal["scope"] == "engineer", "what one worker learned is owed to its role"
    assert "`make fixtures` rebuilds them" in proposal["text"]
    assert persona.migrate(instance, "eng-001") is None, "idempotent: nothing left to move"


def test_migration_drops_what_a_shared_source_already_says(instance):
    """A copy of the template and the role persona is not per-id text, and the template's
    below-header starter is not memory."""
    template = instance / "templates" / "worker" / "AGENTS.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(TEMPLATE_UPPER.replace("eng-001", "{{id}}").replace("`engineers`", "`{{pod}}`")
                        + "## UPDATES BELOW ONLY\n\nEverything below this line is yours.\n\n- (nothing yet)\n")
    _role(instance)
    role_copy = (instance / "personas" / "engineer" / "AGENTS.md").read_text().replace("{{id}}", "eng-001").replace("{{pod}}", "engineers")
    _set(instance, purpose="{{purpose}}")
    _legacy(instance, TEMPLATE_UPPER + role_copy + "\n", "\nEverything below this line is yours.\n\n- (nothing yet)\n")
    result = persona.migrate(instance, "eng-001")
    assert result == {"id": "eng-001", "purpose_moved": False, "proposal": None}
    assert _harness(instance)["purpose"] == "{{purpose}}"
    assert learn.list_proposals(instance / "LEARNINGS") == []


def test_migration_never_overwrites_a_written_purpose(instance):
    _legacy(instance, "A different per-id paragraph.\n\n")
    result = persona.migrate(instance, "eng-001")
    assert _harness(instance)["purpose"] == PERSONA.strip("\n")
    text = learn.load_proposal(instance / "LEARNINGS", result["proposal"])["text"]
    assert "A different per-id paragraph." in text and "already had a purpose" in text


def test_migration_keeps_distilled_directives_and_drops_stamped_regions(instance):
    upper = (
        "<!-- hx:learnings begin (old) -->\nold rules\n<!-- hx:learnings end -->\n\n"
        "## Distilled directives\n\n<!-- hx:distilled begin (promoted by `hx distill`) -->\n"
        "Always run the importer twice.\n<!-- hx:distilled end -->\n\n"
    )
    _legacy(instance, upper)
    result = persona.migrate(instance, "eng-001")
    text = learn.load_proposal(instance / "LEARNINGS", result["proposal"])["text"]
    assert "Always run the importer twice." in text and "old rules" not in text


def test_the_partner_is_not_migrated(instance):
    assert persona.migrate(instance, "partner") is None
    assert (instance / "config" / "partner" / "AGENTS.md").is_file()


def test_a_text_proposal_is_accepted_only_with_the_reviewers_signal_and_check(instance):
    _legacy(instance, "", "\nTwo lessons: run make fixtures; never edit tests/data by hand.\n")
    proposal_id = persona.migrate(instance, "eng-001")["proposal"]
    partner = {"HARNESS_ID": "partner"}
    with pytest.raises(ValidationError):
        learn.accept(instance, proposal_id, env=partner)
    with pytest.raises(ValidationError):
        learn.accept(instance, proposal_id, signal="fixtures stale", check="make fixtures",
                     env={"HARNESS_ID": "eng-001"})
    first = learn.accept(instance, proposal_id, signal="fixtures are stale", check="run make fixtures first",
                         category="build", keep=True, env=partner)
    assert first["rule"] == "L1"
    assert learn.load_proposal(instance / "LEARNINGS", proposal_id)["rules"] == ["L1"]
    learn.accept(instance, proposal_id, signal="tests/data edited by hand", check="regenerate, never edit",
                 category="testing", env=partner)
    assert learn.list_proposals(instance / "LEARNINGS") == []
    assert [rule["key"] for rule in learn.read_rules(instance / "LEARNINGS")] == ["L1", "L2"]


def test_launch_migrates_a_legacy_file(instance, hx, launched):
    _set(instance, purpose="{{purpose}}")
    _legacy(instance, "You own the importer.\n\n", "\nMemory worth keeping.\n")
    launched("eng-001")
    assert _harness(instance)["purpose"] == "You own the importer."
    assert "You own the importer." in (instance / "run" / "eng-001" / "persona.md").read_text()
    assert len(learn.list_proposals(instance / "LEARNINGS")) == 1


def test_install_migrates_every_worker(instance):
    from hx.install import main as install_main
    _set(instance, purpose="{{purpose}}")
    _legacy(instance, "You own the importer.\n\n")
    assert install_main(["--skeleton-only", "--root", str(instance)], env=clean_env(HOME=str(instance.parent / "fake-home"))) == 0
    assert _harness(instance)["purpose"] == "You own the importer."
    assert not (instance / "config" / "eng-001" / "AGENTS.md").exists()
