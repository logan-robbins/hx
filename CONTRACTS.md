# Contracts shared between lanes

Owned by the orchestrator. Propose changes in `handoff/to-orchestrator.md`. The build lane
implements these; the ui lane renders them; fixtures in `tests/ui/fixtures/` must validate
against them. Timestamps are ISO 8601 UTC with a `Z` suffix. Paths are relative to
`HARNESS_ROOT` unless the key ends in `_abs`. Absent values are `null`, never omitted.

## `hx board --json`

One entry per worker id, by id.

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
      "outcome": null,
      "dispatched": "2026-09-20T12:00:00Z",
      "completed": null,
      "open_subagents": 1,
      "goal_ts": "2026-09-20T12:00:03Z",
      "session_alive": true,
      "context_tokens": 48211,
      "seams": 2,
      "turn_ts": "2026-09-20T13:09:40Z"
    }
  ]
}
```

Exit 0 always (v1 cut: no invariants, no `errors`). `partner` is not an item; the Partner has no
work item. `state` is one of `idle|working|complete`, read from the work item's filename suffix
(`pods/<pod>/<id>-<state>.md`; renamed by `hx dispatch`, `hx complete`, `hx resume`, `hx bench`, or
by the agent itself; nothing validates or polices it).
`outcome` is one of `done|blocked|decision|exhausted` or `null`. `seams` counts seam records in `logs/<id>/<id>-main.jsonl` since
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
    "frontmatter": {"id": "eng-001", "pod": "engineers", "outcome": null, "dispatched": "…"},
    "body": "…the markdown body after the frontmatter, verbatim…"
  },
  "task": {
    "order": "…## Order text as dispatched…",
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

`tail` holds the last 50 records of the stream as parsed JSON objects. `hx show partner --json`
returns only `{"id": "partner", "partner_md": "…", "pane": {…}, "streams": […]}`: the Partner has
no work item, task, or step-state contract beyond its Companion's main stream.

## `hx wake partner "<text>"`

The UI calls the same function the CLI uses (`hx.wake.wake_partner(root, text)`), which
returns `True` when the socket accepted the message and `False` when no socket file exists or
the connection was refused. It never blocks and never retries.

## Orders (input files)

`hx dispatch <id> <order-file>` reads an order file from any path and deletes it after a
successful dispatch; the text then lives in exactly two places, `tasks.json` and the work item.
Required `## Order`; required `## Definition of done` containing a fenced ```bash block under
`### Checks`. No frontmatter. Spec 06.

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
| `{{dispatched}}` | the dispatch timestamp |
| `{{order}}` | the order file verbatim (`## Order`, then `## Definition of done` with its `### Checks` block), on its own line directly under the frontmatter |

## Claude Code version strings

`config/claude.json` is `{"bin": "<abs path>", "version": "2.1.278"}` and
`packaging/tested-claude-versions.json` is `{"versions": ["2.1.278", …]}`. Both hold the bare
version: the output of `claude --version` with the ` (Claude Code)` suffix stripped.

## Fresh instance contents

`hx install` creates exactly what spec 17.2 lists (no mirror, no worktrees, no unit files in v1). No worker is installed. The example
worker configuration ships as `templates/worker/{AGENTS.md,SUBAGENTS.md,harness.json}` for the
Partner to copy into `config/<id>/` when it creates an agent; `hx board` lists no items in a fresh root (the Partner is not an item).

## `hx orders --json` and `hx archive --json`

`hx orders --json` (v1 cut): one entry per id in `tasks.json`, no graph, no file comparison:

```json
{"root_abs": "/srv/hx", "ts": "…", "orders": [
  {"id": "eng-002", "pod": "engineers", "state": "complete", "outcome": "decision",
   "order": "…", "addenda": [{"ts": "…", "text": "…"}], "dispatched": "…", "completed": null}
]}
```

`hx archive --json`: unchanged shape, `{"root_abs", "ts", "items": [{"id", "pod", "bench": [...], "archive": [...]}]}`.

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

## `seed/token`

One line, the token printed by `claude setup-token`, pasted by the human; mode 0600, owned by
the harness user. `start.sh` reads it in its own process and exports it as `CLAUDE_CODE_OAUTH_TOKEN`
immediately before `exec` (never `tmux -e`, never on a command line, never in a file under `run/`;
`tmux show-environment` and `ps` do not show it). `install.sh` and `start.sh` refuse
when it is missing or its mode is wider than 0600. hx never reads `~/.claude`, any
`.credentials.json`, or the macOS Keychain. `--from-user-config` no longer exists.

## `run/<id>/home/.claude.json` (pre-seeded by `install.sh`)

Claude Code's per-config-dir state file. `install.sh` writes it before the first launch so
nothing about launch is interactive: onboarding marked complete and the workspace trust dialog
pre-accepted for the agent's cwd (`harness.json.workdir`, `HARNESS_ROOT` for `partner`). Keys verified read-only against a real accepted config and proved live (build-3):
`{"hasCompletedOnboarding": true, "projects": {"<abs cwd>": {"hasTrustDialogAccepted": true, "hasClaudeMdExternalIncludesApproved": true}}}`.
`install.sh` merges into an existing file, never resets it.
Found live 2026-09-20: without it the pane sits at "Quick safety check … Yes, I trust this folder".

## `turn` in `hx show --json`

Adds to the `hx show <id> --json` document:

```json
"turn": {"ts": "2026-09-20T13:09:40Z", "background_tasks": ["<claude task id>", "…"]}
```

From `run/<id>/turn` (written by the `stop` hook); `null` when no turn has ended. The board's
`turn_ts` is `turn.ts`. The UI shows a non-empty `background_tasks` as "stopped with work still
running".

## The Companion is a tmux session (no `claude -p` anywhere)

Every model call in hx is a Claude Code session in tmux operated by pasting. The Companion of
`<id>` runs in window `<id>:companion`, home `run/<id>/companion-home`, launched by
`start.sh <id> --companion` with `--dangerously-skip-permissions`, `IS_SANDBOX=1`,
`--model <companion.model>`, `--append-system-prompt-file run/<id>/companion-system.md`.

`hx companion <id> --wake <stream>` (the in-process `hx.companion.wake`) writes
`run/<id>/companion/<stream>.pass.md`:

```
# Companion pass
stream: eng-001-main
state: /abs/state/eng-001/eng-001-main.json        (absent on the first pass)
log: /abs/logs/eng-001/eng-001-main.jsonl
from_seq: 813
write: /abs/run/eng-001/companion/eng-001-main.out.json
retry_reason: <empty, or the validation failure of the previous attempt>
```

then pastes `/clear` and the fixed pointer
`Companion pass: read <abs pass path> and do what it says.` into the idle pane (queued for the
Companion's own `stop` hook when busy). The Companion's `stop` hook validates `out.json`
against the 07.2 schema, moves it to `state/<id>/<stream>.json` with `prompt_version`, `seq`,
`ts`, and removes the pass file; on failure it rewrites the pass with `retry_reason` and re-wakes
once. `tests/guard/test_no_headless.py` fails the build if `src/` invokes `claude` with `-p`.
