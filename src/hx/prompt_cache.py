"""Prompt-cache accounting: keep invariants cached, restructure when cold.

The provider cache rewards a stable prefix (system prompt, persona,
learnings, tool schemas) and expires on a TTL — 5 minutes by default, one
hour when the instance profile sets ``seam.cache_ttl_s`` to 3600. While the cache is
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
    """Parse a cache lifetime ("1h", "30m", "300", seconds) to seconds."""
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


def _partner_report(root, item_id: str, *, now: str | None = None, env=None) -> dict:
    """One agent's invariant prefix against its model's budget, and its last seam's cache age."""
    from pathlib import Path

    from . import persona as persona_mod, timestamps
    from .config_harness import invariants_channel, persona_channel
    from .config_models import load_models
    from .errors import HxError

    root = Path(root)
    claude_md = _file_bytes(root / "config" / "CLAUDE.md")
    harness = _harness_json(root, item_id)
    role = harness.get("role") if isinstance(harness.get("role"), str) else None
    flavor = harness.get("flavor") if isinstance(harness.get("flavor"), str) else "claude"
    persona_file = root / "personas" / f"{role}.md" if role else None
    persona_bytes = _file_bytes(persona_file) if persona_file else None
    try:
        handed = persona_mod.boundary(root, item_id, env=env)
    except (HxError, OSError, ValueError):
        handed = None
    by_part: dict[str, int] = {}
    for name, _source, size in (handed or {}).get("parts", []):
        by_part[name] = by_part.get(name, 0) + size
    # Each source reaches the agent once, by its channel (`config_harness.INVARIANTS_CHANNEL`,
    # `PERSONA_CHANNEL`): counted once here whichever it is. The rules count as the hook prints
    # them — whole, or as the list of files the bound made of them.
    learnings = (by_part.get("rules", 0) + by_part.get("as-files", 0)) if handed else None
    identity = by_part.get("identity", 0)
    skills, skills_source = _skills(root, item_id, env)
    descriptions = sum(_skill_description_bytes(path) for path in skills.values())
    prefix = (claude_md or 0) + (persona_bytes or 0) + identity + (learnings or 0) + descriptions
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
        from .loop_jobs import profile
        from .token_controls import seam_profile
        configured = "cache_ttl_s" in (profile(root).get("seam") or {})
        ttl = f"{seam_profile(root)['cache_ttl_s']}s"
        source = "config/hx.json seam.cache_ttl_s" if configured else "default"
        age = max(0, int((current - stamp).total_seconds()))
        seam = {"last_seam_ts": seam_ts, "cache_ttl": ttl, "ttl_source": source,
                **assess(prefix_bytes=prefix, age_s=age, ttl=ttl)}
        seam["age_s"] = age
    return {
        "id": item_id,
        "invariant": {
            "claude_md_bytes": claude_md, "claude_md_channel": invariants_channel(flavor),
            "persona_bytes": persona_bytes,
            "persona_source": f"personas/{role}.md" if persona_bytes is not None else None,
            "persona_channel": persona_channel(flavor),
            "identity_bytes": identity, "learnings_bytes": learnings,
            "learnings_as_files": list(handed["as_files"]) if handed else [],
            "hook_bytes": handed["bytes"] if handed else None,
            "hook_bound": handed["bound"] if handed else None,
            "skills": list(skills), "skills_source": skills_source,
            "skill_descriptions_bytes": descriptions,
            "prefix_bytes": prefix,
            "native_tool_schemas": UNMEASURED, "host_context": UNMEASURED,
        },
        "budget": budget,
        "seam": seam,
    }


def _frozen_json(path, *, expected_hash=None, limit=65536):
    """Read a retained runtime input, never rebuild it from current instructions."""
    import hashlib
    from pathlib import Path
    with Path(path).open('rb') as source:
        data=source.read(limit+1)
    if len(data)>limit:
        raise ValidationError('frozen cache report input exceeds its bound')
    if expected_hash and hashlib.sha256(data).hexdigest()!=expected_hash:
        raise ValidationError('frozen native configuration was altered')
    return json.loads(data)


