from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import persona, prompt_compiler as prompts
from hx.errors import ValidationError
from hx.persona import LEGACY_HEADER

FLAVORS = ("claude", "codex", "meta", "grok", "pi")
ROLES = ("backend-engineer", "frontend-engineer", "release-engineer", "qa-engineer")


def configure(instance, *, flavor="claude", role="backend-engineer", item_id="eng-001", workdir=None):
    path = instance / "config" / item_id / "harness.json"
    data = json.loads(path.read_text())
    data.update(flavor=flavor, role=role)
    if workdir:
        data["workdir"] = str(workdir)
    path.write_text(json.dumps(data))
    return data


def text(bundle):
    return Path(bundle["system_path"]).read_text() + Path(bundle["context_path"]).read_text()


@pytest.mark.parametrize("flavor", FLAVORS)
@pytest.mark.parametrize("role", ROLES)
def test_worker_roles_have_resolved_identity_and_one_planned_channel(instance, flavor, role):
    configure(instance, flavor=flavor, role=role)
    result = prompts.build(instance, "eng-001")
    rendered = text(result)
    assert "{{" not in rendered and '"id":"eng-001"' in rendered
    assert rendered.count("Session identity:") == 1
    assert '"runtime":' + json.dumps(flavor) in rendered
    assert "Things I learned" not in rendered and "UPDATES BELOW ONLY" not in rendered
    assert "one observable behavior end to end" in rendered
    persona_text = (instance / "personas" / f"{role}.md").read_text()
    assert prompts.DOMAIN_RE.search(persona_text)["body"].strip("\n") in rendered, \
        "the role persona's domain judgment is the planned prompt's"
    assert "Commit as you go" not in rendered and "tmux" not in rendered, \
        "the live session's mechanics stay with the live session"
    assert "Cannot ask questions" not in rendered
    target = "context" if flavor in {"meta", "codex"} else "system"
    assert {section["channel"] for section in result["sections"]} == {target}
    assert result["delivery"][target]["status"] == "prepared"
    assert result["activation"] == "pending_controller_installation"
    assert not result["permissions_enforced"]


@pytest.mark.parametrize("role", [*ROLES, "partner"])
def test_companions_receive_frozen_pass_policy_and_role_retention_without_worker_mechanics(instance, role):
    item_id = "partner" if role == "partner" else "eng-001"
    configure(instance, role=role, item_id=item_id)
    result = prompts.build(instance, item_id, audience="companion")
    rendered = text(result)
    assert "expected versions" in rendered and "frozen range" in rendered
    assert "Binding\ngoals/constraints require a Partner amendment" in rendered
    assert "hx plan finish" not in rendered and "Commit as you go" not in rendered
    assert "Do not scan\nthe session log" in rendered
    assert "supplied" in rendered and "No model" not in rendered
    assert result["identity"]["runtime"] == "claude"
    role_file = (instance / "companion" / "roles" / f"{role}.md").read_text().strip("\n")
    assert role_file in rendered, "the file the Companion's live system prompt is composed from"
    if role != "partner":  # the fixture replaces the Partner's role file with a stub
        assert "## Retention judgment" in rendered
    assert "You are `eng-001`" not in rendered, "no executor persona in a Companion prompt"


@pytest.mark.parametrize("flavor", ["claude", "codex"])
def test_partner_is_compiled_from_its_audience_and_role_instead_of_exempted(instance, flavor):
    configure(instance, item_id="partner", flavor=flavor, role="partner")
    (instance / "personas" / "partner.md").write_text(
        (prompts.BUNDLE / "personas" / "partner.md").read_text())
    result = prompts.build(instance, "partner")
    rendered = text(result)
    assert "Real dependencies remain\ndependencies" in rendered
    assert "Pass current contracts and relevant findings into assignments" in rendered, \
        "the domain judgment of personas/partner.md"
    assert "No blockers by construction" not in rendered, "its live mechanics stay live"
    assert "runtime's completion and readiness results" in rendered
    assert "hx plan finish" not in rendered


def test_release_and_qa_policies_keep_uncertainty_and_current_proof(instance):
    configure(instance, role="release-engineer")
    release = text(prompts.build(instance, "eng-001"))
    assert "explicitly authorized push or publication is permitted" in release
    assert "Reconcile an\nunknown remote outcome before retrying" in release
    assert "Never run `git push`" not in release
    configure(instance, role="qa-engineer")
    qa = text(prompts.build(instance, "eng-001"))
    assert "Changed inputs invalidate old QA proof" in qa
    assert "selected subset cannot certify broader coverage" in qa


