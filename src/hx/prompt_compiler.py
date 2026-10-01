"""Audience-aware prompt artifacts and explicit native delivery plans.

Builds the planned runtime's prompts without changing a running session. Launch
acknowledgement and tool enforcement belong to the controller/adapter contract.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from . import compile as legacy, store
from .config_harness import load_harness, NO_SYSTEM_PROMPT_FLAVORS
from .continuity_store import canonical, digest
from .errors import ValidationError

SOURCE_BYTES = 65536
BUNDLE = Path(__file__).parent / "skeleton"
AUDIENCES = {"worker", "partner", "companion", "subagent"}


def _prefix(path, *, required=False):
    if not path.is_file():
        if required:
            raise ValidationError(f"missing prompt source: {path}")
        return ""
    chunks, size = [], 0
    with path.open("rb") as handle:
        while line := handle.readline(SOURCE_BYTES + 1 - size):
            size += len(line)
            if size > SOURCE_BYTES:
                raise ValidationError(f"prompt source exceeds 64 KiB before its memory boundary: {path}")
            if path.name == "AGENTS.md" and line.rstrip(b"\r\n") == legacy.HEADER.encode():
                break
            chunks.append(line)
    try:
        return b"".join(chunks).decode("utf-8").strip("\n")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"prompt source is not UTF-8: {path}") from exc


def _source(root, relative):
    local = root / relative
    return local if local.is_file() else BUNDLE / relative


def _known_remainder(text, known):
    """Strip exact generated blocks only; unfamiliar policy is never guessed away."""
    return legacy.strip_known_blocks(text, known)


def _policies(root, item_id, config, values):
    parts, review = [], []
    path = root / "continuity" / "policy.md"
    policy = _prefix(path)
    default_policy = _prefix(BUNDLE / "continuity" / "policy.md")
    if policy and policy != default_policy:
        parts.append(("operator-policy", path, policy))
    old_global = root / "config" / "CLAUDE.md"
    text = _prefix(old_global)
    known = _prefix(BUNDLE / "config" / "CLAUDE.md")
    if text:
        remainder = _known_remainder(text, [known])
        if remainder:
            parts.append(("operator-global", old_global, remainder))
            if known not in text:
                review.append("Modified legacy global instructions need migration review; their complete text was preserved.")
    old_role = root / "personas" / config.role / "AGENTS.md"
    role_text = _prefix(old_role)
    shipped_role = _prefix(BUNDLE / "personas" / config.role / "AGENTS.md")
    if role_text and role_text != shipped_role:
        remainder = _known_remainder(role_text, [shipped_role, legacy.expand_identity(shipped_role, values)])
        if remainder:
            parts.append(("operator-role", old_role, remainder))
            if shipped_role and shipped_role not in role_text:
                review.append("Modified legacy role instructions need migration review; unfamiliar policy was preserved.")
    agent_path = root / "config" / item_id / "AGENTS.md"
    agent_text = _prefix(agent_path)
    previous_roles = [match["body"].strip("\n") for match in legacy.REGION_RE.finditer(agent_text) if match["kind"] == "role"]
    upper = legacy.REGION_RE.sub("", agent_text)
    templates = [shipped_role, role_text, *previous_roles, _prefix(BUNDLE / "templates" / "worker" / "AGENTS.md")]
    known = [*templates, *(legacy.expand_identity(text, values) for text in templates)]
    custom = _known_remainder(upper, known)
    if custom:
        parts.append(("operator-agent", agent_path, custom))
    return parts, review


def _branch(workdir):
    result = subprocess.run(["git", "-C", workdir, "symbolic-ref", "--quiet", "--short", "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=3)
    if result.returncode:
        return None
    return result.stdout.decode("utf-8").strip()


def build(root: Path, item_id: str, *, audience=None, persist=True):
    root = root.resolve()
    config = load_harness(root / "config" / item_id / "harness.json", check_cross_file=False)
    audience = audience or ("partner" if item_id == "partner" else "worker")
    if audience not in AUDIENCES or (audience == "partner") != (item_id == "partner") and audience not in {"companion", "subagent"}:
        raise ValidationError("prompt audience does not match the configured executor")
    values = legacy.identity_values(root, item_id)
    # The repository currently launches companions through the Claude adapter.
    runtime = "claude" if audience == "companion" else config.flavor
    channel = "context" if audience == "subagent" or runtime in NO_SYSTEM_PROMPT_FLAVORS else "system"
    branch = _branch(values["workdir"])
    identity = {"id": item_id, "pod": config.pod, "role": config.role, "audience": audience,
                "runtime": runtime, "executor_runtime": config.flavor,
                "model": (config.companion or {}).get("model") if audience == "companion" else config.model,
                "workdir": values["workdir"], "branch": branch}
    identity_text = "Session identity: " + canonical(identity) + ".\nThe current task or frozen pass supplies assignment-specific inputs; identity alone does not supply a goal."
    sections, sources, seen = [], [], set()
    def add(name, path, body):
        expanded = legacy.expand_identity(body, values)
        source_hash = hashlib.sha256(body.encode()).hexdigest()
        body_hash = hashlib.sha256(expanded.encode()).hexdigest()
        sources.append({"section": name, "path": str(path) if path else None, "source_hash": source_hash})
        if body_hash in seen or not expanded.strip():
            return
        seen.add(body_hash)
        sections.append({"id": name, "channel": channel, "text": expanded,
                         "hash": body_hash, "bytes": len(expanded.encode())})
    common = _source(root, "continuity/prompts/common.md")
    add("common", common, _prefix(common, required=True))
    audience_path = _source(root, f"continuity/prompts/audiences/{audience}.md")
    add("audience", audience_path, _prefix(audience_path, required=True))
    folder = "companion-roles" if audience == "companion" else "roles"
    role_path = _source(root, f"continuity/prompts/{folder}/{config.role}.md")
    if not role_path.is_file():
        role_path = _source(root, f"continuity/prompts/{folder}/generic.md")
    add("role", role_path, _prefix(role_path, required=True))
    add("identity", None, identity_text)
    policies, review = _policies(root, item_id, config, values)
    # Companion/subagent policy must be audience-scoped. Executor domain and
    # per-agent mechanics never leak into a companion's restricted interface.
    for name, path, body in policies:
        if audience in {"companion", "subagent"} and name in {"operator-role", "operator-agent"}:
            continue
        add(name, path, body)
    specific = root / "continuity" / "policy" / f"{audience}.md"
    add("operator-audience", specific, _prefix(specific))
    rendered = {target: "\n\n".join(section["text"] for section in sections if section["channel"] == target) + "\n"
                for target in ("system", "context")}
    rendered = {target: text if text.strip() else "" for target, text in rendered.items()}
    if sum(len(text.encode()) for text in rendered.values()) > 262144:
        raise ValidationError("compiled static instructions exceed 256 KiB; reduce the required policy surface")
    manifest = {"schema_version": 1, "identity": identity, "sources": sources,
                "sections": [{key: value for key, value in section.items() if key != "text"} for section in sections],
                "delivery": {target: {"sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text.encode()),
                                      "status": "prepared" if text else "absent"} for target, text in rendered.items()},
                "tool_visibility": "unverified", "permissions_enforced": False,
                "activation": "pending_controller_installation", "migration_review": review,
                "task_inputs": "current checkpoint or frozen pass; never embedded in the static prefix"}
    version = digest(manifest)
    destination = root / "run" / item_id / "prompts" / audience / version
    result = {**manifest, "version": version,
              "system_path": str(destination / "system.md"), "context_path": str(destination / "context.md")}
    if persist:
        for target, text in rendered.items():
            store.atomic_write_text(destination / f"{target}.md", text)
        store.atomic_write_json(destination / "manifest.json", result)
    return {**result, "manifest_path": str(destination / "manifest.json")}


def verified_bundle(root: Path, item_id: str, manifest_path: Path, *, workdir: str):
    """Verify both prepared channels against current sources and configuration.

    This checks installation inputs; it does not acknowledge native delivery.
    """
    if not manifest_path.resolve().is_relative_to((root / "run" / item_id / "prompts").resolve()):
        raise ValidationError("prompt manifest must belong to the named worker")
    with manifest_path.open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValidationError("prompt manifest exceeds 64 KiB")
    try:
        manifest = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("prompt manifest must be JSON") from exc
    current = build(root, item_id, audience="worker", persist=False)
    expected = {key: value for key, value in current.items() if key != "manifest_path"}
    if manifest != expected or Path(current["identity"]["workdir"]).resolve() != Path(workdir).resolve():
        raise ValidationError("prompt sources, identity, placement, or workdir changed; compile current instructions")
    if current["migration_review"]:
        raise ValidationError("operator policy migration review remains unresolved; preserved legacy policy cannot be silently activated")
    rendered = {}
    for target in ("system", "context"):
        with Path(current[target + "_path"]).open("rb") as handle:
            data = handle.read(262145)
        if len(data) != current["delivery"][target]["bytes"] or hashlib.sha256(data).hexdigest() != current["delivery"][target]["sha256"]:
            raise ValidationError("compiled instructions are missing or altered")
        rendered[target] = data.decode("utf-8")
    return current, rendered


def context_instructions(root: Path, item_id: str, manifest_path: Path, *, workdir: str):
    current, rendered = verified_bundle(root, item_id, manifest_path, workdir=workdir)
    if current["delivery"]["system"]["bytes"]:
        raise ValidationError("system prompt installation is not acknowledged by a native controller")
    return rendered["context"], current["version"]
