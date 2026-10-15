"""`hx models` — what each provider serves now, diffed against what hx ships.

TOOL_SKILL_PLAN §Tools To Build, P0 `hx models`: one read-only query per provider, in
order —

    anthropic  GET https://api.anthropic.com/v1/models?lifecycle=active&limit=1000
               (X-Api-Key: ANTHROPIC_API_KEY, anthropic-version: 2023-06-01)
    openai     GET https://api.openai.com/v1/models          (Bearer OPENAI_API_KEY)
    xai        GET https://api.x.ai/v1/language-models       (Bearer XAI_API_KEY)
    meta       GET https://api.meta.ai/v1/models             (Bearer META_API_KEY)
    typesafe   no request: `hx.jev.MODEL`, printed as `pinned`

— each diffed against `config/models.json` and the backticked IDs of `docs/models.md`.
Credentials come from the process environment, then the instance's env file
(`config/auth.json` `env_file`), then the `seed/` file `hx install` wrote for that adapter.
The Claude OAuth seed (`seed/token`) cannot call the Models API, so it counts only when it is
an API key (`sk-ant-api…`). A missing credential is `unverified`, any HTTP or transport
failure is `error` with its status, 5 s per provider, no retry, no fallback. Nothing is
written: the diff is the proposal, and the Partner or operator commits the registry change.
No credential is ever printed: output names where a key came from, never the key, and every
string is scrubbed of the values in use before it leaves this module.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import timestamps
from .errors import HxError

TIMEOUT_S = 5.0
MAX_BYTES = 8 * 1024 * 1024
ANTHROPIC_VERSION = "2023-06-01"
#: The repository's provider list, when hx runs from a checkout (an installed package has none).
DOCS = Path(__file__).resolve().parents[2] / "docs" / "models.md"

#: Provider → endpoint, header style, credential names in the environment/env file, seed
#: file, the `docs/models.md` heading, and the ID prefixes that are this provider's
#: (configured IDs are attributed by prefix; `added` lists only IDs of the family, so a
#: provider's embeddings and audio models do not drown the diff).
PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "url": "https://api.anthropic.com/v1/models?lifecycle=active&limit=1000",
        "auth": "x-api-key", "keys": ("ANTHROPIC_API_KEY",),
        "seed": "token", "seed_prefix": "sk-ant-api", "heading": "anthropic",
        "family": ("claude-",),
    },
    "openai": {
        "url": "https://api.openai.com/v1/models",
        "auth": "bearer", "keys": ("OPENAI_API_KEY",), "seed": "codex-token", "heading": "openai",
        "family": ("gpt-", "o1", "o3", "o4", "codex-"),
    },
    "xai": {
        "url": "https://api.x.ai/v1/language-models",
        "auth": "bearer", "keys": ("XAI_API_KEY", "GROK_API_KEY"), "seed": "grok-token",
        "heading": "xai", "family": ("grok-",),
    },
    "meta": {
        "url": "https://api.meta.ai/v1/models",
        "auth": "bearer", "keys": ("META_API_KEY",), "seed": "meta-token", "heading": "meta",
        "family": ("muse-", "llama-", "llama"),
    },
    "typesafe": {"heading": "typesafe", "family": ("jev-",)},
}
ORDER = ("anthropic", "openai", "xai", "meta", "typesafe")

_WINDOW_KEYS = ("max_input_tokens", "context_window", "context_length", "max_context_tokens",
                "input_token_limit", "max_prompt_length")
_EFFORT_KEYS = ("effort", "efforts", "reasoning_effort", "reasoning_efforts",
                "supported_reasoning_efforts", "supported_efforts")
_DEPRECATED_LIFECYCLE = {"deprecated", "legacy", "retiring", "retired", "sunset"}


class FetchError(Exception):
    """A provider request that did not produce a JSON body: HTTP status or transport reason."""

    def __init__(self, status: int | None, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def fetch(url: str, headers: dict[str, str], timeout: float = TIMEOUT_S) -> object:
    """One GET, stdlib only. Raises FetchError; the response body of a failure is never kept
    (some providers echo part of the key back in it)."""
    from . import __version__

    request = urllib.request.Request(url, headers={
        **headers, "Accept": "application/json", "User-Agent": f"hx/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — fixed https URLs
            body = response.read(MAX_BYTES)
    except urllib.error.HTTPError as exc:
        raise FetchError(exc.code, f"HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise FetchError(None, f"transport: {exc.reason}") from None
    except (TimeoutError, OSError) as exc:
        raise FetchError(None, f"transport: {type(exc).__name__}") from None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FetchError(None, "the response is not JSON") from None


# --- inputs ----------------------------------------------------------------------------------------


def _env_file_values(root: Path) -> tuple[dict[str, str], str | None]:
    """The instance env file's values, and a note when it is configured but unreadable."""
    from . import envfile

    try:
        settings = envfile.settings(root)
    except HxError as exc:
        return {}, exc.message
    path = settings.get("env_file")
    if not path:
        return {}, None
    try:
        return envfile.parse(Path(path)), None
    except HxError as exc:
        return {}, exc.message


