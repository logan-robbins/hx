"""Audience-aware prompt artifacts and explicit native delivery plans.

Builds the planned runtime's prompts without changing a running session. Launch
acknowledgement and tool enforcement belong to the controller/adapter contract.

A planned prompt reads the sources a live agent reads, one each, and nothing per id but
its `harness.json`:

  continuity/prompts/common.md          every audience
  continuity/prompts/audiences/<a>.md   the audience's mechanics
  personas/<role>.md                    the `## Domain judgment` section of the persona a live
                                        agent of the role reads (the Partner's:
                                        personas/partner.md); worker, Partner, subagent
  harness.json `purpose`                a worker's purpose
  companion/roles/<role>.md             a Companion's role file, as compose_system_prompt reads it
  continuity/policy.md, policy/<a>.md   operator policy

Only the persona's domain judgment: the rest of a persona is the live session's mechanics (its
tmux session, `/goal`, `## Tasks`), which the audience file replaces for a planned run and which
would not fit the native launch's 8000-byte initial budget beside it. Nothing else: not
`config/CLAUDE.md` (the live invariants; a planned run's shared rules are common.md and operator
policy, and its capsule's CLAUDE.md is the compaction instructions), not the learnings (the live
persona's region), not a `config/<id>/AGENTS.md` (gone: `hx.persona.migrate`).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import persona as persona_mod, store
from .ids import PARTNER
from .persona import LEGACY_HEADER
from .config_harness import load_harness, purpose_text, NO_SYSTEM_PROMPT_FLAVORS
from .continuity_store import canonical, digest
from .errors import ValidationError

SOURCE_BYTES = 65536
BUNDLE = Path(__file__).parent / "skeleton"
AUDIENCES = {"worker", "partner", "companion", "subagent"}
#: The persona section a planned prompt gets: the domain judgment, not the live mechanics.
DOMAIN_RE = re.compile(r"^## Domain judgment\n(?P<body>.*?)(?=^## |\Z)", re.S | re.M)


def _prefix(path, *, required=False, persona=False):
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
            # A persona installed before the learnings were the memory channel still ends
            # at the old header; nothing below it is prompt.
            if persona and line.rstrip(b"\r\n") == LEGACY_HEADER.encode():
                break
            chunks.append(line)
    try:
        return b"".join(chunks).decode("utf-8").strip("\n")
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


def build(root: Path, item_id: str, *, audience=None, persist=True):
    root = root.resolve()
    config = load_harness(root / "config" / item_id / "harness.json", check_cross_file=False)
    audience = audience or ("partner" if item_id == "partner" else "worker")
    if audience not in AUDIENCES or (audience == "partner") != (item_id == "partner") and audience not in {"companion", "subagent"}:
        raise ValidationError("prompt audience does not match the configured executor")
    values = persona_mod.identity_values(root, item_id)
    # The repository currently launches companions through the Claude adapter.
    runtime = "claude" if audience == "companion" else config.flavor
    channel = "context" if audience == "subagent" or runtime in NO_SYSTEM_PROMPT_FLAVORS else "system"
    branch = _branch(values["workdir"])
    identity = {"id": item_id, "pod": config.pod, "role": config.role, "audience": audience,
                "runtime": runtime, "executor_runtime": config.flavor,
                "model": ((config.companion or {}).get("model") or config.model) if audience == "companion" else config.model,
                "workdir": values["workdir"], "branch": branch}
    identity_text = "Session identity: " + canonical(identity) + ".\nThe current task or frozen pass supplies assignment-specific inputs; identity alone does not supply a goal."
    sections, sources, seen = [], [], set()
    def add(name, path, body):
        expanded = persona_mod.expand_identity(body, values)
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
                "activation": "pending_controller_installation",
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
