"""Validator and parser for `config/models.json` (spec 05).

One row per model: `window` is the model's context window and `threshold` is hx's seam
threshold, the point at which the `log` hook marks a seam. 1M-window models are capped at
500000. The threshold is never passed to Claude Code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError

#: Spec 05: "1M-window models are capped at 500000."
LARGE_WINDOW = 1_000_000
LARGE_WINDOW_THRESHOLD_CAP = 500_000

_FIELDS = ("window", "threshold")


@dataclass(frozen=True)
class Model:
    id: str
    window: int
    threshold: int


def validate_models(data: object, path: str | Path) -> dict[str, Model]:
    """Validate a parsed `models.json` body. Every message names the file and the rule."""
    path = str(path)
    if not isinstance(data, dict):
        raise ValidationError(
            f"{path}: must be a JSON object mapping model id to "
            f'{{"window": int, "threshold": int}}, got {type(data).__name__}'
        )
    if not data:
        raise ValidationError(f"{path}: must list at least one model")

    models: dict[str, Model] = {}
    for model_id, row in data.items():
        if not isinstance(model_id, str) or model_id.strip() == "":
            raise ValidationError(f"{path}: model id must be a non-empty string, got `{model_id!r}`")
        if model_id != model_id.strip():
            raise ValidationError(f"{path}: model `{model_id!r}`: id has leading or trailing whitespace")
        if not isinstance(row, dict):
            raise ValidationError(
                f"{path}: model `{model_id}`: value must be an object with "
                f"`window` and `threshold`, got {type(row).__name__}"
            )
        unknown = sorted(set(row) - set(_FIELDS))
        if unknown:
            raise ValidationError(
                f"{path}: model `{model_id}`: unknown field(s) {', '.join(unknown)}; "
                f"only {', '.join(_FIELDS)} are allowed"
            )
        values: dict[str, int] = {}
        for field in _FIELDS:
            if field not in row:
                raise ValidationError(f"{path}: model `{model_id}`: missing `{field}`")
            value = row[field]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(
                    f"{path}: model `{model_id}`: `{field}` must be an integer, "
                    f"got {type(value).__name__} `{value!r}`"
                )
            if value <= 0:
                raise ValidationError(f"{path}: model `{model_id}`: `{field}` must be positive, got {value}")
            values[field] = value
        window, threshold = values["window"], values["threshold"]
        if threshold >= window:
            raise ValidationError(
                f"{path}: model `{model_id}`: `threshold` ({threshold}) must be below "
                f"`window` ({window}); the seam has to happen before the window is full"
            )
        if window >= LARGE_WINDOW and threshold > LARGE_WINDOW_THRESHOLD_CAP:
            raise ValidationError(
                f"{path}: model `{model_id}`: a window of {window} is capped at a threshold of "
                f"{LARGE_WINDOW_THRESHOLD_CAP} (spec 05), got {threshold}"
            )
        models[model_id] = Model(id=model_id, window=window, threshold=threshold)
    return models


def load_models(path: str | Path) -> dict[str, Model]:
    """Read and validate `config/models.json`."""
    path = Path(path)
    if not path.exists():
        raise ValidationError(f"{path}: missing; every instance needs config/models.json (spec 05)")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: not valid JSON: {exc}") from exc
    return validate_models(data, path)
