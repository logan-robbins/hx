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

Written by `hx install` (also with `--skeleton-only`), read by `adapters/claude/install.sh` when rendering hook commands and by `start.sh` for its JSON reads:

```json
{"hx_bin": "/abs/path/to/hx", "hook_bin": "/abs/path/to/hx-hook", "python_bin": "/abs/path/to/python3"}
```

When absent, `install.sh` falls back to `$HARNESS_ROOT/bin/hx` and `$HARNESS_ROOT/bin/hx-hook`.

## `templates/work-item.md` placeholders

The gtm lane writes the template; `hx dispatch` renders it (never reconstructs the body in
Python). Exactly these tokens, no others:

| Token | Renders as |
|---|---|
| `{{id}}` | the id |
| `{{pod}}` | the pod |
| `{{after}}` | the `after` ids comma-separated inside the `[...]` already in the template: `after: [eng-000, eng-002]`; `after: []` when empty |
| `{{dispatched}}` | the dispatch timestamp |
| `{{order}}` | the order file verbatim (`## Order`, then `## Definition of done` with its `### Checks` block), on its own line directly under the frontmatter |

## Claude Code version strings

`config/claude.json` is `{"bin": "<abs path>", "version": "2.1.278"}` and
`packaging/tested-claude-versions.json` is `{"versions": ["2.1.278", …]}`. Both hold the bare
version: the output of `claude --version` with the ` (Claude Code)` suffix stripped.

## Fresh instance contents

`hx install` creates exactly what spec 17.2 step 2 lists. No worker is installed. The example
worker configuration ships as `templates/worker/{AGENTS.md,SUBAGENTS.md,harness.json}` for the
Partner to copy into `config/<id>/` when it creates an agent; `hx doctor` and `hx board` must
not expect any id but `partner` in a fresh root.

## `hx orders --json` and `hx archive --json`

Read-only views for the Orders and Archive pages (spec 16.2); Partner and UI callers; exit 0 unless `errors` is non-empty. Adopted as the ui lane proposed:

**`hx orders --json`** — every `orders/*.md` and addendum with the `tasks.json` record it
produced, plus the `after` graph:

```json
{
  "root_abs": "/srv/hx",
  "ts": "2026-09-20T13:10:00Z",
  "orders": [
    {
      "id": "eng-002",
      "pod": "engineers",
      "path": "orders/eng-002.md",
      "after": ["eng-003"],
      "order": "…orders/eng-002.md verbatim…",
      "addenda": [{"ts": "…", "path": "orders/eng-002.addendum.md", "text": "…"}],
      "record": {"order": "…", "after": ["eng-003"], "addenda": [{"ts": "…", "text": "…"}],
                 "outcome": null, "dispatched": "…", "completed": null},
      "state": "queued",
      "ready": false,
      "waiting_on": ["eng-003"],
      "file_matches_record": true
    }
  ],
  "graph": {
    "nodes": [{"id": "eng-002", "state": "queued", "outcome": null, "ready": false}],
    "edges": [{"from": "eng-003", "to": "eng-002", "met": false}]
  },
  "errors": []
}
```

- `record` is the `tasks.json` entry for that id, which is exactly the `task` block of
  `hx show <id> --json` minus nothing — same six keys. `null` when the order file exists but
  was never dispatched, and then `state` and `file_matches_record` are `null` too.
- `waiting_on` is the subset of `after` whose `tasks.json` outcome is not `done`; `ready` is
  `waiting_on == []`, the same rule as the board. This is what spec 16.2 calls "which queued
  items wait on which ids".
- `file_matches_record` is `orders/<id>.md` on disk compared with the order recorded at
  dispatch. It is the one fact this view can show that nothing else can: the Partner edited the
  order file after dispatch, so what the agent is running is not what the file now says. If you
  would rather the UI not surface that, drop the key and we drop the badge.
- `edges` is one entry per `after` relation, in `orders` order; `met` mirrors `waiting_on`.

**`hx archive --json`** — benched bodies and archived dispatches per id:

```json
{
  "root_abs": "/srv/hx",
  "ts": "2026-09-20T13:10:00Z",
  "items": [
    {
      "id": "eng-001",
      "pod": "engineers",
      "bench":   [{"ts": "…", "path": "pods/engineers/archive/eng-001-<ts>.md", "digest": "…"}],
      "archive": [{"ts": "…", "path": "archive/eng-001/<ts>", "digest": "…"}]
    }
  ],
  "errors": []
}
```

- The `bench` and `archive` entries are the `hx show <id> --json` `bench` and `archive` entries
  verbatim — same three keys, same meaning — so this view is the whole-fleet form of what
  `hx show` already returns per id. An id with no history yet has two empty lists.

## SSE `changed` scopes

`/api/events` pushes `{"changed": [...]}` where every entry is an id (`partner` or `[a-z]+-[0-9]{3}`) except the reserved scope `tasks`, emitted when `tasks.json` changed. The browser treats `tasks` as "re-fetch the board and the orders view".

## `hx metrics <id> [--json]`

Spec 07.4 and 08. One entry per seam record in the main stream since `dispatched`; the
`metrics` object of `hx show --json` is exactly this document.

```json
{
  "id": "eng-001",
  "stream": "eng-001-main",
  "dispatched": "2026-09-20T12:00:00Z",
  "seams": [
    {
      "seq": 812,
      "ts": "2026-09-20T12:50:00Z",
      "source": "clear",
      "prompt_version": "base-3/engineer-2",
      "context_tokens_before": 91044,
      "context_file_bytes": 18422,
      "working_set_size": 9,
      "next_10_turns": {
        "turns": 10,
        "tool_calls": 14,
        "reads_of_context_file": 1,
        "reads_of_working_set": 2,
        "other": 11
      }
    }
  ],
  "totals": {"seams": 1, "tool_calls": 14, "reads_of_context_file": 1, "reads_of_working_set": 2, "other": 11}
}
```

`source` is one of `clear|compact|restart|resume|startup`. `next_10_turns.turns` is fewer than
10 when the stream ended sooner. `reads_of_context_file` must be 1 per seam (spec 13 M7);
`reads_of_working_set` is the waste metric. Text form: one line per seam with the same fields.

## `hx wake partner "<text>"` CLI form

The UI's only write path reads this, so it is a contract. Last stdout line is exactly one of:

```
HX-WAKE partner accepted
HX-WAKE partner no-socket
HX-WAKE partner refused
```

Exit 0 only for `accepted`; exit 3 for the other two (distinct from usage errors, which exit 2).
Callers inside hx (`hx complete`, `hx heartbeat`) treat a non-zero wake as a warning, never as
their own failure. `hx.wake.wake_partner(root, text) -> bool` is unchanged.

## `run/partner/socket.json`

Written by the `context` hook at every SessionStart of the Partner from the environment Claude
Code exports to hooks; read by `hx.wake`:

```json
{"socket": "/abs/path/to/socket", "token": "…", "ts": "2026-09-20T14:20:00Z", "session_id": "…"}
```

Only these four keys. `hx.wake.read_socket` may keep accepting the raw
`CLAUDE_CODE_MESSAGING_*` spelling for one milestone, then drops it.
