"""Prompt-cache accounting: keep invariants cached, restructure when cold.

The provider cache rewards a stable prefix (system prompt, persona,
learnings, tool schemas) and expires on a TTL — 5 minutes by default, one
hour when the harness configures ``cache_ttl: "1h"``. While the cache is
warm, changing the prefix pays a full rewrite, so context changes should be
append-only deltas. Once it is cold, restructuring costs nothing extra:
that is the moment to compress, reorder, or rebuild the context. This
module makes that limit explicit so seams and companion passes can choose.
"""
from __future__ import annotations

import json
import re

from .errors import ValidationError

DEFAULT_TTL_S = 300
MIN_CACHEABLE_TOKENS = 1024  # provider minimum prefix for a cache breakpoint


def ttl_seconds(value) -> int:
    """Parse a harness ``cache_ttl`` ("1h", "30m", "300", seconds) to seconds."""
    if value is None:
        return DEFAULT_TTL_S
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value <= 0:
            raise ValidationError("cache_ttl must be positive")
        return int(value)
    match = re.fullmatch(r"\s*(\d+)\s*([smh])?\s*", str(value))
    if not match:
        raise ValidationError(f"unparseable cache_ttl: {value!r}")
    amount = int(match.group(1))
    return amount * {"s": 1, "m": 60, "h": 3600}[match.group(2) or "s"]


def assess(*, prefix_bytes: int, age_s: float, ttl="5m") -> dict:
    """Cache state for an invariant prefix last written ``age_s`` ago.

    Token accounting follows hx convention: one UTF-8 byte is charged as
    one token until an adapter tokenizer is supplied (conservative).
    """
    if prefix_bytes < 0 or age_s < 0:
        raise ValidationError("prefix_bytes and age_s must be nonnegative")
    ttl_s = ttl_seconds(ttl)
    prefix_tokens = prefix_bytes
    cacheable = prefix_tokens >= MIN_CACHEABLE_TOKENS
    if not cacheable:
        state = "cold"
    elif age_s < ttl_s * 0.8:
        state = "warm"
    elif age_s < ttl_s:
        state = "expiring"
    else:
        state = "cold"
    recommendation = {"warm": "keep-prefix", "expiring": "defer-to-expiry",
                      "cold": "restructure-free"}[state]
    return {"state": state, "cacheable": cacheable,
            "prefix_tokens": prefix_tokens, "ttl_s": ttl_s,
            "age_s": age_s, "recommendation": recommendation,
            "note": "Warm: append deltas only, prefix edits pay a rewrite. "
                    "Cold: restructure freely, the rewrite is already owed."}


def invariant_budget(*, window: int, threshold: int) -> dict:
    """How much invariant prefix a model can carry under its seam threshold."""
    if window <= 0 or threshold <= 0 or threshold >= window:
        raise ValidationError("invariant budget needs 0 < threshold < window")
    return {"invariant_ceiling_tokens": threshold,
            "working_room_tokens": window - threshold,
            "note": "Invariants (persona, learnings, policy, tool schemas) must fit "
                    "under the seam threshold; the rest of the window is working room "
                    "for the current task, which is restructured at cold seams."}


# --- hx cache report (TOOL_SKILL_PLAN step 8) ----------------------------------------------------

#: What a report says about prefix parts hx cannot see from the filesystem: the adapter's own
#: tool schemas and whatever the host injects. Reported, never estimated.
UNMEASURED = "unmeasured"


def _file_bytes(path) -> int | None:
    try:
        return path.stat().st_size if path.is_file() else None
    except OSError:
        return None


def _persona(root, item_id: str) -> tuple[int | None, str | None, str]:
    """(bytes, source, text) of the persona in the agent's system prompt: `run/<id>/persona.md`
    once a launch derived it, else what `start.sh` would derive (AGENTS.md above the header)."""
    from .compile import HEADER

    derived = root / "run" / item_id / "persona.md"
    if derived.is_file():
        text = derived.read_text(errors="replace")
        return len(text.encode()), f"run/{item_id}/persona.md", text
    agents = root / "config" / item_id / "AGENTS.md"
    if agents.is_file():
        text = agents.read_text(errors="replace").partition(HEADER)[0]
        return len(text.encode()), f"config/{item_id}/AGENTS.md above the header", text
    return None, None, ""