def _worker_report(root, item_id, *, env=None):
    import hashlib
    from pathlib import Path
    from .board import ledger_reader, rows
    from .continuity_store import digest
    from .config_models import load_models
    from .config_harness import load_harness

    result={'id':item_id,'kind':'packet','status':'unassigned','run':None,'launch':None,
            'invariant':{'prefix_bytes':None,'native_tool_schemas':UNMEASURED,'host_context':UNMEASURED},
            'budget':{'model':None,'invariant_ceiling_tokens':None,'working_room_tokens':None,
                      'within':None,'share':None},'checkpoint':None,'tools':None,
            'context_usage':UNMEASURED,'provider_usage':[],
            'cache':{'state':UNMEASURED,'note':'Provider cache residency is not inferred from checkpoint or file timestamps.'}}
    with ledger_reader(root) as db:
        if db is None:
            return result
        selected=rows(db,'''SELECT r.run_id,r.task_id,r.task_revision,r.phase,r.ended_at,
            l.launch_id,l.status AS native_status,l.payload
            FROM runs r JOIN unit_runs u USING(run_id)
            JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision
            LEFT JOIN native_launches l USING(run_id)
            WHERE r.worker_id=? AND json_extract(t.payload,'$.dependency_packet')=1
            ORDER BY r.ended_at IS NOT NULL,r.started_at DESC,l.rowid DESC LIMIT 1''',(item_id,))
        if not selected:
            return result
        row=selected[0]
        result['run']={key:row[key] for key in ('run_id','task_id','task_revision','phase','ended_at')}
        if row['launch_id'] is None:
            result['status']='awaiting_launch'
            return result
        payload=row['payload']
        result['status']=row['native_status']
        result['launch']={key:row[key] for key in ('launch_id','native_status')}
        result['launch'].update(prompt_version=payload['prompt_version'],
            instruction_delivery=payload.get('instruction_delivery'),
            initial_input_limit=payload.get('initial_input_limit'),
            charged_initial_tokens=payload.get('charged_initial_tokens'),
            accounting='conservative utf8-byte charge; not provider token usage')
        checkpoint=db.execute('''SELECT c.checkpoint_id,c.packet_hash,c.task_revision,c.acknowledged,c.payload,a.size
            FROM checkpoints c JOIN artifacts a ON a.hash=c.packet_hash
            WHERE c.checkpoint_id=? AND c.run_id=?''',(payload.get('checkpoint_id'),row['run_id'])).fetchone()
        if checkpoint:
            metadata=json.loads(checkpoint['payload'])
            result['checkpoint']={key:checkpoint[key] for key in ('checkpoint_id','packet_hash','task_revision','acknowledged','size')}
            result['checkpoint'].update(charged_tokens=metadata['charged_tokens'],tokenizer=metadata['tokenizer'])
        usage=rows(db,'''SELECT binding_id,stream_id,session_id,decoder,latest_usage
            FROM native_bindings WHERE run_id=? ORDER BY binding_id LIMIT 257''',(row['run_id'],))
        result['provider_usage']=[{**entry,'latest_usage':json.loads(entry['latest_usage']) if entry['latest_usage'] else UNMEASURED}
                                  for entry in usage[:256]]
        result['provider_usage_truncated']=len(usage)>256
        result['provider_usage_scope']='Latest captured usage per binding; not summed across attempts, sessions or children.'
        tokens=db.execute('SELECT payload FROM runtime_cycles WHERE scope=?',('tokens:'+row['launch_id'],)).fetchone()
        if tokens:
            result['context_usage']=json.loads(tokens[0])
        tools=payload.get('tool_selection')
        result['tools']={'launch_selection':tools,'native_schema_tokens':UNMEASURED,
                         'visibility':payload.get('tool_visibility',UNMEASURED)}
    capsule=Path(payload['capsule'])
    manifest=_frozen_json(payload['manifest'])
    base={key:value for key,value in manifest.items() if key not in {'version','system_path','context_path'}}
    if manifest.get('version')!=payload['prompt_version'] or digest(base)!=payload['prompt_version']:
        raise ValidationError('frozen prompt manifest identity changed')
    for channel in ('system','context'):
        path=Path(manifest[channel+'_path'])
        with path.open('rb') as source:
            data=source.read(262145)
        expected=manifest['delivery'][channel]
        if len(data)!=expected['bytes'] or hashlib.sha256(data).hexdigest()!=expected['sha256']:
            raise ValidationError('frozen prompt channel was altered')
    from .prompt_compiler import _verify_required_file
    from .instruction_materials import read_bundle
    required_files=[]
    for material in manifest['required_files']:
        _verify_required_file(material)
        required_files.append({**material,'integrity':'verified','read_status':'not_required_inline'})
    read_bundle(manifest['required_files'])
    sizes={channel:manifest['delivery'][channel]['bytes'] for channel in ('system','context')}
    prefix=sum(sizes.values())
    result['invariant'].update(prefix_bytes=prefix,prompt_version=payload['prompt_version'],
        channels=manifest['delivery'],sections=manifest['sections'],inline_bytes=prefix,
        referenced_full_text_bytes=0, backing_material_bytes=sum(material['payload_bytes'] for material in required_files),
        required_files=required_files,reference_read_status='not_required_inline',
        accounting='Full frozen instructions are delivered inline once; linked materials are verified backing, with no required model read. Checkpoint, tool schemas and host additions are accounted separately or unmeasured.')
    for relative in (f'config/{item_id}/harness.json','config/models.json'):
        _frozen_json(capsule/relative,expected_hash=payload['configuration'][relative])
    harness=load_harness(capsule/'config'/item_id/'harness.json',check_cross_file=False)
    model=load_models(capsule/'config/models.json')[harness.model]
    computed=invariant_budget(window=model.window,threshold=model.threshold)
    ceiling=computed['invariant_ceiling_tokens']
    result['budget'].update(model=harness.model,invariant_ceiling_tokens=ceiling,
        working_room_tokens=computed['working_room_tokens'],within=False if prefix>ceiling else None,
        measured_prefix_share=round(prefix/ceiling,4),
        note='Known static bytes are a lower bound; total prefix and provider cache state remain unmeasured.')
    return result


