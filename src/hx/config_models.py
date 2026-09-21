"""Validator and parser for `config/models.json` (spec 05).

One row per model: `window` is the model's context window and `threshold` is hx's seam
threshold, the point at which the `log` hook marks a seam. 1M-window models are capped at
500000. The threshold is never passed to Claude Code.

`autocompact_window` is optional and is the one number here that *is* passed to Claude Code:
`adapters/claude/start.sh` exports it as `CLAUDE_CODE_AUTO_COMPACT_WINDOW` for the agent's
session, which moves native autocompaction down from the model's own window (about 967k on a
1M model) to a number the operator picks. Spec 11 assumed the native window and therefore said
the variable stays unset; running an agent at a 250k autocompact window makes that assumption
false, and hx has to know the number to keep its own seam below it. Hence the rule
`threshold < autocompact_window <= window`: hx seams first, native compaction never runs, and
a row that inverts the two is rejected here rather than discovered as a lost conversation.
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

#: Optional, and validated only when the row carries it.
_OPTIONAL_FIELDS = ("autocompact_window",)


@dataclass(frozen=True)
class Model:
    id: str
    window: int
    threshold: int
    #: `None` when the row omits it: the session then runs on the native autocompact window
    #: and `start.sh` exports nothing.
    autocompact_window: int | None = None


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
        unknown = sorted(set(row) - set(_FIELDS) - set(_OPTIONAL_FIELDS))
        if unknown:
            raise ValidationError(
                f"{path}: model `{model_id}`: unknown field(s) {', '.join(unknown)}; "
                f"only {', '.join(_FIELDS + _OPTIONAL_FIELDS)} are allowed"
            )
        values: dict[str, int] = {}
        for field in _FIELDS + tuple(f for f in _OPTIONAL_FIELDS if f in row):
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
        autocompact = values.get("autocompact_window")
        if autocompact is not None:
            if autocompact <= threshold:
                raise ValidationError(
                    f"{path}: model `{model_id}`: `autocompact_window` ({autocompact}) must be "
                    f"above `threshold` ({threshold}); hx has to take its seam before Claude "
                    f"Code compacts, or the seam never happens"
                )
            if autocompact > window:
                raise ValidationError(
                    f"{path}: model `{model_id}`: `autocompact_window` ({autocompact}) must not "
                    f"exceed `window` ({window})"
                )
        models[model_id] = Model(
            id=model_id, window=window, threshold=threshold, autocompact_window=autocompact
        )
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
