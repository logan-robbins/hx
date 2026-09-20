# Contracts shared between lanes

Owned by the orchestrator. Propose changes in `handoff/to-orchestrator.md`. The build lane
implements these; the ui lane renders them; fixtures in `tests/ui/fixtures/` must validate
against them. Timestamps are ISO 8601 UTC with a `Z` suffix. Paths are relative to
`HARNESS_ROOT` unless the key ends in `_abs`. Absent values are `null`, never omitted.

## `hx board --json`

Exit 0 when `errors` is empty, else 1 (same rule as the text form). One entry per id, `partner`
first, then by id.

```json
{
  "root_abs": "/srv/hx",
  "ts": "2026-09-20T13:10:00Z",
  "items": [
    {
      "id": "eng-001",
      "pod": "engineers",
      "role": "engineer",
      "state": "working",
      "file": "pods/engineers/eng-001-working.md",
      "after": ["eng-000"],
      "ready": true,
      "outcome": null,
      "dispatched": "2026-09-20T12:00:00Z",
      "completed": null,
      "open_subagents": 1,
      "goal_ts": "2026-09-20T12:00:03Z",
      "goal_pending": false,
      "session_alive": true,
      "context_tokens": 48211,
      "seams": 2,
      "turn_ts": "2026-09-20T13:09:40Z"
    }
  ],
  "errors": ["pods/engineers/eng-002-working.md: no live tmux session eng-002"]
}
```

`state` is one of `idle|queued|working|complete`. `outcome` is one of
`done|blocked|decision|exhausted` or `null`. `ready` is true when every `after` id has outcome
`done` in `tasks.json`. `seams` counts seam records in `logs/<id>/<id>-main.jsonl` since
`dispatched`. `context_tokens` is from the last main-stream record, `null` if none.

## `hx show <id> --json`

```json
{
  "id": "eng-001",
  "pod": "engineers",
  "role": "engineer",
  "state": "working",
  "file": "pods/engineers/eng-001-working.md",
  "work_item": {
    "frontmatter": {"id": "eng-001", "pod": "engineers", "after": [], "outcome": null, "dispatched": "…"},
    "body": "…the markdown body after the frontmatter, verbatim…"
  },
  "task": {
    "order": "…## Order text as dispatched…",
    "after": [],
    "addenda": [{"ts": "2026-09-20T12:30:00Z", "text": "…"}],
    "outcome": null,
    "dispatched": "…",
    "completed": null
  },
  "persona_path": "run/eng-001/persona.md",
  "step_state": {
    "eng-001-main": { "…the Companion's step-state JSON for that stream, spec 07…": 0 },
    "eng-001-s001": { "…": 0 }
  },
  "context_file": {
    "path": "run/eng-001/eng-001-main.context.md",
    "text": "…last composed context file, verbatim…",
    "seam_ts": "2026-09-20T12:50:00Z"
  },
  "streams": [
    {"handle": "eng-001-main", "path": "logs/eng-001/eng-001-main.jsonl", "open": true,
     "records": 812, "tail": [ {"…raw record…": 0} ]},
    {"handle": "eng-001-s001", "path": "logs/eng-001/eng-001-s001-closed.jsonl", "open": false,
     "records": 40, "tail": [], "digest": "…state/<id>/<stream>.digest.md text or null…"}
  ],
  "subagents": {"<claude agent_id>": "s001"},
  "metrics": { "…output of hx metrics for this id, spec 08…": 0 },
  "pane": {"session": "eng-001", "alive": true, "lines": ["…last 120 lines, ANSI stripped…"]},
  "archive": [
    {"ts": "2026-09-19T10:00:00Z", "path": "archive/eng-001/2026-09-19T10:00:00Z", "digest": "…"}
  ],
  "bench": [
    {"ts": "…", "path": "pods/engineers/archive/eng-001-2026-09-19T10:00:00Z.md", "digest": "…"}
  ]
}
```

`tail` holds the last 50 records of the stream as parsed JSON objects. For `partner`, the
object additionally carries `"partner_md": "…PARTNER.md text…"`.

## `hx wake partner "<text>"`

The UI calls the same function the CLI uses (`hx.wake.wake_partner(root, text)`), which
returns `True` when the socket accepted the message and `False` when no socket file exists or
the connection was refused. It never blocks and never retries.

## Orders (`orders/<id>.md`)

Optional YAML frontmatter with `after: [ids]`; required `## Order`; required
`## Definition of done` containing a fenced ```bash block under `### Checks`. Spec 06.

## `config/hx.json`

Written by `hx install`, read by `adapters/claude/install.sh` when rendering hook commands:

```json
{"hx_bin": "/abs/path/to/hx", "hook_bin": "/abs/path/to/hx-hook"}
```

When absent, `install.sh` falls back to `$HARNESS_ROOT/bin/hx` and `$HARNESS_ROOT/bin/hx-hook`.