def test_planned_and_live_personas_are_one_source(instance):
    """The planned domain judgment is the live persona's section, and the purpose is the live
    persona's purpose: an edit to either reaches both, and nothing else of the persona is read."""
    data = configure(instance)
    data["purpose"] = "Owns the importer service and its schema."
    (instance / "config" / "eng-001" / "harness.json").write_text(json.dumps(data))
    source = instance / "personas" / "backend-engineer.md"
    source.write_text(source.read_text() + "Prefer the importer's own retry helper.\n")
    planned = text(prompts.build(instance, "eng-001"))
    live = persona.boundary(instance, "eng-001")["text"]
    for line in ("Prefer the importer's own retry helper.", "Owns the importer service and its schema."):
        assert line in planned and line in live
    assert "## How you work" in live and "## How you work" not in planned
    subagent = text(prompts.build(instance, "eng-001", audience="subagent"))
    assert "Prefer the importer's own retry helper." in subagent
    assert "Owns the importer service" not in subagent, "a subagent's scope is its bounded question"


def test_the_planned_prompt_reads_no_per_id_file_no_invariants_and_no_memory(instance):
    configure(instance, flavor="codex")
    (instance / "config" / "eng-001" / "AGENTS.md").write_text("OLD PER-ID PERSONA\n")
    clauses = instance / "config" / "CLAUDE.md"
    before = prompts.build(instance, "eng-001")
    clauses.write_text(clauses.read_text() + "\n\nPreserve the customer's deployment region exactly.\n")
    after = prompts.build(instance, "eng-001")
    assert after["version"] == before["version"], "config/CLAUDE.md reaches agents through its own channel"
    assert "OLD PER-ID PERSONA" not in text(after)
    source = instance / "personas" / "backend-engineer.md"
    source.write_text(source.read_text() + "\n" + LEGACY_HEADER + "\n" + "OLD PERSONAL STATE " * 100000)
    rendered = text(prompts.build(instance, "eng-001"))
    assert "OLD PERSONAL STATE" not in rendered
    assert "Preserve service, data, and interface invariants." in rendered
    assert "migration_review" not in after


def test_a_role_without_a_persona_has_no_domain_section(instance):
    configure(instance, role="engineer")
    result = prompts.build(instance, "eng-001")
    assert "domain" not in [section["id"] for section in result["sections"]]
    assert '"role":"engineer"' in text(result)


def test_unknown_identity_variable_fails_before_a_prompt_is_delivered(instance):
    configure(instance)
    path = instance / "continuity" / "policy.md"
    path.write_text("Deploy only to {{missing_target}}.")
    with pytest.raises(ValidationError, match="missing_target"):
        prompts.build(instance, "eng-001")


def test_operator_policy_is_not_mistaken_for_a_personal_memory_region(instance):
    configure(instance)
    policy = instance / "continuity" / "policy.md"
    policy.write_text("Operator policy.\n" + LEGACY_HEADER + "\nDo not publish before integration passes.\n")
    assert "Do not publish before integration passes." in text(prompts.build(instance, "eng-001"))


def test_subagent_keeps_actual_workdir_and_parent_commit_control(instance):
    configure(instance, flavor="pi")
    result = prompts.build(instance, "eng-001", audience="subagent")
    rendered = text(result)
    assert "parent coordinates shared writes\nand commits" in rendered
    assert "hx plan finish" not in rendered and "agent/{{id}}" not in rendered
    assert "Preserve service, data, and interface invariants." in rendered, "the persona's domain judgment"
    assert "Commit as you go" not in rendered and "You are `eng-001`" not in rendered, \
        "none of the parent's own mechanics"
    assert {section["channel"] for section in result["sections"]} == {"context"}


def test_context_bundle_validates_sources_and_never_claims_system_prefix_loaded(instance):
    configure(instance, flavor="claude")
    system = prompts.build(instance, "eng-001")
    with pytest.raises(ValidationError, match="not acknowledged"):
        prompts.context_instructions(instance, "eng-001", Path(system["manifest_path"]), workdir=system["identity"]["workdir"])
    configure(instance, flavor="codex")
    bundle = prompts.build(instance, "eng-001")
    rendered, version = prompts.context_instructions(instance, "eng-001", Path(bundle["manifest_path"]), workdir=bundle["identity"]["workdir"])
    assert rendered == text(bundle) and version == bundle["version"]
    assert prompts.build(instance, "eng-001")["version"] == version
    policy = instance / "continuity" / "policy.md"
    policy.write_text("Keep the release target unchanged.")
    with pytest.raises(ValidationError, match="sources, identity"):
        prompts.context_instructions(instance, "eng-001", Path(bundle["manifest_path"]), workdir=bundle["identity"]["workdir"])


def test_a_planned_build_returns_a_manifest_and_enforces_identity(instance):
    configure(instance, flavor="meta", role="qa-engineer")
    manifest = prompts.build(instance, "eng-001")
    assert Path(manifest["manifest_path"]).is_file()
    assert manifest["identity"]["role"] == "qa-engineer"
    with pytest.raises(ValidationError, match="audience"):
        prompts.build(instance, "eng-001", audience="partner")


def test_qa_role_and_companion_are_valid_installed_configuration(instance):
    from hx.config_harness import load_harness
    configure(instance, role="qa-engineer")
    config = load_harness(instance / "config" / "eng-001" / "harness.json")
    assert config.role == "qa-engineer"
    assert (instance / "personas" / "qa-engineer.md").is_file()
    assert (instance / "companion" / "roles" / "qa-engineer.md").is_file()
