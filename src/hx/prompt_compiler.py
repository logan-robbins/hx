"""Compile audience-aware instructions and verified native delivery artifacts.

All worker assignments use the same packet/run lifecycle. Compilation prepares
instructions without changing a running session; the native controller owns
installation, acknowledgment, tool policy, and initial-input budget preflight.

Configured sources are common policy, audience mechanics, the role persona's
Domain judgment section, worker purpose, Companion role retention, and operator
policy. Identity expands from harness.json. Source and bundle limits apply before
publication. Per-id instruction files and global CLAUDE.md are not an alternate
worker prompt authority; required assignment facts arrive in the current ledger
checkpoint or frozen observation pass rather than the invariant prefix.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import instruction_materials, learn, persona as persona_mod, store
from .ids import PARTNER
from .config_harness import load_harness, purpose_text, NO_SYSTEM_PROMPT_FLAVORS
from .continuity_store import canonical, digest
from .errors import ValidationError

SOURCE_BYTES = 262144
BUNDLE = Path(__file__).parent / "skeleton"
AUDIENCES = {"worker", "partner", "companion", "learning", "subagent"}
#: Domain judgment is shared by compiled audience instructions and the configured persona.
DOMAIN_RE = re.compile(r"^## Domain judgment\n(?P<body>.*?)(?=^## |\Z)", re.S | re.M)


def _prefix(path, *, required=False, persona=False):
    if not path.is_file():
        if required:
            raise ValidationError(f"missing prompt source: {path}")
        return ""
    data = path.read_bytes()
    if len(data) > SOURCE_BYTES:
        raise ValidationError(f"prompt source exceeds 256 KiB: {path}; split the source explicitly")
    try:
        return data.decode("utf-8").strip("\n")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"prompt source is not UTF-8: {path}") from exc


def _source(root, relative):
    local = root / relative
    return local if local.is_file() else BUNDLE / relative


def _domain(root, item_id, config):
    """`(path, text)`: the `## Domain judgment` section of the persona the live agent reads,
    `personas/<role>.md` (the Partner's `personas/partner.md`), or "" when the role has no
    persona or the persona has no such section."""
    path = persona_mod.role_source(root, config.role)
    match = DOMAIN_RE.search(_prefix(path, required=item_id == PARTNER, persona=True))
    return path, match["body"].strip("\n") if match else ""


def _policies(root, audience):
    """Operator policy: `continuity/policy.md` where it differs from the shipped default, then
    `continuity/policy/<audience>.md`."""
    parts = []
    path = root / "continuity" / "policy.md"
    policy = _prefix(path)
    if policy and policy != _prefix(BUNDLE / "continuity" / "policy.md"):
        parts.append(("operator-policy", path, policy))
    specific = root / "continuity" / "policy" / f"{audience}.md"
    parts.append(("operator-audience", specific, _prefix(specific)))
    return parts


def _branch(workdir):
    result = subprocess.run(["git", "-C", workdir, "symbolic-ref", "--quiet", "--short", "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=3)
    if result.returncode:
        return None
    return result.stdout.decode("utf-8").strip()



def _verify_required_file(material):
    return instruction_materials.verify_file(material)

def build(root: Path, item_id: str, *, audience=None, persist=True, env=None):
    root = root.resolve()
    config = load_harness(root / "config" / item_id / "harness.json", check_cross_file=False)
    audience = audience or ("partner" if item_id == "partner" else "worker")
    if audience not in AUDIENCES or (audience == "partner") != (item_id == "partner") and audience not in {"companion", "learning", "subagent"}:
        raise ValidationError("prompt audience does not match the configured executor")
    values = persona_mod.identity_values(root, item_id)
    # Companions run on the Claude Messages API whatever the worker's flavor.
    runtime = "claude" if audience in {"companion", "learning"} else config.flavor
    channel = "context" if audience == "subagent" or runtime in NO_SYSTEM_PROMPT_FLAVORS else "system"
    branch = _branch(values["workdir"])
    sidecar = config.learning if audience == "learning" else config.companion
    identity = {"id": item_id, "pod": config.pod, "role": config.role, "audience": audience,
                "runtime": runtime, "executor_runtime": config.flavor,
                "model": ((sidecar or {}).get("model") or config.model) if audience in {"companion", "learning"} else config.model,
                "workdir": values["workdir"], "branch": branch}
    identity_text = "Session identity: " + canonical(identity) + ".\nThe current task or frozen pass supplies assignment-specific inputs; identity alone does not supply a goal."
    sections, sources, seen = [], [], set()
    def add(name, path, body, **metadata):
        expanded = persona_mod.expand_identity(body, values)
        source_hash = hashlib.sha256(body.encode()).hexdigest()
        body_hash = hashlib.sha256(expanded.encode()).hexdigest()
        sources.append({"section": name, "path": str(path) if path else None, "source_hash": source_hash, "bytes": len(body.encode()), **metadata})
        if body_hash in seen or not expanded.strip():
            return
        seen.add(body_hash)
        sections.append({"id": name, "channel": channel, "text": expanded,
                         "hash": body_hash, "bytes": len(expanded.encode())})
    common = _source(root, "continuity/prompts/common.md")
    add("common", common, _prefix(common, required=True))
    audience_path = _source(root, f"continuity/prompts/audiences/{audience}.md")
    add("audience", audience_path, _prefix(audience_path, required=True))
    if audience == "companion":
        # The Companion's role file, exactly as its live system prompt reads it.
        role_path = root / "companion" / "roles" / f"{config.role}.md"
        add("role", role_path, _prefix(role_path, required=True))
    else:
        add("domain", *_domain(root, item_id, config))
        if audience == "worker":
            add("purpose", root / "config" / item_id / "harness.json", purpose_text(config.purpose) or "")
    add("identity", None, identity_text)
    for name, path, body in _policies(root, audience):
        add(name, path, body)
    for path in learn.rule_files(root, env, config.role):
        role_scope = path.parent == learn.role_dir(root, config.role)
        scope = config.role if role_scope else "global"
        # Only reviewed rule category files are instruction sources. Proposals
        # remain independent-review inputs, never executable instructions.
        add(f"learning:{scope}:{path.stem}", path, path.read_text(encoding="utf-8"),
            kind="reviewed-learning", scope=scope, category=path.stem)
    full = {target: "\n\n".join(section["text"] for section in sections if section["channel"] == target) + "\n"
            for target in ("system", "context")}
    full = {target: value if value.strip() else "" for target, value in full.items()}
    if sum(len(value.encode()) for value in full.values()) > 262144:
        raise ValidationError("compiled static instructions exceed 256 KiB; reduce the required policy surface")
    required_files, materials, rendered = [], {}, dict(full)
    # Companion evidence tools cannot read arbitrary files, so its exact instructions
    # stay on the configured channel rather than creating unreachable references.
    if audience not in {"companion", "learning"}:
        material_text = "\n\n".join(section["text"] for section in sections if section["id"] != "identity") + "\n"
        required_files, materials = instruction_materials.prepare(
            material_text, root / "run" / item_id / "prompts" / "material",
            channel=channel, delivery="inline", sections=[section["id"] for section in sections if section["id"] != "identity"])
    manifest = {"schema_version": 1, "identity": identity, "sources": sources,
                "sections": [{key: value for key, value in section.items() if key != "text"} for section in sections],
                "delivery": {target: {"sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text.encode()),
                                      "status": "prepared" if text else "absent"} for target, text in rendered.items()},
                "tool_visibility": "unverified", "permissions_enforced": False,
                "activation": "pending_controller_installation",
                "task_inputs": "current checkpoint or frozen pass; never embedded in the static prefix",
                "required_files": required_files,
                "full_instruction_bytes": sum(len(value.encode()) for value in full.values())}
    version = digest(manifest)
    destination = root / "run" / item_id / "prompts" / audience / version
    result = {**manifest, "version": version,
              "system_path": str(destination / "system.md"), "context_path": str(destination / "context.md")}
    if persist:
        instruction_materials.install(required_files, materials)
        for target, text in rendered.items():
            store.atomic_write_text(destination / f"{target}.md", text)
        store.atomic_write_json(destination / "manifest.json", result)
    return {**result, "manifest_path": str(destination / "manifest.json")}


def verified_bundle(root: Path, item_id: str, manifest_path: Path, *, workdir: str, audience="worker"):
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
    current = build(root, item_id, audience=audience, persist=False)
    expected = {key: value for key, value in current.items() if key != "manifest_path"}
    if manifest != expected or Path(current["identity"]["workdir"]).resolve() != Path(workdir).resolve():
        raise ValidationError("prompt sources, identity, placement, or workdir changed; compile current instructions")
    instruction_materials.read_bundle(current["required_files"])
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


def verify_material(root: Path, item_id: str, manifest_path: Path, path: Path, *, workdir: str, audience="worker"):
    """Authorize a complete read only for an exact verified required instruction file."""
    manifest, _ = verified_bundle(root, item_id, manifest_path, workdir=workdir, audience=audience)
    requested = Path(path).absolute()
    for material in manifest["required_files"]:
        if requested == Path(material["path"]).absolute():
            _verify_required_file(material)
            return material
    return None