def credential(provider: str, root: Path, env, env_file: dict[str, str]) -> tuple[str | None, str | None]:
    """(value, origin) for one provider, or (None, None). The origin names a variable or a
    file, never the value."""
    spec = PROVIDERS[provider]
    for name in spec.get("keys", ()):
        if env.get(name):
            return env[name], f"env:{name}"
    for name in spec.get("keys", ()):
        if env_file.get(name):
            return env_file[name], f"env-file:{name}"
    seed = spec.get("seed")
    if seed:
        path = root / "seed" / seed
        try:
            value = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        except OSError:
            value = ""
        prefix = spec.get("seed_prefix")
        if value and (prefix is None or value.startswith(prefix)):
            return value, f"seed/{seed}"
    return None, None


def documented(path: Path | None) -> dict[str, list[str]] | None:
    """`docs/models.md`: provider heading → the first backticked token of each bullet."""
    if path is None or not path.is_file():
        return None
    found: dict[str, list[str]] = {}
    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            section = heading.group(1).strip().lower()
            continue
        bullet = re.match(r"^\s*[-*]\s+`([^`]+)`", line)
        if bullet and section:
            found.setdefault(section, []).append(bullet.group(1))
    return found


def configured(root: Path) -> dict[str, dict]:
    """`config/models.json` rows, by ID (unvalidated: a broken registry is still diffed)."""
    path = root / "config" / "models.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HxError(f"{path}: not valid JSON: {exc}") from exc
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def provider_of(model_id: str) -> str | None:
    for name in ORDER:
        if model_id.startswith(PROVIDERS[name]["family"]):
            return name
    return None


# --- parsing a listing -----------------------------------------------------------------------------


def _records(body: object) -> list[dict]:
    """The model records of any of the listing shapes: `{data: [...]}`, `{models: [...]}`, `[...]`."""
    if isinstance(body, dict):
        for key in ("data", "models", "language_models"):
            if isinstance(body.get(key), list):
                body = body[key]
                break
    if not isinstance(body, list):
        raise FetchError(None, "the response has no model list (`data` or `models`)")
    return [r for r in body if isinstance(r, dict) and isinstance(r.get("id"), str)]


def _deprecated(record: dict) -> bool:
    lifecycle = str(record.get("lifecycle") or record.get("status") or "").lower()
    if lifecycle in _DEPRECATED_LIFECYCLE or record.get("deprecated") is True:
        return True
    return any(record.get(key) for key in ("deprecated_at", "deprecation_date", "retirement_date"))


def _window(record: dict) -> int | None:
    for where in (record, record.get("limits") or {}, record.get("capabilities") or {}):
        if not isinstance(where, dict):
            continue
        for key in _WINDOW_KEYS:
            value = where.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
    return None


