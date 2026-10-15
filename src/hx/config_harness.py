"""Validator and parser for `config/<id>/harness.json` (spec 05).

Spec 05 names four cross-file rules: `id` equals the directory name; `model` exists in
`models.json`; `role` has a `companion/roles/<role>.md`; `workdir` exists. The Partner has no
`workdir`: it runs in HARNESS_ROOT. There is no `branch` — hx manages no git (spec 14 D25).
Failure is exit 2.
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
_OPTIONAL = ("workdir", "harness", "companion", "learning", "flavor", "purpose", "explorer")

#: `explorer`: what the worker's orientation subagent runs on. Every engineer persona's first
#: rule spawns it with the goal paragraph before the first edit (Agent tool, this model and
#: effort), and the identity line names both. Both keys are required when the block is there.
_EXPLORER_FIELDS = ("model", "effort")

#: What `templates/worker/harness.json` ships in `purpose`. The Partner replaces it with the one
#: paragraph that makes this agent different from the next one of its role; until then the
#: rendered persona has no purpose and `hx doctor` warns (docs/prompt-runtime.md).
PURPOSE_PLACEHOLDER = "{{purpose}}"

#: Which adapter launches this agent. Absent means Claude, so existing configs keep working.
FLAVORS = ("claude", "pi", "grok", "meta", "codex")

#: Flavors whose CLI takes no system-prompt flag, verified per adapter: `muse` exposes none
#: and neither does `codex` (launched bare). The planned runtime's prompt compiler puts their
#: system section in the context channel (`hx.prompt_compiler`).
NO_SYSTEM_PROMPT_FLAVORS = ("meta", "codex")

#: The one channel `config/CLAUDE.md` (the invariants) reaches an agent through, per flavor.
#: Exactly one each, never two: `home` is the home's native user CLAUDE.md, which
#: `adapters/claude/start.sh` refreshes and Claude Code loads at every start; `agents` is
#: `$CODEX_HOME/AGENTS.md`, which `adapters/codex/start.sh` writes at every exec (the
#: invariants, then the persona); `hook` is the `context` hook's output at every boundary
#: (`hx.persona.boundary`), for the flavors with no user instruction file hx can use.
INVARIANTS_CHANNEL = {"claude": "home", "codex": "agents", "pi": "hook", "grok": "hook", "meta": "hook"}

#: The one channel `personas/<role>.md` reaches an agent through, per flavor: `hook` is the
#: `context` hook's output at every boundary; `flag` is the file itself, passed to Pi's
#: `--append-system-prompt` and Grok's `--rules` at launch; `agents` is `$CODEX_HOME/AGENTS.md`.
PERSONA_CHANNEL = {"claude": "hook", "codex": "agents", "pi": "flag", "grok": "flag", "meta": "hook"}


def invariants_channel(flavor: str) -> str:
    """`home`, `agents` or `hook`: where the invariants reach an agent of this flavor."""
    return INVARIANTS_CHANNEL.get(flavor, "home")


def persona_channel(flavor: str) -> str:
    """`hook`, `flag` or `agents`: where the persona reaches an agent of this flavor."""
    return PERSONA_CHANNEL.get(flavor, "hook")

#: The Partner runs on Claude or Codex. The other flavors stay worker-only: `hx wake`
#: reaches a Codex Partner by pasting into its pane, while pi/grok/meta have no
#: wake path at all (spec 12).
PARTNER_FLAVORS = ("claude", "codex")

_COMPANION_INT_FIELDS = (
    "batch_records",
    "state_budget_tokens",
    "seam_min_context_tokens",
)

# Zero permits immediate checkpoint boundaries.
_COMPANION_NON_NEGATIVE_FIELDS = ("seam_min_interval_s",)
_COMPANION_STR_FIELDS = ("provider", "model", "cache_ttl", "effort")

#: `disabled` controls the optional native Companion session.
_COMPANION_BOOL_FIELDS = ("disabled",)

#: Spec 05: `claude-cli` goes through the pinned binary with the seed token and is the only one
#: built; `anthropic` is the Messages API with an API key, for instances that have one.
#: `openai-api` is the OpenAI API companion for Codex/OpenAI lanes (fleet direction
#: 2026-10-05); validation-only, like the other two — Companion sessions are still
#: created and routed by `start.sh --companion` in tmux, independent of this value.
COMPANION_PROVIDERS = ("claude-cli", "anthropic", "openai-api")


def resolve_workdir(workdir: str, root: Path) -> Path:
    """A relative `workdir` is relative to HARNESS_ROOT (CONTRACTS.md); an absolute one stands.

    Spec 17.2: the workdir is whatever directory the Partner chose for that agent. hx neither
    creates a repository there nor cares whether it is one.
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
    flavor: str = "claude"
    harness: dict = field(default_factory=dict)
    companion: dict = field(default_factory=dict)
    learning: dict = field(default_factory=dict)
    purpose: str | None = None
    explorer: dict = field(default_factory=dict)

    @property
    def is_partner(self) -> bool:
        return self.id == PARTNER