def agent_report(root, item_id, *, now=None, env=None):
    if item_id=='partner':
        return {'kind':'partner',**_partner_report(root,item_id,now=now,env=env)}
    return _worker_report(root,item_id,env=env)


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
        if row['kind']=='packet':
            run=row['run'] or {}
            checkpoint=row['checkpoint'] or {}
            lines.append(f"{row['id']}: {row['status']} run={run.get('run_id','-')} "
                         f"static_prefix={row['invariant']['prefix_bytes']} B "
                         f"checkpoint={checkpoint.get('checkpoint_id','-')} "
                         f"model={row['budget']['model'] or 'unmeasured'}; "
                         "tool schemas, host context and provider cache residency unmeasured")
            continue
        inv, budget, seam = row["invariant"], row["budget"], row["seam"]
        share = "n/a" if budget["share"] is None else f"{budget['share']:.1%}"
        line = (f"{row['id']}: prefix {inv['prefix_bytes']} B (CLAUDE.md {inv['claude_md_bytes']} "
                f"via {inv['claude_md_channel']}, persona {inv['persona_bytes']} via "
                f"{inv['persona_channel']}, identity {inv['identity_bytes']}, "
                f"learnings {inv['learnings_bytes']}"
                f"{' as files' if inv['learnings_as_files'] else ''}, "
                f"skill descriptions {inv['skill_descriptions_bytes']} over {len(inv['skills'])}; "
                f"hook {inv['hook_bytes']} of {inv['hook_bound']} B) "
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