def _effort(record: dict) -> list[str] | None:
    for where in (record, record.get("capabilities") or {}):
        if not isinstance(where, dict):
            continue
        for key in _EFFORT_KEYS:
            value = where.get(key)
            if isinstance(value, list):
                levels = [v if isinstance(v, str) else (v.get("effort") or v.get("name"))
                          for v in value if isinstance(v, (str, dict))]
                return [str(v) for v in levels if v]
            if isinstance(value, dict):
                levels = [name for name, flag in value.items()
                          if flag is True or (isinstance(flag, dict) and flag.get("supported"))]
                if levels:
                    return levels
    return None


def _headers(provider: str, key: str) -> dict[str, str]:
    if PROVIDERS[provider]["auth"] == "x-api-key":
        return {"X-Api-Key": key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {key}"}


def _base(provider: str, status: str, source: str) -> dict:
    return {"status": status, "source": source, "ids": [], "added": [], "removed": [],
            "deprecated": [], "window": {}, "effort": {}}


def _diff(result: dict, provider: str, docs: dict[str, list[str]] | None,
          registry: dict[str, dict], returned: set[str]) -> None:
    """Fill `added`, `removed`, `configured`, `documented`, `missing`, `window_over`."""
    family = PROVIDERS[provider]["family"]
    ids = set(result["ids"])
    listed = None if docs is None else set(docs.get(PROVIDERS[provider]["heading"], []))
    mine = {model_id: row for model_id, row in registry.items() if provider_of(model_id) == provider}
    result["configured"] = sorted(mine)
    result["documented"] = None if listed is None else sorted(listed)
    known = (listed or set()) | set(mine)
    result["added"] = sorted(i for i in ids - (listed or set()) if i.startswith(family))
    result["removed"] = sorted(known - returned)
    result["missing"] = sorted(set(mine) - ids)
    over = {}
    for model_id, row in mine.items():
        limit = result["window"].get(model_id)
        window = row.get("window")
        if isinstance(limit, int) and isinstance(window, int) and window > limit:
            over[model_id] = {"configured": window, "max_input_tokens": limit}
    result["window_over"] = over


def query(provider: str, root: Path, *, env, env_file: dict[str, str],
          docs: dict[str, list[str]] | None, registry: dict[str, dict]) -> dict:
    """One provider's block (CONTRACTS.md `hx models`)."""
    if provider == "typesafe":
        from . import jev

        result = _base(provider, "pinned", "hx.jev.MODEL")
        result["ids"] = [jev.MODEL]
        _diff(result, provider, docs, registry, {jev.MODEL})
        return result
    spec = PROVIDERS[provider]
    key, origin = credential(provider, root, env, env_file)
    result = _base(provider, "unverified", spec["url"])
    result["credential"] = origin
    if key is None:
        result["reason"] = f"no credential ({' / '.join(spec['keys'])} or seed/{spec['seed']})"
        if provider == "anthropic":
            result["reason"] += "; the Claude OAuth seed token cannot call the Models API"
        _diff(result, provider, docs, registry, set())
        result["removed"], result["missing"] = [], []
        return result
    try:
        records = _records(fetch(spec["url"], _headers(provider, key), TIMEOUT_S))
    except FetchError as exc:
        result.update(status="error", http=exc.status, reason=exc.reason)
        _diff(result, provider, docs, registry, set())
        result["removed"], result["missing"] = [], []
        return result
    result["status"] = "ok"
    returned = set()
    for record in records:
        model_id = record["id"]
        returned.add(model_id)
        (result["deprecated"] if _deprecated(record) else result["ids"]).append(model_id)
        window = _window(record)
        if window is not None:
            result["window"][model_id] = window
        effort = _effort(record)
        if effort:
            result["effort"][model_id] = effort
    result["ids"].sort()
    result["deprecated"].sort()
    _diff(result, provider, docs, registry, returned)
    return result


def report(root: Path, *, providers: list[str] | None = None, env=None,
           docs_path: Path | None = DOCS) -> dict:
    """Every selected provider, in order, plus the `--expect current` verdict inputs."""
    env = os.environ if env is None else env
    env_file, env_note = _env_file_values(root)
    docs = documented(docs_path)
    registry = configured(root)
    blocks = {name: query(name, root, env=env, env_file=env_file, docs=docs, registry=registry)
              for name in (providers or ORDER)}
    stale = []
    for name, block in blocks.items():
        if block["status"] != "ok":
            continue
        stale += [f"{name}: {model_id} is configured but not in the active list"
                  for model_id in block["missing"]]
        stale += [f"{name}: {model_id} window {row['configured']} exceeds max_input_tokens "
                  f"{row['max_input_tokens']}" for model_id, row in block["window_over"].items()]
    doc = {"ts": timestamps.now(), "docs": str(docs_path) if docs is not None else None,
           "env_file": env_note, "providers": blocks, "stale": stale}
    secrets = [credential(name, root, env, env_file)[0] for name in blocks if name != "typesafe"]
    return _scrub(doc, [s for s in secrets if s])


def _scrub(value, secrets: list[str]):
    """Belt and braces: no string leaving this module contains a credential in use."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, dict):
        return {_scrub(k, secrets): _scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, secrets) for v in value]
    return value


def _short(items, limit: int = 12) -> str:
    items = list(items)
    if not items:
        return "-"
    shown = ", ".join(items[:limit])
    return shown + (f" (+{len(items) - limit})" if len(items) > limit else "")


def render(doc: dict) -> str:
    lines = []
    for name, block in doc["providers"].items():
        head = f"{name:<10} {block['status']:<10} {block['source']}"
        if block.get("credential"):
            head += f"  credential={block['credential']}"
        lines.append(head)
        if block.get("reason"):
            lines.append(f"  reason: {block['reason']}")
        if block["status"] in ("ok", "pinned"):
            lines.append(f"  ids {len(block['ids'])}: {_short(block['ids'])}")
            lines.append(f"  added: {_short(block['added'])}")
            lines.append(f"  removed: {_short(block['removed'])}")
            lines.append(f"  deprecated: {_short(block['deprecated'])}")
            mine = {k: v for k, v in block["window"].items() if k in set(block.get("configured", []))}
            if mine:
                lines.append("  window: " + ", ".join(f"{k}={v}" for k, v in sorted(mine.items())))
            efforts = {k: v for k, v in block["effort"].items() if k in set(block.get("configured", []))}
            if efforts:
                lines.append("  effort: " + "; ".join(f"{k}={'/'.join(v)}" for k, v in sorted(efforts.items())))
    for line in doc["stale"]:
        lines.append(f"stale: {line}")
    counts = {s: sum(1 for b in doc["providers"].values() if b["status"] == s)
              for s in ("ok", "unverified", "error", "pinned")}
    lines.append("HX-MODELS " + " ".join(f"{k}={v}" for k, v in counts.items())
                 + f" stale={len(doc['stale'])}")
    return "\n".join(lines)


def main(argv: list[str], root: Path, env=None) -> int:
    """`hx models [--provider P] [--expect current] [--json]`. Writes nothing."""
    from .caller import require_partner_caller

    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(
        prog="hx models",
        description="each provider's active model list, diffed against config/models.json and "
                    "docs/models.md; writes nothing",
        epilog="example: hx models --provider anthropic --expect current",
    )
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.add_argument("--provider", action="append", choices=ORDER,
                        help="one provider (repeatable); default every provider in order")
    parser.add_argument("--expect", choices=("current",),
                        help="current: exit 3 when a configured ID is not active or its window "
                             "exceeds the provider's max_input_tokens")
    parser.add_argument("--docs", type=Path, default=DOCS, help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    require_partner_caller("models", env)
    doc = report(root, providers=args.provider, env=env, docs_path=args.docs)
    doc["expect"] = args.expect
    print(json.dumps(doc, indent=2) if args.json else render(doc))
    if args.expect == "current" and doc["stale"]:
        print(f"hx: models: --expect current: {len(doc['stale'])} stale entr"
              f"{'y' if len(doc['stale']) == 1 else 'ies'}", file=sys.stderr)
        return 3
    return 0