def purpose_text(value: object) -> str | None:
    """The purpose as written, or None while it is absent, empty, or still the placeholder."""
    if not isinstance(value, str) or not value.strip() or PURPOSE_PLACEHOLDER in value:
        return None
    return value.strip("\n")


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
        if data.get("workdir") is not None:
            raise ValidationError(
                f"{path}: the Partner has no `workdir`: it runs in HARNESS_ROOT (spec 05, 17.4)"
            )
        workdir = None
    else:
        workdir = _require_str(data, "workdir", path)

    flavor = data.get("flavor", "claude")
    if not isinstance(flavor, str) or flavor not in FLAVORS:
        raise ValidationError(
            f"{path}: `flavor` must be one of {', '.join(FLAVORS)}, got `{flavor!r}`"
        )
    if item_id == PARTNER and flavor not in PARTNER_FLAVORS:
        raise ValidationError(
            f"{path}: the Partner runs on claude or codex (spec 12), "
            f"got `flavor` `{flavor}`"
        )

    purpose = data.get("purpose")
    if purpose is not None and not isinstance(purpose, str):
        raise ValidationError(f"{path}: `purpose` must be a string, got {type(purpose).__name__}")

    explorer = data.get("explorer") or {}
    if not isinstance(explorer, dict):
        raise ValidationError(f"{path}: `explorer` must be an object, got {type(explorer).__name__}")
    if explorer:
        unknown_explorer = sorted(set(explorer) - set(_EXPLORER_FIELDS))
        if unknown_explorer:
            raise ValidationError(
                f"{path}: unknown `explorer` field(s) {', '.join(unknown_explorer)}; allowed: "
                f"{', '.join(_EXPLORER_FIELDS)}"
            )
        for key in _EXPLORER_FIELDS:
            value = explorer.get(key)
            if not isinstance(value, str) or value.strip() == "":
                raise ValidationError(
                    f"{path}: `explorer.{key}` must be a non-empty string, got `{value!r}`"
                )
        if explorer["effort"] not in EFFORT_LEVELS:
            raise ValidationError(
                f"{path}: `explorer.effort` must be one of {', '.join(EFFORT_LEVELS)} "
                f"(spec 11), got `{explorer['effort']}`"
            )

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
    for key in _COMPANION_NON_NEGATIVE_FIELDS:
        if key in companion:
            value = companion[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError(
                    f"{path}: `companion.{key}` must be a non-negative integer, got `{value!r}`"
                )
    for key in _COMPANION_STR_FIELDS:
        if key in companion:
            value = companion[key]
            if not isinstance(value, str) or value.strip() == "":
                raise ValidationError(
                    f"{path}: `companion.{key}` must be a non-empty string, got `{value!r}`"
                )
    for key in _COMPANION_BOOL_FIELDS:
        if key in companion:
            value = companion[key]
            if not isinstance(value, bool):
                raise ValidationError(
                    f"{path}: `companion.{key}` must be a boolean, got `{value!r}`"
                )
    companion_effort = companion.get("effort")
    if companion_effort is not None and companion_effort not in EFFORT_LEVELS:
        raise ValidationError(
            f"{path}: `companion.effort` must be one of {', '.join(EFFORT_LEVELS)} "
            f"(spec 11), got `{companion_effort}`"
        )
    provider = companion.get("provider")
    if provider is not None and provider not in COMPANION_PROVIDERS:
        raise ValidationError(
            f"{path}: `companion.provider` must be one of {', '.join(COMPANION_PROVIDERS)} "
            f"(spec 05), got `{provider!r}`"
        )

    unknown_companion = sorted(
        set(companion) - set(_COMPANION_INT_FIELDS) - set(_COMPANION_NON_NEGATIVE_FIELDS)
        - set(_COMPANION_STR_FIELDS) - set(_COMPANION_BOOL_FIELDS)
    )
    if unknown_companion:
        raise ValidationError(
            f"{path}: unknown `companion` field(s) {', '.join(unknown_companion)}"
        )

    learning = data.get("learning") or {}
    if not isinstance(learning, dict) or learning.keys() - {"model", "effort", "disabled", "batch_records", "request_bytes", "max_proposals"}:
        raise ValidationError(f"{path}: learning must contain only model, effort, disabled, batch_records, request_bytes, max_proposals")
    for key in ("model", "effort"):
        if key in learning and (not isinstance(learning[key], str) or not learning[key].strip()):
            raise ValidationError(f"{path}: learning.{key} must be nonempty text")
    if "effort" in learning and learning["effort"] not in EFFORT_LEVELS:
        raise ValidationError(f"{path}: learning.effort must be one of {', '.join(EFFORT_LEVELS)}")
    if "disabled" in learning and type(learning["disabled"]) is not bool:
        raise ValidationError(f"{path}: learning.disabled must be a boolean")
    for key, maximum in (("batch_records", 128), ("request_bytes", 262144), ("max_proposals", 8)):
        if key in learning and (type(learning[key]) is not int or not 1 <= learning[key] <= maximum):
            raise ValidationError(f"{path}: learning.{key} must be an integer from 1 to {maximum}")

    if models is not None and model not in models:
        raise ValidationError(
            f"{path}: `model` `{model}` is not in config/models.json "
            f"(known: {', '.join(sorted(models)) or 'none'}); spec 05 requires it to be listed, "
            f"and models are always full ids, never aliases (spec 08)"
        )
    if models is not None and explorer and explorer["model"] not in models:
        raise ValidationError(
            f"{path}: `explorer.model` `{explorer['model']}` is not in config/models.json "
            f"(known: {', '.join(sorted(models)) or 'none'}); like `model`, it must be listed"
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
        flavor=flavor,
        harness=harness,
        companion=companion,
        learning=learning,
        purpose=purpose_text(purpose),
        explorer=explorer,
    )


def flavor_of(root: Path, item_id: str) -> str:
    """The adapter directory name for an id. Unreadable config stays `claude`."""
    path = root / "config" / item_id / "harness.json"
    if not path.is_file():
        return "claude"
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return "claude"
    if not isinstance(data, dict):
        return "claude"
    flavor = data.get("flavor") or "claude"
    return flavor if flavor in FLAVORS else "claude"


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
