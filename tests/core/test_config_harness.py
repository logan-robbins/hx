"""`config/<id>/harness.json` validator (spec 05), including its four cross-file rules."""

from __future__ import annotations

import json

import pytest

from hx.config_harness import load_harness, validate_harness
from hx.errors import ValidationError

WORKER = {
    "id": "eng-001",
    "pod": "engineers",
    "role": "engineer",
    "model": "claude-opus-5",
    "effort": "xhigh",
    "workdir": "/work/wt/eng-001",
    "harness": {"args": []},
    "companion": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "batch_records": 20,
        "cache_ttl": "1h",
        "state_budget_tokens": 10000,
        "seam_min_context_tokens": 60000,
        "seam_min_interval_s": 600,
    },
}
PARTNER = {
    "id": "partner",
    "pod": "partner",
    "role": "partner",
    "model": "claude-opus-5",
    "effort": "xhigh",
}


def malformed():
    return {
        "not an object": [],
        "missing id": {k: v for k, v in WORKER.items() if k != "id"},
        "missing pod": {k: v for k, v in WORKER.items() if k != "pod"},
        "missing role": {k: v for k, v in WORKER.items() if k != "role"},
        "missing model": {k: v for k, v in WORKER.items() if k != "model"},
        "missing effort": {k: v for k, v in WORKER.items() if k != "effort"},
        "id not an id": {**WORKER, "id": "Engineer1"},
        "id does not match the directory": {**WORKER, "id": "eng-002"},
        "unknown effort": {**WORKER, "effort": "ludicrous"},
        "empty pod": {**WORKER, "pod": ""},
        "worker without workdir": {k: v for k, v in WORKER.items() if k != "workdir"},
        "`branch` is not a field": {**WORKER, "branch": "agent/eng-001"},
        "unknown field": {**WORKER, "provider": "anthropic"},
        "harness args not strings": {**WORKER, "harness": {"args": [1]}},
        "companion budget not an integer": {**WORKER, "companion": {"state_budget_tokens": "big"}},
        "companion negative interval": {**WORKER, "companion": {"seam_min_interval_s": -1}},
        "companion unknown provider": {**WORKER, "companion": {"provider": "openai"}},
        "unknown companion field": {**WORKER, "companion": {"temperature": 1}},
    }


def test_good_worker():
    config = validate_harness(WORKER, "config/eng-001/harness.json", dir_name="eng-001")
    assert config.role == "engineer" and not config.is_partner


def test_good_partner():
    config = validate_harness(PARTNER, "config/partner/harness.json", dir_name="partner")
    assert config.is_partner and config.workdir is None
    assert not hasattr(config, "branch"), "hx manages no git (spec 14 D25)"


@pytest.mark.parametrize("name", sorted(malformed()))
def test_every_malformed_fixture_is_rejected_by_name(name, tmp_path):
    path = tmp_path / "eng-001" / "harness.json"
    path.parent.mkdir()
    path.write_text(json.dumps(malformed()[name]))
    with pytest.raises(ValidationError) as exc:
        load_harness(path, check_cross_file=False)
    assert str(path) in str(exc.value)


def test_the_partner_has_no_workdir(tmp_path):
    path = tmp_path / "partner" / "harness.json"
    path.parent.mkdir()
    path.write_text(json.dumps({**PARTNER, "workdir": "/work/partner"}))
    with pytest.raises(ValidationError) as exc:
        load_harness(path, check_cross_file=False)
    assert "workdir" in str(exc.value)


@pytest.mark.parametrize("field,value", [("pod", "engineers"), ("role", "engineer")])
def test_the_partner_pod_and_role_are_fixed(field, value, tmp_path):
    path = tmp_path / "partner" / "harness.json"
    path.parent.mkdir()
    path.write_text(json.dumps({**PARTNER, field: value}))
    with pytest.raises(ValidationError) as exc:
        load_harness(path, check_cross_file=False)
    assert "Partner" in str(exc.value)


def test_model_must_exist_in_models_json(instance):
    path = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(path.read_text())
    body["model"] = "claude-opus-4"
    path.write_text(json.dumps(body))
    with pytest.raises(ValidationError) as exc:
        load_harness(path, root=instance)
    assert "models.json" in str(exc.value)


def test_role_must_have_a_companion_role_file(instance):
    (instance / "companion" / "roles" / "engineer.md").unlink()
    with pytest.raises(ValidationError) as exc:
        load_harness(instance / "config" / "eng-001" / "harness.json", root=instance)
    assert "companion/roles/engineer.md" in str(exc.value)


def test_workdir_must_exist(instance):
    import shutil

    shutil.rmtree(instance / "wt" / "eng-001")
    with pytest.raises(ValidationError) as exc:
        load_harness(instance / "config" / "eng-001" / "harness.json", root=instance)
    assert "workdir" in str(exc.value)


def test_zero_is_a_valid_seam_interval(tmp_path):
    """Zero means no minimum interval between seams, which is a real setting (spec 05)."""
    path = tmp_path / "eng-001" / "harness.json"
    path.parent.mkdir()
    path.write_text(json.dumps({**WORKER, "companion": {"seam_min_interval_s": 0}}))
    assert load_harness(path, check_cross_file=False).companion["seam_min_interval_s"] == 0


@pytest.mark.parametrize("provider", ["claude-cli", "anthropic"])
def test_the_two_providers_of_spec_05(provider, tmp_path):
    path = tmp_path / "eng-001" / "harness.json"
    path.parent.mkdir()
    path.write_text(json.dumps({**WORKER, "companion": {"provider": provider}}))
    assert load_harness(path, check_cross_file=False).companion["provider"] == provider


def test_a_relative_workdir_resolves_against_the_root(instance):
    """The skeleton ships `"workdir": "wt/eng-001"` (CONTRACTS.md, handoff/gtm-to-build.md)."""
    path = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(path.read_text())
    body["workdir"] = "wt/eng-001"
    path.write_text(json.dumps(body))
    assert load_harness(path, root=instance).workdir == "wt/eng-001"

    body["workdir"] = "wt/eng-404"
    path.write_text(json.dumps(body))
    with pytest.raises(ValidationError) as exc:
        load_harness(path, root=instance)
    assert str(instance / "wt" / "eng-404") in str(exc.value)


def test_an_absolute_workdir_still_stands(instance, tmp_path):
    """Spec 05's own example is `"/work/wt/eng-001"`."""
    elsewhere = tmp_path / "work" / "wt" / "eng-001"
    elsewhere.mkdir(parents=True)
    path = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(path.read_text())
    body["workdir"] = str(elsewhere)
    path.write_text(json.dumps(body))
    assert load_harness(path, root=instance).workdir == str(elsewhere)


def test_the_skeleton_harness_files_validate():
    """The gtm lane authors these; a break here is reported, not fixed (ORCHESTRATION.md)."""
    from hx.install import skeleton_dir

    partner = skeleton_dir() / "config" / "partner" / "harness.json"
    assert load_harness(partner, check_cross_file=False).id == "partner"
    # `templates/worker/harness.json` is a template the Partner copies and fills in, so it
    # carries placeholders rather than an id and is not a config file yet (CONTRACTS.md).
    worker = skeleton_dir() / "templates" / "worker" / "harness.json"
    if worker.exists():
        assert "workdir" in worker.read_text()
