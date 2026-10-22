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
        "model": "claude-haiku-4-5-20251001",
        "batch_records": 20,
        "state_budget_tokens": 10000,
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
        "unknown field": {**WORKER, "nickname": "eng"},
        "harness args not strings": {**WORKER, "harness": {"args": [1]}},
        "unknown flavor": {**WORKER, "flavor": "fleet"},
        "companion budget not an integer": {**WORKER, "companion": {"state_budget_tokens": "big"}},
        "unknown companion field": {**WORKER, "companion": {"temperature": 1}},
        "companion disabled not a boolean": {**WORKER, "companion": {"disabled": "yes"}},
        "companion bad effort": {**WORKER, "companion": {"effort": "ludicrous"}},
        "companion empty effort": {**WORKER, "companion": {"effort": ""}},
        "explorer not an object": {**WORKER, "explorer": "claude-sonnet-5"},
        "unknown explorer field": {**WORKER, "explorer": {"model": "claude-sonnet-5",
                                                          "effort": "high", "budget": 1}},
        "explorer missing effort": {**WORKER, "explorer": {"model": "claude-sonnet-5"}},
        "explorer empty model": {**WORKER, "explorer": {"model": " ", "effort": "high"}},
        "explorer bad effort": {**WORKER, "explorer": {"model": "claude-sonnet-5",
                                                       "effort": "ludicrous"}},
    }


def test_good_worker():
    config = validate_harness(WORKER, "config/eng-001/harness.json", dir_name="eng-001")
    assert config.role == "engineer" and not config.is_partner
    assert config.flavor == "claude"


def test_companion_effort_overrides_the_agents():
    """The Companion extracts step state at its own effort, not the agent's."""
    config = validate_harness(
        {**WORKER, "companion": {**WORKER["companion"], "effort": "low"}},
        "config/eng-001/harness.json",
        dir_name="eng-001",
    )
    assert config.companion["effort"] == "low"
    absent = validate_harness(WORKER, "config/eng-001/harness.json", dir_name="eng-001")
    assert "effort" not in absent.companion, "unset means the agent's effort"


def test_flavor_pi_is_accepted():
    config = validate_harness({**WORKER, "flavor": "pi"}, "config/eng-001/harness.json", dir_name="eng-001")
    assert config.flavor == "pi"


def test_the_partner_cannot_be_pi():
    with pytest.raises(ValidationError) as exc:
        validate_harness({**PARTNER, "flavor": "pi"}, "config/partner/harness.json", dir_name="partner")
    assert "claude or codex" in str(exc.value)


def test_flavor_grok_is_accepted():
    config = validate_harness({**WORKER, "flavor": "grok"}, "config/eng-001/harness.json", dir_name="eng-001")
    assert config.flavor == "grok"


def test_the_partner_cannot_be_grok():
    with pytest.raises(ValidationError) as exc:
        validate_harness({**PARTNER, "flavor": "grok"}, "config/partner/harness.json", dir_name="partner")
    assert "claude or codex" in str(exc.value)


def test_companion_disabled_is_accepted():
    """`companion.disabled` runs the agent with no Companion window at all (spec 05)."""
    for value in (True, False):
        config = validate_harness(
            {**WORKER, "companion": {**WORKER["companion"], "disabled": value}},
            "config/eng-001/harness.json",
            dir_name="eng-001",
        )
        assert config.companion["disabled"] is value


def test_companion_disabled_must_be_a_boolean():
    for bad in ("yes", 1, 0, None):
        with pytest.raises(ValidationError) as exc:
            validate_harness(
                {**WORKER, "companion": {**WORKER["companion"], "disabled": bad}},
                "config/eng-001/harness.json",
                dir_name="eng-001",
            )
        assert "`companion.disabled` must be a boolean" in str(exc.value)


def test_flavor_meta_is_accepted():
    config = validate_harness({**WORKER, "flavor": "meta"}, "config/eng-001/harness.json", dir_name="eng-001")
    assert config.flavor == "meta"


def test_the_partner_cannot_be_meta():
    with pytest.raises(ValidationError) as exc:
        validate_harness({**PARTNER, "flavor": "meta"}, "config/partner/harness.json", dir_name="partner")
    assert "claude or codex" in str(exc.value)


def test_flavor_codex_is_accepted():
    config = validate_harness({**WORKER, "flavor": "codex"}, "config/eng-001/harness.json", dir_name="eng-001")
    assert config.flavor == "codex"


def test_the_partner_can_be_codex():
    """The Partner runs on Claude or Codex (spec 12)."""
    config = validate_harness({**PARTNER, "flavor": "codex"}, "config/partner/harness.json", dir_name="partner")
    assert config.flavor == "codex" and config.is_partner


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


def test_an_explorer_block_is_accepted_and_kept():
    """What the orientation subagent runs on: optional, and both keys when present."""
    explorer = {"model": "claude-sonnet-5", "effort": "high"}
    config = validate_harness({**WORKER, "explorer": explorer}, "config/eng-001/harness.json",
                              dir_name="eng-001")
    assert config.explorer == explorer
    assert validate_harness(WORKER, "x", dir_name="eng-001").explorer == {}


def test_the_explorer_model_must_exist_in_models_json(instance):
    path = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(path.read_text())
    body["explorer"] = {"model": "claude-sonnet-5", "effort": "high"}
    path.write_text(json.dumps(body))
    assert load_harness(path, root=instance).explorer["model"] == "claude-sonnet-5"
    body["explorer"]["model"] = "claude-sonnet-4"
    path.write_text(json.dumps(body))
    with pytest.raises(ValidationError) as exc:
        load_harness(path, root=instance)
    assert "`explorer.model`" in str(exc.value) and "models.json" in str(exc.value)


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


@pytest.mark.parametrize('field',['memory_episode_chars','memory_half_life_h','memory_inject_k'])
def test_episode_configuration_is_not_a_runtime_option(field):
    with pytest.raises(ValidationError,match='unknown.*companion'):
        validate_harness({**WORKER,'companion':{field:1}},'config/eng-001/harness.json',dir_name='eng-001')
