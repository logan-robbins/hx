"""Validator and parser for `config/<id>/harness.json` (spec 05).

Spec 05 names four cross-file rules: `id` equals the directory name; `model` exists in
`models.json`; `role` has a `companion/roles/<role>.md`; `workdir` exists. The Partner has
no `workdir` and no `branch`. Failure is exit 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config_models import Model, load_models
from .errors import ValidationError
from .ids import ID_RE, PARTNER

#: `--effort <level>` at launch, spec 11 ("`low`…`max`").
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

_REQUIRED = ("id", "pod", "role", "model", "effort")
_OPTIONAL = ("workdir", "branch", "harness", "companion")

_COMPANION_INT_FIELDS = (
    "batch_records",
    "state_budget_tokens",
    "seam_min_context_tokens",
    "seam_min_interval_s",
)
_COMPANION_STR_FIELDS = ("provider", "model", "cache_ttl")


def resolve_workdir(workdir: str, root: Path) -> Path:
    """A relative `workdir` is relative to HARNESS_ROOT (CONTRACTS.md); an absolute one stands.

    The skeleton ships `"workdir": "wt/eng-001"`, because `hx install` copies it verbatim into
    whatever root the user chose, so an absolute path baked into the package would be wrong
    everywhere but the machine it was written on (handoff/gtm-to-build.md).
    """
    path = Path(workdir)
    return path if path.is_absolute() else root / path


@dataclass(frozen=True)
class HarnessConfig:
    id: str
    pod: str
    role: str
    model: str
    effort: str
    workdir: str | None = None
    branch: str | None = None
    harness: dict = field(default_factory=dict)
    companion: dict = field(default_factory=dict)

    @property
    def is_partner(self) -> bool:
        return self.id == PARTNER


def _require_str(data: dict, key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or value.strip() == "":
        raise ValidationError(f"{path}: `{key}` must be a non-empty string, got `{value!r}`")
    return value


def validate_harness(
    data: object,
    path: str | Path,
    *,
    dir_name: str | None = None,
    models: dict[str, Model] | None = None,
    root: Path | None = None,
) -> HarnessConfig:
    """Validate one `harness.json` body.

    `dir_name`, `models` and `root` enable the cross-file rules of spec 05; each is skipped
    when the caller has not supplied what it needs.
    """
    path = str(path)
    if not isinstance(data, dict):
        raise ValidationError(f"{path}: must be a JSON object, got {type(data).__name__}")
    unknown = sorted(set(data) - set(_REQUIRED) - set(_OPTIONAL))
    if unknown:
        raise ValidationError(
            f"{path}: unknown field(s) {', '.join(unknown)}; allowed: "
            f"{', '.join(_REQUIRED + _OPTIONAL)}"
        )
    for key in _REQUIRED:
        if key not in data:
            raise ValidationError(f"{path}: missing `{key}`")

    item_id = _require_str(data, "id", path)
    if not ID_RE.match(item_id):
        raise ValidationError(
            f"{path}: `id` `{item_id}` does not match the id form `partner` or `<pod>-NNN` (spec 06)"
        )
    if dir_name is not None and item_id != dir_name:
        raise ValidationError(
            f"{path}: `id` is `{item_id}` but the directory is `config/{dir_name}/`; "
            f"spec 05 requires them to be equal"
        )

    pod = _require_str(data, "pod", path)
    role = _require_str(data, "role", path)
    model = _require_str(data, "model", path)
    effort = _require_str(data, "effort", path)
    if effort not in EFFORT_LEVELS:
        raise ValidationError(
            f"{path}: `effort` must be one of {', '.join(EFFORT_LEVELS)} (spec 11), got `{effort}`"
        )

    if item_id == PARTNER:
        if pod != PARTNER:
            raise ValidationError(f'{path}: the Partner must have `"pod": "partner"` (spec 05), got `{pod}`')
        if role != PARTNER:
            raise ValidationError(f'{path}: the Partner must have `"role": "partner"` (spec 05), got `{role}`')
        for forbidden in ("workdir", "branch"):
            if data.get(forbidden) is not None:
                raise ValidationError(
                    f"{path}: the Partner has no `{forbidden}`: it runs in HARNESS_ROOT with no "
                    f"worktree (spec 05, 17.4)"
                )
        workdir = branch = None
    else:
        workdir = _require_str(data, "workdir", path)
        branch = _require_str(data, "branch", path)

    harness = data.get("harness") or {}
    if not isinstance(harness, dict):
        raise ValidationError(f"{path}: `harness` must be an object, got {type(harness).__name__}")
    if "args" in harness and not (
        isinstance(harness["args"], list) and all(isinstance(a, str) for a in harness["args"])
    ):
        raise ValidationError(f"{path}: `harness.args` must be a list of strings")

    companion = data.get("companion") or {}
    if not isinstance(companion, dict):
        raise ValidationError(f"{path}: `companion` must be an object, got {type(companion).__name__}")
    for key in _COMPANION_INT_FIELDS:
        if key in companion:
            value = companion[key]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValidationError(
                    f"{path}: `companion.{key}` must be a positive integer, got `{value!r}`"
                )
    for key in _COMPANION_STR_FIELDS:
        if key in companion:
            value = companion[key]
            if not isinstance(value, str) or value.strip() == "":
                raise ValidationError(
                    f"{path}: `companion.{key}` must be a non-empty string, got `{value!r}`"
                )
    unknown_companion = sorted(set(companion) - set(_COMPANION_INT_FIELDS) - set(_COMPANION_STR_FIELDS))
    if unknown_companion:
        raise ValidationError(
            f"{path}: unknown `companion` field(s) {', '.join(unknown_companion)}"
        )

    if models is not None and model not in models:
        raise ValidationError(
            f"{path}: `model` `{model}` is not in config/models.json "
            f"(known: {', '.join(sorted(models)) or 'none'}); spec 05 requires it to be listed, "
            f"and models are always full ids, never aliases (spec 08)"
        )

    if root is not None:
        role_file = root / "companion" / "roles" / f"{role}.md"
        if not role_file.is_file():
            raise ValidationError(
                f"{path}: `role` `{role}` has no {role_file.relative_to(root)} (spec 05)"
            )
        if workdir is not None and not resolve_workdir(workdir, root).is_dir():
            raise ValidationError(
                f"{path}: `workdir` `{workdir}` does not exist "
                f"(resolved to {resolve_workdir(workdir, root)}); spec 05"
            )

    return HarnessConfig(
        id=item_id,
        pod=pod,
        role=role,
        model=model,
        effort=effort,
        workdir=workdir,
        branch=branch,
        harness=harness,
        companion=companion,
    )


def load_harness(
    path: str | Path,
    *,
    root: Path | None = None,
    models: dict[str, Model] | None = None,
    check_cross_file: bool = True,
) -> HarnessConfig:
    """Read and validate `config/<id>/harness.json`.

    With `root` given and `check_cross_file`, `models.json` is loaded for the model rule.
    """
    path = Path(path)
    if not path.exists():
        raise ValidationError(f"{path}: missing (spec 05)")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: not valid JSON: {exc}") from exc
    if models is None and root is not None and check_cross_file:
        models_path = root / "config" / "models.json"
        if models_path.exists():
            models = load_models(models_path)
    return validate_harness(
        data,
        path,
        dir_name=path.parent.name,
        models=models,
        root=root if check_cross_file else None,
    )
