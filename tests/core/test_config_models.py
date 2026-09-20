"""`config/models.json` validator (spec 05).

M0 pass criterion: every malformed fixture is rejected with a message naming the file and
the rule.
"""

from __future__ import annotations

import json

import pytest

from hx.config_models import LARGE_WINDOW_THRESHOLD_CAP, load_models, validate_models
from hx.errors import ValidationError

GOOD = {
    "claude-opus-5": {"window": 1000000, "threshold": 500000},
    "claude-sonnet-5": {"window": 1000000, "threshold": 500000},
}

MALFORMED = {
    "not an object": [],
    "empty": {},
    "row is not an object": {"m": 3},
    "missing window": {"m": {"threshold": 10}},
    "missing threshold": {"m": {"window": 10}},
    "window not an integer": {"m": {"window": "1000000", "threshold": 5}},
    "threshold not an integer": {"m": {"window": 1000, "threshold": 1.5}},
    "boolean window": {"m": {"window": True, "threshold": 1}},
    "zero window": {"m": {"window": 0, "threshold": 1}},
    "negative threshold": {"m": {"window": 100, "threshold": -1}},
    "threshold equals window": {"m": {"window": 100, "threshold": 100}},
    "threshold above window": {"m": {"window": 100, "threshold": 200}},
    "1M window over the cap": {"m": {"window": 1000000, "threshold": 500001}},
    "unknown field": {"m": {"window": 100, "threshold": 10, "effort": "max"}},
    "empty model id": {"": {"window": 100, "threshold": 10}},
    "model id with whitespace": {" m ": {"window": 100, "threshold": 10}},
}


def test_the_skeleton_models_file_validates():
    from hx.install import skeleton_dir

    models = load_models(skeleton_dir() / "config" / "models.json")
    assert "claude-opus-5" in models
    assert models["claude-opus-5"].threshold == LARGE_WINDOW_THRESHOLD_CAP


def test_good_fixture():
    models = validate_models(GOOD, "config/models.json")
    assert set(models) == set(GOOD)
    assert models["claude-opus-5"].window == 1000000


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_every_malformed_fixture_is_rejected_by_name(name, tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(MALFORMED[name]))
    with pytest.raises(ValidationError) as exc:
        load_models(path)
    message = str(exc.value)
    assert str(path) in message, f"{name}: message does not name the file: {message}"
    assert len(message) > len(str(path)) + 5, f"{name}: message states no rule: {message}"


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ValidationError) as exc:
        load_models(tmp_path / "models.json")
    assert "models.json" in str(exc.value)


def test_invalid_json_names_the_file(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{not json")
    with pytest.raises(ValidationError) as exc:
        load_models(path)
    assert str(path) in str(exc.value)
    assert "not valid JSON" in str(exc.value)