def _skill_description_bytes(path) -> int:
    from .skill_manifest import parse_frontmatter

    try:
        fields, _ = parse_frontmatter(path.read_text(), path)
    except (OSError, ValidationError):
        return 0
    value = fields.get("description")
    return len(value.encode()) if isinstance(value, str) else 0


def _skills(root, item_id: str, env) -> tuple[dict, str]:
    """The skills whose descriptions sit in the agent's listing: the installed copies, or
    the manifest's choice for a home `install.sh` has not written yet."""
    from . import skill_manifest
    from .config_harness import flavor_of

    home = root / "run" / item_id / "home"
    installed = home / ("muse/skills" if flavor_of(root, item_id) == "meta" else "skills")
    if installed.is_dir():
        found = {p.name: p / "SKILL.md" for p in installed.iterdir() if (p / "SKILL.md").is_file()}
        return dict(sorted(found.items())), "installed"
    directory = skill_manifest.skills_dir(env)
    try:
        names = skill_manifest.for_agent(directory, root, item_id, env={})
    except ValidationError:
        names = []
    return {name: directory / name / "SKILL.md" for name in sorted(names)}, "manifest"


def _harness_json(root, item_id: str) -> dict:
    try:
        data = json.loads((root / "config" / item_id / "harness.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _last_seam_ts(root, item_id: str) -> str | None:
    from . import streams

    path = streams.main_stream(root, item_id)
    last = None
    if path.is_file():
        for record in streams.iter_records(path):
            if record.get("event") == "seam" and isinstance(record.get("ts"), str):
                last = record["ts"]
    return last


def agent_report(root, item_id: str, *, now: str | None = None, env=None) -> dict:
    """One agent's invariant prefix against its model's budget, and its last seam's cache age."""
    from pathlib import Path

    from . import learn, timestamps
    from .compile import GLOBAL_BEGIN, LEARNINGS_BEGIN
    from .config_models import load_models

    root = Path(root)
    persona_bytes, persona_source, persona_text = _persona(root, item_id)
    claude_md = _file_bytes(root / "config" / "CLAUDE.md")
    try:
        learnings = len(learn.persona_region(root, env).encode())
    except (ValidationError, OSError):
        learnings = None
    in_persona = LEARNINGS_BEGIN in persona_text
    # The invariants reach each agent once (`config_harness.INVARIANTS_CHANNEL`); on Pi and
    # Grok that once is inside the persona, so the file is not charged a second time.
    claude_md_in_persona = GLOBAL_BEGIN in persona_text
    skills, skills_source = _skills(root, item_id, env)
    descriptions = sum(_skill_description_bytes(path) for path in skills.values())
    prefix = ((persona_bytes or 0) + (0 if claude_md_in_persona else (claude_md or 0))
              + descriptions + (0 if in_persona else (learnings or 0)))
    harness = _harness_json(root, item_id)
    model = harness.get("model") if isinstance(harness.get("model"), str) else None
    budget = {"model": model, "invariant_ceiling_tokens": None, "working_room_tokens": None,
              "within": None, "share": None}
    try:
        row = load_models(root / "config" / "models.json").get(model) if model else None
    except ValidationError:
        row = None
    if row is not None:
        try:
            computed = invariant_budget(window=row.window, threshold=row.threshold)
        except ValidationError:
            computed = None
        if computed is not None:
            ceiling = computed["invariant_ceiling_tokens"]
            budget.update(invariant_ceiling_tokens=ceiling,
                          working_room_tokens=computed["working_room_tokens"],
                          within=prefix <= ceiling, share=round(prefix / ceiling, 4))
    seam = None
    seam_ts = _last_seam_ts(root, item_id)
    stamp = timestamps.parse(seam_ts) if seam_ts else None
    current = timestamps.parse(now or timestamps.now())
    if stamp is not None and current is not None:
        ttl, source = harness.get("cache_ttl"), "harness.json cache_ttl"
        if ttl is None:
            ttl, source = "5m", "default"
        try:
            ttl_seconds(ttl)
        except ValidationError:
            ttl, source = "5m", "default (harness.json cache_ttl is unparseable)"
        age = max(0, int((current - stamp).total_seconds()))
        seam = {"last_seam_ts": seam_ts, "cache_ttl": ttl, "ttl_source": source,
                **assess(prefix_bytes=prefix, age_s=age, ttl=ttl)}
        seam["age_s"] = age
    return {
        "id": item_id,
        "invariant": {
            "persona_bytes": persona_bytes, "persona_source": persona_source,
            "claude_md_bytes": claude_md, "claude_md_in_persona": claude_md_in_persona,
            "learnings_bytes": learnings, "learnings_in_persona": in_persona,
            "skills": list(skills), "skills_source": skills_source,
            "skill_descriptions_bytes": descriptions,
            "prefix_bytes": prefix,
            "native_tool_schemas": UNMEASURED, "host_context": UNMEASURED,
        },
        "budget": budget,
        "seam": seam,
    }


def report(root, item_id: str | None = None, *, now: str | None = None, env=None) -> dict:
    """`hx cache report [ID]`: every configured agent (`config/<id>/`), or one."""
    from pathlib import Path

    from . import timestamps
    from .errors import NotFound
    from .ids import ID_RE

    root = Path(root)
    config = root / "config"
    ids = sorted(p.name for p in config.iterdir() if p.is_dir() and ID_RE.match(p.name)) \
        if config.is_dir() else []
    if item_id is not None:
        if item_id not in ids:
            raise NotFound(f"unknown id: {item_id} (no config/{item_id}/)")
        ids = [item_id]
    return {"ts": now or timestamps.now(),
            "agents": [agent_report(root, i, now=now, env=env) for i in ids]}


def render_report(doc: dict) -> str:
    lines = []
    for row in doc["agents"]:
        inv, budget, seam = row["invariant"], row["budget"], row["seam"]
        share = "n/a" if budget["share"] is None else f"{budget['share']:.1%}"
        line = (f"{row['id']}: prefix {inv['prefix_bytes']} B (persona {inv['persona_bytes']}, "
                f"CLAUDE.md {inv['claude_md_bytes']}"
                f"{' in persona' if inv.get('claude_md_in_persona') else ''}, "
                f"learnings {inv['learnings_bytes']}"
                f"{' in persona' if inv['learnings_in_persona'] else ''}, "
                f"skill descriptions {inv['skill_descriptions_bytes']} over {len(inv['skills'])}) "
                f"= {share} of {budget['model'] or 'no model'} ceiling "
                f"{budget['invariant_ceiling_tokens']}; tool schemas and host context unmeasured")
        if seam is not None:
            line += (f"; last seam {seam['age_s']}s ago, {seam['state']} against "
                     f"{seam['cache_ttl']} ({seam['ttl_source']})")
        lines.append(line)
    over = sum(1 for row in doc["agents"] if row["budget"]["within"] is False)
    lines.append(f"HX-CACHE-REPORT agents={len(doc['agents'])} over_budget={over}")
    return "\n".join(lines) + "\n"


def main(argv, root, *, env=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="hx cache")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("assess", epilog="example: hx cache assess --prefix-bytes 42000 --age-s 240 --ttl 1h")
    p.add_argument("--prefix-bytes", type=int, required=True)
    p.add_argument("--age-s", type=float, required=True)
    p.add_argument("--ttl", default="5m")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p = sub.add_parser("budget", epilog="example: hx cache budget --window 1000000 --threshold 200000")
    p.add_argument("--window", type=int, required=True)
    p.add_argument("--threshold", type=int, required=True)
    p.add_argument("--root", help=argparse.SUPPRESS)
    p = sub.add_parser("report", help="invariant prefix per agent against its model's budget",
                       epilog="example: hx cache report eng-001 --json")
    p.add_argument("id", nargs="?", help="one agent (default: every config/<id>/)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "report":
        doc = report(root, args.id, env=env)
        print(json.dumps(doc, indent=2) if args.json else render_report(doc), end="\n" if args.json else "")
        return 0
    if args.command == "assess":
        result = assess(prefix_bytes=args.prefix_bytes, age_s=args.age_s, ttl=args.ttl)
    else:
        result = invariant_budget(window=args.window, threshold=args.threshold)
    print(json.dumps(result, indent=2))
    return 0
