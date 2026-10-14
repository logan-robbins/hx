"""`hx persona` — what the `context` hook hands an agent at every boundary, from its sources.

docs/prompt-runtime.md § Prompt layers: the invariants and the persona where the hook is their
channel (`config_harness.INVARIANTS_CHANNEL`, `PERSONA_CHANNEL`), the identity line
(`You are <id> in pod <pod>. <purpose>`), every rule file that holds a rule (global, then the
role's), and one pointer to the work item. Nothing is copied per id and nothing is rendered
into a file. `migrate_layout` and `migrate` move an instance off the layouts before this one.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from hx import learn, persona
from hx.errors import NotFound, ValidationError
from hx.hook_context import CONTEXT_LINE

from .conftest import PERSONA, clean_env

PARTNER_ENV = {"HARNESS_ID": "partner"}
IDENTITY = "You are eng-001 in pod engineers. You are eng-001, an engineer. You commit as you go."
ROLE_TEXT = "# Engineer\n\nYou are an engineer in this hx instance.\n\n## How you work\n\nCommit as you go.\n"


def _harness(instance, item_id="eng-001"):
    return json.loads((instance / "config" / item_id / "harness.json").read_text())


def _set(instance, item_id="eng-001", **fields):
    path = instance / "config" / item_id / "harness.json"
    body = json.loads(path.read_text())
    body.update(fields)
    path.write_text(json.dumps(body, indent=2) + "\n")


def _role(instance, role="engineer", text=ROLE_TEXT):
    path = instance / "personas" / f"{role}.md"
    path.write_text(text)
    return path


def _rule(instance, signal, *, role=None, category="build"):
    proposal = learn.propose(instance, signal=signal, check="c", evidence="e", category=category,
                             role=role, env={"HARNESS_ID": "eng-001"})
    return learn.accept(instance, proposal["id"], env=PARTNER_ENV)


def _text(instance, item_id="eng-001"):
    return persona.boundary(instance, item_id)["text"]


# --- the chain ------------------------------------------------------------------------------------


def test_a_claude_agent_is_handed_persona_identity_rules_and_pointer_in_that_order(instance, work_item):
    _role(instance)
    _rule(instance, "for everyone")
    _rule(instance, "for engineers", role="engineer", category="testing")
    item = work_item("eng-001", "working")
    handed = persona.boundary(instance, "eng-001")
    text = handed["text"]
    pointer = CONTEXT_LINE.format(path=item.resolve())
    assert text.startswith(ROLE_TEXT.strip("\n")), "the role persona, whole, first"
    assert text.rstrip("\n").endswith(pointer), "the one Read of the boundary, last"
    order = [ROLE_TEXT.strip("\n"), IDENTITY, "# LEARNINGS · build", "for everyone",
             "# engineer · testing", "for engineers", pointer]
    assert [text.index(piece) for piece in order] == sorted(text.index(piece) for piece in order)
    assert (instance / "LEARNINGS" / "build.md").read_text().strip("\n") in text, "each rule file whole"
    assert [name for name, _, _ in handed["parts"]] == ["persona", "identity", "rules", "rules", "pointer"]
    assert handed["file"] == item and handed["as_files"] == [] and handed["bound"] == 10_000
    assert "{{" not in text and "hx:learnings" not in text


def test_rule_files_without_a_rule_and_other_roles_rules_are_not_handed(instance, work_item):
    _rule(instance, "for qa", role="qa-engineer")
    work_item("eng-001", "working")
    text = _text(instance)
    assert "for qa" not in text and "# LEARNINGS ·" not in text, "the shipped headers alone are no rule"
    _rule(instance, "for engineers", role="engineer")
    assert "for engineers" not in _text(instance, "partner")


def test_the_identity_line_carries_the_purpose_on_one_line(instance):
    assert persona.identity_line(instance, "eng-001") == IDENTITY
    assert persona.identity_line(instance, "partner") == "You are partner in pod partner."


@pytest.mark.parametrize("value", [None, "", "{{purpose}}"])
def test_no_purpose_until_the_partner_writes_one(instance, value):
    body = _harness(instance)
    if value is None:
        body.pop("purpose")
    else:
        body["purpose"] = value
    (instance / "config" / "eng-001" / "harness.json").write_text(json.dumps(body))
    assert persona.identity_line(instance, "eng-001") == "You are eng-001 in pod engineers."
    assert "{{purpose}}" not in _text(instance) and PERSONA.strip("\n") not in _text(instance)
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


def test_an_agent_with_no_task_is_told_its_goal_comes_as_a_pointer(instance, work_item):
    _role(instance)
    assert _text(instance).rstrip("\n").endswith(persona.IDLE_LINE)
    work_item("eng-001", "idle")
    assert _text(instance).rstrip("\n").endswith(persona.IDLE_LINE), "an idle item has nothing to read"
    work_item("eng-001", "done", outcome="done")
    assert persona.boundary(instance, "eng-001")["file"] is None


def test_the_partners_boundary_file_is_partner_md(instance):
    partner_md = instance / "PARTNER.md"
    partner_md.write_text("# Partner\n")
    text = _text(instance, "partner")
    assert text.startswith("You are the Partner.\n\nYou are partner in pod partner.")
    assert text.rstrip("\n").endswith(CONTEXT_LINE.format(path=partner_md.resolve()))
    partner_md.unlink()
    assert persona.IDLE_LINE not in _text(instance, "partner"), "the Partner gets no /goal"


def test_a_role_without_a_persona_gets_identity_rules_and_pointer(instance):
    assert _text(instance).startswith(IDENTITY)
    (instance / "personas" / "partner.md").unlink()
    with pytest.raises(NotFound, match="Partner's persona is missing"):
        persona.boundary(instance, "partner")


@pytest.mark.parametrize("flavor,invariants,role_persona", [
    ("claude", False, True), ("pi", True, False), ("grok", True, False),
    ("meta", True, True), ("codex", False, False),
])
def test_each_source_reaches_each_flavor_through_one_channel(instance, flavor, invariants, role_persona):
    _role(instance)
    _set(instance, flavor=flavor)
    text = _text(instance)
    clauses = (instance / "config" / "CLAUDE.md").read_text().strip("\n")
    assert (clauses in text) is invariants and text.count(clauses) <= 1
    assert (ROLE_TEXT.strip("\n") in text) is role_persona
    assert IDENTITY in text
    if invariants and role_persona:
        assert text.index(clauses) < text.index(ROLE_TEXT.strip("\n")) < text.index(IDENTITY)


# --- the bound ------------------------------------------------------------------------------------


def test_past_the_bound_the_rules_go_as_files_then_the_persona_too(instance, work_item, monkeypatch):
    _role(instance)
    _rule(instance, "for everyone " + "x" * 400)
    _rule(instance, "for engineers " + "y" * 400, role="engineer")
    work_item("eng-001", "working")
    whole = persona.boundary(instance, "eng-001")
    assert whole["as_files"] == []

    monkeypatch.setitem(persona.HOOK_BYTES_BY_FLAVOR, "claude", whole["bytes"] - 1)
    bounded = persona.boundary(instance, "eng-001")
    assert bounded["as_files"] == ["LEARNINGS/build.md", "personas/engineer/build.md"]
    assert "x" * 400 not in bounded["text"] and ROLE_TEXT.strip("\n") in bounded["text"]
    assert "Your learnings are not printed here" in bounded["text"]
    assert f"- {(instance / 'LEARNINGS' / 'build.md').resolve()}" in bounded["text"]
    assert bounded["bytes"] <= bounded["bound"]

    monkeypatch.setitem(persona.HOOK_BYTES_BY_FLAVOR, "claude", 600)
    tight = persona.boundary(instance, "eng-001")
    assert tight["as_files"] == ["personas/engineer.md", "LEARNINGS/build.md", "personas/engineer/build.md"]
    assert ROLE_TEXT.strip("\n") not in tight["text"] and IDENTITY in tight["text"]
    assert "Your persona and learnings are not printed here" in tight["text"]
    from hx.doctor import run_checks
    rows = [row for row in run_checks(instance) if row[1] == "hook-size"]
    assert rows and rows[0][0] == "warn" and "eng-001 (personas/engineer.md" in rows[0][2]


def test_the_bound_is_claude_codes_hook_cap_on_claude_and_32_kib_elsewhere():
    assert persona.hook_bound("claude") == 10_000
    assert persona.hook_bound("pi") == persona.hook_bound("meta") == 32 * 1024


# --- the CLI and the launch -----------------------------------------------------------------------


def test_hx_persona_prints_what_the_hook_prints(instance, run_hx, work_item):
    work_item("eng-001", "working")
    result = run_hx("persona", "eng-001", "--root", str(instance))
    assert result.returncode == 0, result.stderr
    assert result.stdout == _text(instance)
    summary = run_hx("persona", "eng-001", "--summary", "--root", str(instance))
    assert summary.stdout.startswith("HX-PERSONA eng-001 bytes=") and "bound=10000" in summary.stdout


def test_hx_compile_is_gone(instance, run_hx):
    result = run_hx("compile", "eng-001", "--planned", "--root", str(instance))
    assert result.returncode != 0
    with pytest.raises(ImportError):
        __import__("hx.compile")


def test_launch_delivers_an_invariants_edit_through_the_home_and_renders_no_file(instance, hx, launched):
    """On Claude the invariants' one channel is the home's native CLAUDE.md, refreshed at
    launch; the persona arrives through the hook, with no system-prompt flag and no file."""
    stale = instance / "run" / "eng-001" / "persona.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("what an older hx rendered\n")
    clauses = instance / "config" / "CLAUDE.md"
    clauses.write_text(clauses.read_text() + "\nLaunched invariant.\n")
    launched("eng-001")
    assert "Launched invariant." in (instance / "run" / "eng-001" / "home" / "CLAUDE.md").read_text()
    assert not stale.exists(), "launch removes the file an older hx rendered"
    argv = json.loads((instance / "run" / "eng-001" / "fake-argv.json").read_text())["argv"]
    assert "--append-system-prompt-file" not in argv
    assert "Launched invariant." not in _text(instance)
    assert not (instance / "config" / "eng-001" / "AGENTS.md").exists()


# --- migration from the persona layout before personas/<role>.md ------------------------------------


def test_an_old_role_persona_moves_to_personas_role_md(instance):
    old = instance / "personas" / "engineer" / "AGENTS.md"
    old.parent.mkdir(parents=True)
    old.write_text("# {{id}}: Engineer\n\nYou are `{{id}}`, in pod `{{pod}}`.\n")
    results = persona.migrate_layout(instance)
    assert [r for r in results if r.get("persona")] == [
        {"persona": "personas/engineer/AGENTS.md", "role": "engineer", "proposal": None, "moved": True}]
    assert (instance / "personas" / "engineer.md").read_text() == "# <id>: Engineer\n\nYou are `<id>`, in pod `<pod>`.\n"
    assert not old.parent.exists(), "the emptied role directory goes"
    assert "is now personas/engineer.md" in persona.describe_migration(results[0])
    assert persona.migrate_layout(instance) == [], "idempotent"


def test_a_shipped_old_persona_is_removed_and_an_edited_one_leaves_a_proposal(instance, monkeypatch):
    _role(instance)
    old = instance / "personas" / "engineer" / "AGENTS.md"
    old.parent.mkdir(parents=True)
    shipped = "# {{id}}: Engineer\n\nShipped words.\n"
    old.write_text(shipped)
    digest = hashlib.sha256(persona._norm_persona(shipped).encode()).hexdigest()
    monkeypatch.setattr(persona, "SHIPPED_PERSONAS", frozenset({digest}))
    (result,) = persona.migrate_layout(instance)
    assert result["proposal"] is None and not old.exists()
    assert (instance / "personas" / "engineer.md").read_text() == ROLE_TEXT, "the new persona stands"

    old.parent.mkdir()
    old.write_text(ROLE_TEXT + "\nAlways run the importer twice.\n")
    (result,) = persona.migrate_layout(instance)
    proposal = learn.load_proposal(instance / "LEARNINGS", result["proposal"])
    assert "Always run the importer twice." in proposal["text"] and "Commit as you go." not in proposal["text"]
    assert proposal["scope"] == "engineer" and proposal["author"] == "hx-migration"


def test_the_old_partner_persona_moves_its_memory_into_a_proposal(instance):
    old = instance / "config" / "partner" / "AGENTS.md"
    old.write_text("You are the Partner.\n\n<!-- hx:learnings begin (old) -->\nold\n<!-- hx:learnings end -->\n\n"
                   "## UPDATES BELOW ONLY\n\nThe CI box needs `make deps` first.\n")
    (result,) = persona.migrate_layout(instance)
    assert not old.exists() and (instance / "personas" / "partner.md").read_text() == "You are the Partner.\n"
    proposal = learn.load_proposal(instance / "LEARNINGS", result["proposal"])
    assert "`make deps` first" in proposal["text"] and proposal["scope"] == "partner"
    assert proposal["author"] == "partner", "so the Partner cannot accept its own; the human decides"
    assert persona.migrate(instance, "partner") is None, "the Partner has no per-id file"


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
    _set(instance, purpose="{{purpose}}")
    _legacy(instance, TEMPLATE_UPPER + ROLE_TEXT + "\n", "\nEverything below this line is yours.\n\n- (nothing yet)\n")
    result = persona.migrate(instance, "eng-001")
    assert result == {"id": "eng-001", "agents_removed": True, "purpose_moved": False, "proposal": None}
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


def test_a_copied_subagents_md_is_removed_and_a_written_one_stays(instance):
    from hx import doctor

    path = instance / "config" / "eng-001" / "SUBAGENTS.md"
    template = (instance / "templates" / "worker" / "SUBAGENTS.md").read_text()
    old_comment = ("<!--\n  Template. Copied to config/<id>/SUBAGENTS.md with {{id}} and {{pod}} replaced.\n-->")
    copied = template.split("-->", 1)[1].replace("{{id}}", "eng-001").replace("{{pod}}", "engineers")
    path.write_text("# Subagents of eng-001\n\n" + old_comment + copied)
    result = persona.migrate(instance, "eng-001")
    assert result["subagents_removed"] is True and not path.exists(), "a copy, whatever its comment said"
    assert "SUBAGENTS.md removed" in persona.describe_migration(result)
    assert "AGENTS.md removed" not in persona.describe_migration(result).replace("SUBAGENTS.md removed", "")
    path.write_text("You are a subagent of eng-001. Only ever touch src/importer/**.\n")
    assert persona.migrate(instance, "eng-001") is None
    assert path.is_file(), "written for this id on purpose: kept and used"
    assert persona.render_subagents(instance, "eng-001") == (
        "config/eng-001/SUBAGENTS.md", "You are a subagent of eng-001. Only ever touch src/importer/**.")
    rows = [row for row in doctor.run_checks(instance) if row[1] == "subagents:eng-001"]
    assert rows and rows[0][0] == "warn"


def test_a_text_proposal_is_accepted_only_with_the_reviewers_signal_and_check(instance):
    _legacy(instance, "", "\nTwo lessons: run make fixtures; never edit tests/data by hand.\n")
    proposal_id = persona.migrate(instance, "eng-001")["proposal"]
    with pytest.raises(ValidationError):
        learn.accept(instance, proposal_id, env=PARTNER_ENV)
    with pytest.raises(ValidationError):
        learn.accept(instance, proposal_id, signal="fixtures stale", check="make fixtures",
                     env={"HARNESS_ID": "eng-001"})
    first = learn.accept(instance, proposal_id, signal="fixtures are stale", check="run make fixtures first",
                         category="build", keep=True, env=PARTNER_ENV)
    assert first["rule"] == "L1"
    assert learn.load_proposal(instance / "LEARNINGS", proposal_id)["rules"] == ["L1"]
    learn.accept(instance, proposal_id, signal="tests/data edited by hand", check="regenerate, never edit",
                 category="testing", env=PARTNER_ENV)
    assert learn.list_proposals(instance / "LEARNINGS") == []
    assert [rule["key"] for rule in learn.read_all_rules(instance)] == ["L1", "L2"]


def test_launch_migrates_a_legacy_file(instance, hx, launched):
    _set(instance, purpose="{{purpose}}")
    _legacy(instance, "You own the importer.\n\n", "\nMemory worth keeping.\n")
    launched("eng-001")
    assert _harness(instance)["purpose"] == "You own the importer."
    assert "You are eng-001 in pod engineers. You own the importer." in _text(instance)
    assert len(learn.list_proposals(instance / "LEARNINGS")) == 1


def test_install_migrates_every_worker(instance):
    from hx.install import main as install_main
    _set(instance, purpose="{{purpose}}")
    _legacy(instance, "You own the importer.\n\n")
    assert install_main(["--skeleton-only", "--root", str(instance)], env=clean_env(HOME=str(instance.parent / "fake-home"))) == 0
    assert _harness(instance)["purpose"] == "You own the importer."
    assert not (instance / "config" / "eng-001" / "AGENTS.md").exists()
