from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import instruction_materials, learn, persona, prompt_compiler as prompts
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


def startup_text(bundle):
    return Path(bundle["system_path"]).read_text() + Path(bundle["context_path"]).read_text()


def text(bundle):
    return startup_text(bundle) + instruction_materials.read_bundle([ref for ref in bundle["required_files"] if ref.get("delivery") != "inline"]).decode()


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
    assert "Every directive follows the dependency graph" in rendered
    assert "Maximize useful parallelism for every directive" in rendered
    assert "Search" in rendered or "search" in rendered
    assert "Apparent task\nsize does not determine packet count" in rendered
    persona_text = (instance / "personas" / "partner.md").read_text()
    assert prompts.DOMAIN_RE.search(persona_text)["body"].strip("\n") in rendered
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
    assert "## Domain judgment" in live and "## Domain judgment" not in planned
    assert "Use `hx-worker` for exact mechanics." in live
    assert "Use `hx-worker` for exact mechanics." not in planned
    subagent = text(prompts.build(instance, "eng-001", audience="subagent"))
    assert "Prefer the importer's own retry helper." in subagent
    assert "Owns the importer service" not in subagent, "a subagent's scope is its bounded question"


def test_prompt_sources_exclude_per_id_and_global_invariants_and_bound_whole_role_source(instance):
    configure(instance, flavor="codex")
    (instance / "config" / "eng-001" / "AGENTS.md").write_text("OLD PER-ID PERSONA\n")
    clauses = instance / "config" / "CLAUDE.md"
    before = prompts.build(instance, "eng-001")
    clauses.write_text(clauses.read_text() + "\n\nPreserve the customer's deployment region exactly.\n")
    after = prompts.build(instance, "eng-001")
    assert after["version"] == before["version"], "config/CLAUDE.md reaches agents through its own channel"
    assert "OLD PER-ID PERSONA" not in text(after)
    assert "Preserve service, data, and interface invariants." in text(after)
    assert "migration_review" not in after
    source = instance / "personas" / "backend-engineer.md"
    source.write_text(source.read_text() + "\n## Operator policy details\n" + "OVERSIZED ROLE SOURCE " * 100000)
    with pytest.raises(ValidationError, match="256 KiB"):
        prompts.build(instance, "eng-001")


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
    policy.write_text("Operator policy.\n" + "## Operator policy details" + "\nDo not publish before integration passes.\n")
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
    assert rendered == startup_text(bundle) and version == bundle["version"]
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


@pytest.mark.parametrize("audience", ["worker", "partner", "subagent"])
def test_compiled_instruction_sources_carry_universal_graph_search_and_parallel_contract(instance, audience):
    item_id = "partner" if audience == "partner" else "eng-001"
    configure(instance, item_id=item_id, role="partner" if audience == "partner" else "backend-engineer")
    if audience == "partner":
        (instance / "personas" / "partner.md").write_text((prompts.BUNDLE / "personas" / "partner.md").read_text())
    rendered = text(prompts.build(instance, item_id, audience=audience)).lower()
    for obligation in ("every directive", "graph", "search", "reuse", "parallel", "packet count"):
        assert obligation in rendered, (audience, obligation)
    assert "simple path" not in rendered and "goal files" not in rendered


def test_total_static_prompt_bound_remains_enforced(instance):
    configure(instance)
    common = instance / "continuity" / "prompts" / "common.md"
    audience = instance / "continuity" / "prompts" / "audiences" / "worker.md"
    role = instance / "personas" / "backend-engineer.md"
    common.write_text("Common " + "c" * 65000)
    audience.write_text("Audience " + "a" * 65000)
    role.write_text("## Domain judgment\n" + "d" * 65000)
    policy = instance / "continuity" / "policy.md"
    policy.write_text("Policy " + "p" * 65000)
    extra = instance / "continuity" / "policy" / "worker.md"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("Extra policy " + "e" * 65000)
    with pytest.raises(ValidationError, match="256 KiB"):
        prompts.build(instance, "eng-001")


