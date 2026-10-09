from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import compile as compiler, prompt_compiler as prompts
from hx.errors import ValidationError

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
    assert "Things I learned" not in rendered and "Memory episodes" not in rendered
    assert "Read a file once" not in rendered and "hx memory search" not in rendered
    assert "one observable behavior end to end" in rendered
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


@pytest.mark.parametrize("flavor", ["claude", "codex"])
def test_partner_is_compiled_from_its_audience_and_role_instead_of_exempted(instance, flavor):
    configure(instance, item_id="partner", flavor=flavor, role="partner")
    result = prompts.build(instance, "partner")
    rendered = text(result)
    assert "Real dependencies remain\ndependencies" in rendered
    assert "simulate it" not in rendered and "No blockers by construction" not in rendered
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


def test_operator_policy_is_preserved_and_generated_memory_is_not_read(instance, monkeypatch):
    configure(instance)
    agent = instance / "config" / "eng-001" / "AGENTS.md"
    role = (instance / "personas" / "backend-engineer" / "AGENTS.md").read_text().split(compiler.HEADER)[0].strip("\n")
    policy = "Do not publish unless integration and approval are both recorded."
    agent.write_text(role + "\n\n" + policy + "\n\n" + compiler.HEADER + "\n" + "OLD PERSONAL STATE " * 100000)
    global_path = instance / "config" / "CLAUDE.md"
    global_path.write_text(global_path.read_text() + "\n\nPreserve the customer's deployment region exactly.\n")
    result = prompts.build(instance, "eng-001")
    rendered = text(result)
    assert rendered.count(policy) == 1
    assert "Preserve the customer's deployment region exactly." in rendered
    assert "OLD PERSONAL STATE" not in rendered
    assert "You are one Claude" not in rendered
    assert result["migration_review"] == []


def test_unfamiliar_legacy_policy_is_preserved_and_cannot_silently_activate(instance):
    configure(instance, flavor="codex")
    path = instance / "config" / "CLAUDE.md"
    path.write_text(path.read_text().replace("Run `hx task`", "Require approval before publication. Run `hx task`"))
    result = prompts.build(instance, "eng-001")
    assert "Require approval before publication." in text(result)
    assert result["migration_review"]
    with pytest.raises(ValidationError, match="migration review"):
        prompts.context_instructions(instance, "eng-001", Path(result["manifest_path"]), workdir=result["identity"]["workdir"])


def test_native_compiler_expands_identity_deduplicates_copied_role_and_keeps_custom_policy(instance):
    configure(instance)
    agent = instance / "config" / "eng-001" / "AGENTS.md"
    role = (instance / "personas" / "backend-engineer" / "AGENTS.md").read_text().split(compiler.HEADER)[0].strip("\n")
    expanded = compiler.expand_identity(role, compiler.identity_values(instance, "eng-001"))
    policy = "Only the Partner can authorize changes to the production schema."
    agent.write_text(expanded + "\n\n" + policy + "\n\n" + compiler.HEADER + "\nCurrent legacy memory.\n")
    compiler.compile_agent(instance, "eng-001")
    compiled = agent.read_text()
    assert "{{" not in compiled and compiled.count("# eng-001: Backend Engineer") == 1
    assert compiled.count(policy) == 1
    assert "Current legacy memory." in compiled  # Active legacy state migrates with P12.
    assert compiler.compile_agent(instance, "eng-001")["compiled"] is False


def test_exact_block_deduplication_does_not_edit_policy_sentences():
    copied = "Canonical role instruction."
    custom = "Do not replace the phrase Canonical role instruction. inside this policy."
    assert compiler.strip_known_blocks(copied + "\n\n" + custom, [copied]) == custom


def test_copied_previous_role_is_removed_when_canonical_role_changes(instance):
    role_dir = instance / "personas" / "engineer"
    role_dir.mkdir(exist_ok=True)
    before = "Follow the original generated role instructions."
    after = "Follow the current generated role instructions."
    role_file = role_dir / "AGENTS.md"
    role_file.write_text(after + "\n\n" + compiler.HEADER + "\n")
    agent = instance / "config" / "eng-001" / "AGENTS.md"
    old = compiler.render("Old globals.", "engineer", before, before + "\n\nKeep the operator's deployment target.")
    agent.write_text(old + compiler.HEADER + "\nLegacy current state.\n")
    compiler.compile_agent(instance, "eng-001")
    assert before not in agent.read_text()
    assert agent.read_text().count(after) == 1
    assert "Keep the operator's deployment target." in agent.read_text()


def test_unknown_identity_variable_fails_before_a_prompt_is_delivered(instance):
    configure(instance)
    path = instance / "continuity" / "policy.md"
    path.write_text("Deploy only to {{missing_target}}.")
    with pytest.raises(ValidationError, match="missing_target"):
        prompts.build(instance, "eng-001")


def test_operator_policy_is_not_mistaken_for_a_personal_memory_region(instance):
    configure(instance)
    policy = instance / "continuity" / "policy.md"
    policy.write_text("Operator policy.\n" + compiler.HEADER + "\nDo not publish before integration passes.\n")
    assert "Do not publish before integration passes." in text(prompts.build(instance, "eng-001"))


def test_subagent_keeps_actual_workdir_and_parent_commit_control(instance):
    configure(instance, flavor="pi")
    result = prompts.build(instance, "eng-001", audience="subagent")
    rendered = text(result)
    assert "parent coordinates shared writes\nand commits" in rendered
    assert "hx plan finish" not in rendered and "agent/{{id}}" not in rendered
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


def test_compile_planned_cli_returns_a_manifest_and_enforces_identity(instance, run_hx):
    configure(instance, flavor="meta", role="qa-engineer")
    result = run_hx("compile", "eng-001", "--planned", "--root", str(instance))
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert Path(manifest["manifest_path"]).is_file()
    assert manifest["identity"]["role"] == "qa-engineer"
    with pytest.raises(ValidationError, match="audience"):
        prompts.build(instance, "eng-001", audience="partner")


def test_qa_role_and_companion_are_valid_installed_configuration(instance):
    from hx.config_harness import load_harness
    configure(instance, role="qa-engineer")
    config = load_harness(instance / "config" / "eng-001" / "harness.json")
    assert config.role == "qa-engineer"
    assert (instance / "personas" / "qa-engineer" / "AGENTS.md").is_file()
    assert (instance / "companion" / "roles" / "qa-engineer.md").is_file()