@pytest.mark.parametrize("flavor", FLAVORS)
def test_long_warranted_policy_is_referenced_in_full_not_truncated_in_startup(instance, flavor):
    import hashlib
    configure(instance, flavor=flavor)
    policy = instance / "continuity" / "policy.md"
    full_policy = "Preserve the exact release constraints.\n" + "Full warranted condition.\n" * 1000 + "FINAL MANDATORY CONDITION: retain the original provider."
    policy.write_text(full_policy)
    bundle = prompts.build(instance, "eng-001")
    initial = startup_text(bundle)
    assert full_policy in initial
    assert all(ref["delivery"] == "inline" for ref in bundle["required_files"])
    assert "Required file:" not in initial and "continue until" not in initial
    assert '\"id\":\"eng-001\"' in initial and '\"workdir\":' in initial
    material, = bundle["required_files"]
    exact = Path(material["path"]).read_bytes()
    assert full_policy.encode() in exact
    assert hashlib.sha256(exact).hexdigest() == material["sha256"]
    assert len(exact) == material["bytes"] > 20000
    verified = prompts.verify_material(instance, "eng-001", Path(bundle["manifest_path"]), Path(material["path"]), workdir=bundle["identity"]["workdir"])
    assert verified == material
    assert prompts.verify_material(instance, "eng-001", Path(bundle["manifest_path"]), policy, workdir=bundle["identity"]["workdir"]) is None


def test_required_full_instruction_tamper_blocks_bundle_and_rebuild(instance):
    configure(instance, flavor="codex")
    bundle = prompts.build(instance, "eng-001")
    material, = bundle["required_files"]
    Path(material["path"]).write_text("A substituted excerpt must not become the full policy.")
    for operation in (lambda: prompts.verified_bundle(instance, "eng-001", Path(bundle["manifest_path"]), workdir=bundle["identity"]["workdir"]),
                      lambda: prompts.build(instance, "eng-001")):
        with pytest.raises(ValidationError, match="required full instruction file.*altered"):
            operation()


def test_companion_keeps_full_inline_policy_when_its_evidence_tools_cannot_read_files(instance):
    configure(instance)
    bundle = prompts.build(instance, "eng-001", audience="companion")
    assert bundle["required_files"] == []
    assert "frozen range" in startup_text(bundle)
    assert "structured result channel" in startup_text(bundle)


@pytest.mark.parametrize("flavor,role", [(flavor, role) for role in ROLES for flavor in FLAVORS] + [(flavor, "partner") for flavor in ("claude", "codex")])
@pytest.mark.parametrize("audience", ["executor", "subagent", "companion", "learning"])
def test_reviewed_global_and_exact_role_learnings_reach_every_native_audience(instance, flavor, role, audience):
    import hashlib
    item_id = "partner" if role == "partner" else "eng-001"
    configure(instance, flavor=flavor, role=role, item_id=item_id)
    author = {"HARNESS_ID": "eng-002"}
    reviewer = {"HARNESS_ID": "partner"}
    global_proposal = learn.propose(instance, signal="GLOBAL observation", check="Never discard scope or negation.",
        evidence="verified global evidence", category="evidence", env=author)
    learn.accept(instance, global_proposal["id"], env=reviewer)
    role_proposal = learn.propose(instance, signal=f"ROLE {role} observation", check="Do not deploy before integration passes.",
        evidence="verified role evidence", category="delivery", role=role, env=author)
    learn.accept(instance, role_proposal["id"], env=reviewer)
    foreign_role = "qa-engineer" if role != "qa-engineer" else "backend-engineer"
    foreign = learn.propose(instance, signal="FOREIGN ROLE observation", check="Keep only foreign context.",
        evidence="foreign evidence", category="domain", role=foreign_role, env=author)
    learn.accept(instance, foreign["id"], env=reviewer)
    learn.propose(instance, signal="PENDING PROPOSAL IS NOT POLICY", check="Do not execute unreviewed candidates.",
        evidence="unreviewed evidence", category="runtime", env=author)
    compiled = prompts.build(instance, item_id, audience=None if audience == "executor" else audience)
    rendered = text(compiled)
    assert "GLOBAL observation" in rendered and f"ROLE {role} observation" in rendered
    assert "Never discard scope or negation." in rendered
    assert "Do not deploy before integration passes." in rendered
    assert "FOREIGN ROLE observation" not in rendered and "PENDING PROPOSAL IS NOT POLICY" not in rendered
    sources = [source for source in compiled["sources"] if source.get("kind") == "reviewed-learning"]
    assert {source["scope"] for source in sources} == {"global", role}
    for source in sources:
        exact = Path(source["path"]).read_bytes()
        assert source["source_hash"] == hashlib.sha256(exact).hexdigest()
        assert source["bytes"] == len(exact)
        assert exact.decode() in rendered


def test_reviewed_rule_revision_invalidates_prepared_bundle(instance):
    configure(instance)
    proposal = learn.propose(instance, signal="A release proof is stale", check="Compare the exact tested artifact.",
        evidence="observed run", category="delivery", role="backend-engineer", env={"HARNESS_ID": "eng-001"})
    learn.accept(instance, proposal["id"], env={"HARNESS_ID": "partner"})
    old = prompts.build(instance, "eng-001")
    source = learn.rule_files(instance, role="backend-engineer")[0]
    source.write_text(source.read_text() + "\nDo not omit the final reviewed condition.\n")
    assert prompts.build(instance, "eng-001")["version"] != old["version"]
    with pytest.raises(ValidationError, match="sources, identity"):
        prompts.verified_bundle(instance, "eng-001", Path(old["manifest_path"]), workdir=old["identity"]["workdir"])


def test_long_multibyte_policy_has_bounded_linked_parts_and_one_exact_bundle(instance):
    import hashlib
    configure(instance)
    policy = "Must preserve every constraint.\n" + "不可省略否定条件。" * 6000 + "\nFINAL condition: do not rebuild the existing helper.\n"
    (instance / "continuity" / "policy.md").write_text(policy)
    bundle = prompts.build(instance, "eng-001")
    refs = bundle["required_files"]
    assert len(refs) > 1
    complete = instruction_materials.read_bundle(refs)
    assert policy.strip("\n").encode() in complete
    assert hashlib.sha256(complete).hexdigest() == refs[0]["bundle_sha256"]
    assert len(complete) == refs[0]["bundle_bytes"]
    for index, ref in enumerate(refs):
        data = Path(ref["path"]).read_bytes()
        assert len(data) <= 40000 and data.decode()
        assert ref["part"] == index + 1 and ref["parts"] == len(refs)
        # Each footer names its place and the part to read next; every part is its own complete read.
        continuation = f"Read {ref['next_path']} next" if ref["next_path"] else "END OF COMPLETE MATERIAL"
        assert f"{continuation}; part {index + 1} of {len(refs)}" in data.decode()
        assert prompts.verify_material(instance, "eng-001", Path(bundle["manifest_path"]), Path(ref["path"]),
            workdir=bundle["identity"]["workdir"]) == ref
    Path(refs[-1]["path"]).write_text("Missing final obligation")
    with pytest.raises(ValidationError, match="altered"):
        instruction_materials.read_bundle(refs)


def test_learning_companion_has_assessment_authority_separate_from_context_companion(instance):
    configure(instance)
    compiled = prompts.build(instance, "eng-001", audience="learning")
    rendered = text(compiled)
    assert compiled["identity"]["audience"] == "learning"
    assert compiled["identity"]["runtime"] == "claude"
    assert compiled["required_files"] == []
    assert "structured assessment" in rendered and "independent accept, merge, or reject" in rendered
    assert "Never patch" in rendered
    assert "submit_patch" not in rendered and "expected versions" not in rendered


CHAT_RULE = "Chat is a machine channel:"
PARTNER_LINE = "Your replies to the human are the one place where a sentence is the deliverable."


@pytest.mark.parametrize("audience", ["worker", "subagent", "partner", "companion", "learning"])
def test_every_delivered_bundle_carries_the_chat_channel_rule_exactly_once(instance, audience):
    item_id = "partner" if audience == "partner" else "eng-001"
    configure(instance, item_id=item_id, role="partner" if audience == "partner" else "backend-engineer")
    (instance / "personas" / "partner.md").write_text((prompts.BUNDLE / "personas" / "partner.md").read_text())
    bundle = prompts.build(instance, item_id, audience=audience)
    _, rendered = prompts.verified_bundle(instance, item_id, Path(bundle["manifest_path"]),
                                          workdir=bundle["identity"]["workdir"], audience=audience)
    delivered = rendered["system"] + rendered["context"]
    assert delivered.count(CHAT_RULE) == 1
    assert delivered.count("a decision the work needs is a typed `hx progress` blocker.") == 1
    assert delivered.count("compaction state are\ncomplete statements that keep subjects, negation, conditions") == 1
    assert delivered.count(PARTNER_LINE) == int(audience == "partner")


def test_live_partner_boundary_carries_its_reply_line_once_without_the_worker_chat_rule(instance):
    (instance / "personas" / "partner.md").write_text((prompts.BUNDLE / "personas" / "partner.md").read_text())
    text = persona.boundary(instance, "partner", env={})["text"]
    assert text.count(PARTNER_LINE) == 1
    assert "Ask a question only\nwhen the human must decide." in text
    assert CHAT_RULE not in text
