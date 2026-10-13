# Contracts

Fixtures in `tests/ui/fixtures/` must validate against these. Timestamps are ISO 8601 UTC
with a `Z` suffix. Paths are relative to `HARNESS_ROOT` unless the key ends in `_abs`.
Absent values are `null`, never omitted.

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
      "needs_input": false,
      "context_tokens": 48211,
      "seams": 2,
      "turn_ts": "2026-09-20T13:09:40Z",
      "companion_pass": false,
      "companion_ts": "2026-09-20T13:09:35Z",
      "scope": "Add a --json flag to hx doctor."
    }
  ],
  "memory": {"episodes": 12, "queued": 1, "indexed_ts": "2026-09-20T13:05:00Z"}
}
```

`scope` is the goal's first content line (from `tasks.json`, else the work item's
`## Goal`), capped at 160 characters, `null` when the id has no goal. It is
machine-derived so the Partner sees what each stream is building without spending a
read per worker. The text form carries the same as trailing
`scope <id>: <text>` lines after the status rows.

`companion_pass` is true while a pass file sits in `run/<id>/companion/` (hx wrote it, the
Companion has not yet answered); `companion_ts` is the newest `ts` on any of the id's step states,
`null` before the first. `memory` is the episode store's summary (`docs/memory.md`): `episodes`
as of the last `hx memory index` (`state/memory/stats.json`, `null` before the first), `queued`
episodes waiting to be indexed, and when the last index ran. It never opens the store.

Exit 0 always (v1 cut: no invariants, no `errors`). `partner` is not an item; the Partner has no
work item. `state` is one of `idle|working|complete`, read from the work item's filename suffix
(`pods/<pod>/<id>-<state>.md`; renamed by `hx dispatch`, `hx complete`, `hx resume`, `hx bench`, or
by the agent itself; nothing validates or polices it).
`outcome` is one of `done|blocked|decision|exhausted` or `null`. `seams` counts seam records in `logs/<id>/<id>-main.jsonl` since
`dispatched`. `context_tokens` is from the last main-stream record, `null` if none.
`needs_input` is true while a live session's pane tail carries a known awaiting-human marker
(approval, billing, login — `INPUT_PATTERNS` in `src/hx/board.py`); false for dead sessions.
An item can be `idle` with a non-null `outcome`: a killed session leaves its file, and `hx bench`
clears such an item when the outcome is `done` and the session is dead.

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
    "goal": "…## Goal text as dispatched…",
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
returns only `{"id": "partner", "partner_md": "…", "pane": {…}, "streams": […], "companion": {…},
"compactions": {…}}`: the Partner has no work item or task; its Companion's compactions are shown
like any other agent's.

Every `hx show` document carries its Companion's visible activity:

```json
"companion": {"pass_in_flight": true, "pass_stream": "eng-001-main",
              "pass_since": "2026-09-20T13:09:41Z", "last_state_ts": "2026-09-20T13:09:35Z",
              "streams": 2}
```

`pass_in_flight` and `pass_stream` come from the pass file in `run/<id>/companion/`, `pass_since`
is its mtime, `last_state_ts` the newest step state's `ts`, `streams` how many streams have one.

Every `hx show` document also carries the Companion's last written compaction per stream, the
installed `state/<id>/<stream>.json` rendered exactly as `hx compose` puts it in front of the
master (one tagged line per fact, spec 07.2):

```json
"compactions": {"eng-001-main": {"path": "state/eng-001/eng-001-main.json",
                                 "ts": "2026-09-20T13:09:35Z", "seq": 412,
                                 "text": "goal: …\ncon: …\nopen st7 … next …\nseq 412 ts …"}}
```

A stream whose state file is unreadable is left out. The UI's Compaction page
(`#compaction?agent=<id>&stream=<handle>`) is this block and nothing else.

## `hx wake partner "<text>"`

The UI calls the same function the CLI uses (`hx.wake.wake_partner(root, text)`), which
returns `True` when the socket accepted the message and `False` when no socket file exists or
the connection was refused. It never blocks and never retries. A Partner that does not run
on Claude has no socket: the wake is pasted into its tmux pane instead, and `False` means
there was no pane to paste into or the paste failed.

## Goals (input files)

`hx dispatch <id> <goal-file>` reads a goal file from any path and deletes it after a
successful dispatch; the text then lives in exactly two places, `tasks.json` and the work item.
Required `## Goal`; required `## Definition of done` containing a fenced ```bash block under
`### Checks`. No frontmatter. Spec 06.

Before the goal is sent, `hx dispatch` runs that block with `bash -e` in the id's workdir
(`HARNESS_ID` set to the id, 120 s budget) and prints one line per id ahead of `HX-DISPATCH`:

| Result | stdout | Dispatch |
|---|---|---|
| exit 0 | `HX-GATE-EMPTY <id>` and why, exit 1 | refused for every id; nothing written, goal files kept |
| non-zero exit `n` | `gate preflight: exit <n> [<id>], first FAIL line: <line>` (`no FAIL line in the output` when there is none) | proceeds |
| budget spent | `gate preflight: not judged after 120s [<id>]`; the block's process group is killed | proceeds |

## Addenda (input files)

`hx resume <id> <file>` and `hx amend <id> <file>` take an addendum file and delete it after a
successful run. Its text is appended as `### Goal addendum <ts>` under the work item's
`## Goal` and as `{"ts", "text"}` to `tasks.json[<id>].addenda`. An addendum that carries its own
`### Checks` heading (outside a fence) with a fenced ```bash block replaces the Checks fence, fence
markers included, in `tasks.json[<id>].goal` and in the work item's `## Definition of done`,
once the amended goal passes the goal parser; a heading with no usable block, a comment-only
block, or two such headings is refused with nothing written. `hx amend` works on a `working` or
`complete` item, changes no state and pastes nothing, and prints
`HX-AMEND <id> checks=replaced|kept addenda=<n>` (`n` = addenda recorded after it). `hx resume`
prints the same line ahead of its `HX-RESUME` line.

## `config/partner/guard.json`

Read on every call by the Partner's `guard` hook (`hx-hook --id partner guard`, Claude Code
`PreToolUse`, matcher `Bash|Read|Edit|Write|Grep|Glob`; spec 09.1). Written by the human or the
Partner, never by hx:

```json
{"deny_paths": ["/abs/product/tree"],
 "deny_commands": ["\\bdotnet\\b", "\\bpytest\\b", "\\bgit\\s+grep\\b"],
 "allow_commands": ["^hx "]}
```

All three keys are optional lists of strings; `deny_paths` entries are absolute (`~` expands);
the other two are Python regexes, `re.search`ed against each simple command of a `Bash` call.
Exit 2 with one `hx guard: denied: …` line on stderr denies; exit 0 with no output allows. An
absent or invalid file (not JSON, not an object, an unknown key, a bad regex, a relative root)
means the guard is inactive; that, and every denial, is appended to `logs/partner/guard.log`.

## `config/hx.json`

Written by `hx install` (also with `--skeleton-only`), read by `adapters/claude/install.sh` when rendering hook commands and by `start.sh` for its JSON reads:

```json
{"hx_bin": "/abs/path/to/hx", "hook_bin": "/abs/path/to/hx-hook", "python_bin": "/abs/path/to/python3"}
```

When absent, `install.sh` falls back to `$HARNESS_ROOT/bin/hx` and `$HARNESS_ROOT/bin/hx-hook`.

Two optional keys, read by `hx learn`, `hx compile`, `hx heartbeat` and `hx doctor`
(docs/learnings.md): `"learnings"` — the absolute path of the learnings directory this
instance writes to (default `$HARNESS_ROOT/LEARNINGS`; a fleet names one shared directory; a
value ending in `.md` is the pre-directory layout and is refused), and `"learnings_prefix"`
— the rule prefix letter(s) a proposal gets by default (default `L`).
`HX_LEARNINGS` and `HX_LEARNINGS_PREFIX` in the environment override both.

One optional key read by every adapter's `install.sh` (through `python -m hx.skill_manifest
select`) and by `hx doctor`: `"skills"` — a list of the optional skill names this instance
turns on, e.g. `"skills": ["hx-conversion"]`. Absent means none. A name that does not ship, or
names a skill that is not `optional` in `src/hx/skills/manifest.json`, fails the doctor
`skills` row. `hx install` rewrites only the three entry points and keeps every other key.

## `hx learn` (central learnings)

The directory holds one file per fixed category — `orientation`, `ownership`, `planning`,
`domain`, `build`, `testing`, `runtime`, `evidence`, `delivery`, `harness`, `environment`
(`hx.learn.CATEGORIES`, `hx learn categories`) — each a fixed header, `## Rules`, and rule
lines. A rule line: `**Rule <P><n>** · Signal: … · Check: … · Evidence: …`, prefix 1–3
capitals, numbers unique per prefix across every file, each cell one line; the category is the
file, never part of the key. A proposal (`LEARNINGS/proposals/<id>.json`):

```json
{"id": "20261011T183601-eng-001-057487", "ts": "2026-10-11T18:36:01Z", "author": "eng-001",
 "instance": "mc", "role": "qa-engineer", "prefix": "T", "category": "evidence",
 "signal": "…", "check": "…", "evidence": "…", "evidence_for": null,
 "learnings": "/abs/LEARNINGS",
 "nearest": [{"rule": "T3", "score": 0.71, "signal": "…", "category": "delivery"}]}
```

A decision (`LEARNINGS/decisions.jsonl`, one per line): `{"ts", "proposal", "author",
"reviewer", "reviewer_instance", "decision": "new|merged|rejected", "rule": "T5"|null,
"category", "why"}`. The accepted or rejected proposal moves to `LEARNINGS/accepted/` or
`LEARNINGS/rejected/` with `decision`, `rule`/`why`, `category`, `reviewer` and `decided`
added. Output lines: `HX-LEARN-PROPOSED <id> category=<c> nearest=<rule:score,…>`,
`HX-LEARN-PENDING <n>`, `HX-LEARN new|merged <id> rule=<key> category=<c>`,
`HX-LEARN rejected <id>`, `HX-LEARN-CHECK rules=<n> pending=<p> fails=<f>`.

The persona region (`hx compile`, `hx.learn.persona_region`) carries every rule line whole
under `### <category> — <definition>` headings in taxonomy order; `hx doctor` warns
`learnings-size` above `hx.learn.PERSONA_WARN_BYTES` (200 KiB).

## `templates/work-item.md` placeholders

The gtm lane writes the template; `hx dispatch` renders it (never reconstructs the body in
Python). Exactly these tokens, no others:

| Token | Renders as |
|---|---|
| `{{id}}` | the id |
| `{{pod}}` | the pod |
| `{{dispatched}}` | the dispatch timestamp |
| `{{goal}}` | the goal file verbatim (`## Goal`, then `## Definition of done` with its `### Checks` block), on its own line directly under the frontmatter |

## Claude Code version strings

`config/claude.json` is `{"bin": "<abs path>", "version": "2.1.278"}` and
`packaging/tested-claude-versions.json` is `{"versions": ["2.1.278", …]}`. Both hold the bare
version: the output of `claude --version` with the ` (Claude Code)` suffix stripped.

## Fresh instance contents

`hx install` creates exactly what spec 17.2 lists (no mirror, no worktrees, no unit files in v1). No worker is installed. The example
worker configuration ships as `templates/worker/{AGENTS.md,SUBAGENTS.md,harness.json}` for the
Partner to copy into `config/<id>/` when it creates an agent; `hx board` lists no items in a fresh root (the Partner is not an item).

## `hx goals --json` and `hx archive --json`

`hx goals --json` (v1 cut): one entry per id in `tasks.json`, no graph, no file comparison:

```json
{"root_abs": "/srv/hx", "ts": "…", "goals": [
  {"id": "eng-002", "pod": "engineers", "state": "complete", "outcome": "decision",
   "goal": "…", "addenda": [{"ts": "…", "text": "…"}], "dispatched": "…", "completed": null}
]}
```

`hx archive --json`: unchanged shape, `{"root_abs", "ts", "items": [{"id", "pod", "bench": [...], "archive": [...]}]}`.

## `hx read` and `hx recall`

`hx read <id>` is the trust-model view: status only (`<id> <state> <outcome>`, dispatched,
completed, file), no prose. `--detail` adds the `## Digest` and `## Open decision` sections
for `blocked`/`decision` follow-ups; `--full` prints the whole body. A non-`complete` item
is refused except with `--full`.

`hx recall [QUERY] [--id ID] [--pod POD] [--limit N] [--full]` is last-resort file memory:
substring search over completed Work Item bodies (live `*-complete.md` plus
`pods/<pod>/archive/`), never the vector store. A query or a filter is required; at most 50
files are scanned newest-first; `--limit` defaults to 5, max 20; excerpts are capped (400
chars, 2000 with `--full`). Output lines start with `HX-RECALL`, or a single `HX-RECALL none`.

## `hx code`, `hx content`, `hx exec`, `hx sift` (search and retrieval)

All four print JSON to stdout and exit 0 on success (docs/search-and-retrieval.md).
`hx code index` returns `{"files": N, "symbols": N}`; `code search` returns a list of
`{"path", "title", "snippet", "score", "layer"}` where `layer` is `symbol`, `fts`, or
`fuzzy`; `code symbols` returns `{"name", "qualified", "kind", "path", "line", "end_line"}`
entries; `code impact` returns defined symbols, referencing files, and candidate tests;
`code edges` returns `{"from_path", "to_path", "kind": "imports", "status": "candidate"}`.
`hx content index` returns `{"label", "chunks", "code_chunks"}`; `content search` returns a
map of query to `{"title", "content", "source", "content_type", "rank", "layer", "stale"}`
lists. `hx exec` returns `{"exit_code", "stdout_returned", "bytes_produced", "indexed", ...}`;
output over 100 KB returns an index pointer with no raw text. `hx sift candidates` returns
`{"status", "sifted", "results": [{"id", "snippet", "jev_score", "candidate"}]}`; on provider
failure `status` is `unsifted` and every `jev_score` is null. Sift and triage output is
advisory: it never validates edges, receipts, or completion.

## SSE `changed` scopes

`/api/events` pushes `{"changed": [...]}` where every entry is an id (`partner` or `[a-z]+-[0-9]{3}`) except two reserved scopes: `tasks`, emitted when `tasks.json` changed, and `memory`, emitted when anything under `state/memory/` moved (an episode queued or indexed). The browser treats `tasks` as "re-fetch the board and the goals view"; every scope re-reads the board, which is where `memory` and `companion_pass` land. An id also moves when a pass file appears or disappears under `run/<id>/companion/`.

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

Turns come from the harness transcript the post-seam boundary record references
(Claude-flavor JSONL; first 10 assistant turns at or after the seam timestamp).
When no parseable transcript exists the whole `next_10_turns` object is `null`
fields — a missing window is reported, never approximated. `totals` sums the
non-null windows; `totals.seams` always counts seam records.

## `hx metrics [--json]` (fleet rollup)

No id: one entry per board item plus `summary`, `partner` and `graph`:

```json
{
  "root_abs": "/home/u/hx-demo",
  "ts": "2026-10-04T23:00:00Z",
  "agents": [
    {
      "id": "eng-001", "pod": "eng", "role": "engineer", "state": "working",
      "file": "pods/eng/eng-001-working.md", "outcome": None,
      "dispatched": "2026-10-04T18:23:23Z", "completed": None,
      "open_subagents": 0, "goal_ts": "2026-10-04T23:19:43Z",
      "session_alive": true, "needs_input": false, "context_tokens": 225313,
      "seams": 2, "turn_ts": "2026-10-04T23:24:07Z",
      "companion_pass": false, "companion_ts": None,
      "scope": "first line of the goal…",
      "harness": {"flavor": "claude", "model": "claude-opus-5-5",
                  "effort": "xhigh", "workdir": "/data/worktrees/hx-eng-001"},
      "ties": {"mentions": ["qa-056"]},
      "links": {"work_item": "pods/eng/eng-001-working.md",
                "harness": "config/eng-001/harness.json",
                "context": "run/eng-001/eng-001-main.context.md",
                "stream": "logs/eng-001/eng-001-main.jsonl",
                "pane_log": "logs/eng-001/eng-001-pane.log",
                "goal_marker": "run/eng-001/goal"},
      "limit_paused": false
    }
  ],
  "summary": {"agents": 274, "working": 36,
              "by_pod": {"eng": {"working": 36, "idle": 57}},
              "paused_limit": ["eng-001"], "needs_input": [], "dead": []},
  "partner": {"seq": 11191, "ts": "2026-10-04T23:00:20Z", "event": "seam_waiting"},
  "graph": None
}
```

The agent entry starts as the `hx board --json` item verbatim, then `harness`
(best-effort `config/<id>/harness.json`), `ties.mentions` (id mentions in goal
and addenda text, minus self), `links` (relative paths, `null` when the file is
absent) and `limit_paused` (pane tail matches a usage-limit marker; `false`
when the session is dead). `graph` is always `null` from the product: the
instance overlay fills it (see below). Text form: one line per agent, then
summary, paused-limit, needs-input, dead and partner lines.

## `hx metrics --watch[=SECS]` (JSONL stream)

For the UI and live dashboards. First object is
`{"type": "snapshot", "ts": …, "fleet": {…the fleet document…}}`, then one
`{"type": "delta", "ts": …, "changed": […], "agents": […], "summary": …,
"partner": …}` per re-scan that moved anything. `changed` holds agent ids plus
the `tasks` and `partner` pseudo-scopes (same vocabulary as SSE `changed`).
Fast ticks compare mtime fingerprints; pane-only changes (death, limit banner,
input prompt with no file write behind it) surface on the slow tick (30s).
`agents` carries full entries for changed ids only; `summary` and `partner`
ride every delta.

## Instance graph overlay (UI composition)

The UI's lit-nodes layer is instance-domain, not product: the instance ships a
small reader (for DFA: `hx-dfa/tools/graph-status.py --json`) that joins the
fleet document with its own graph source and emits
`{"nodes": [{"id": …, "active": true, "owners": […], "kind": …}], "edges": …}`.
The browser composes board + metrics + overlay; `hx metrics --json` leaves
`graph` null so no product code depends on instance schema.

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
`--model <companion.model>`, `--append-system-prompt-file run/<id>/companion-system.md`, `--setting-sources user`.

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

## `hx memory` (episode memory)

Design and operating notes: `docs/memory.md`. Store `state/memory/chroma`, collection
`episodes`, global to the instance; queue `state/memory/queue/<uuid>.json`; lock
`state/memory/index.lock` (an exclusive `flock` around every chroma open, reads included).

Queue file — what the four write points (`companion.ingest` `pass`, `seam.seam` `seam`,
`hook_compact.post` `compact`, `complete.complete` `complete`) write, and what `index` consumes:

```json
{
  "episode_id": "eng-001/eng-001-main/seam/41",
  "document": "goal: …\ndecision: … why=…\nclosed st1: … (verified) a1b2c3d\ndead end: …",
  "metadata": {
    "ts": "2026-09-21T12:05:05Z", "t": 1789992305.116019,
    "id": "eng-001", "pod": "engineers", "role": "backend-engineer",
    "stream": "eng-001-main", "kind": "seam", "seq": 41,
    "outcome": "", "prompt_version": "a1b2c3d4/e5f6a7b8"
  }
}
```

Exactly those ten metadata keys, all flat scalars (chroma stores nothing else); `kind` is one of
`pass`, `seam`, `compact`, `complete`; `outcome` is non-empty only for `complete`;
`prompt_version` is `{base}/{role}` from the step state's `prompt_version` object. The
`episode_id` is `<id>/<stream>/<kind>/<seq>` and indexing upserts, so re-indexing is idempotent.

```
hx memory search QUERY [--role R] [--pod P] [--id ID] [--kind K] [--k N=5]
                       [--all-roles] [--half-life-h H=24] [--json]
hx memory index
hx memory list [--id ID] [--role R] [--kind K] [--limit N=20] [--json]
hx memory stats [--json]
```

`search` defaults the role filter to the caller's own persona (`HX_ROLE`, else the `role` of
`HARNESS_ID`, else no filter); `--all-roles` drops it. Ranking is
`score = (1 - cosine_distance) * (0.5 + 0.5 * 0.5 ** (age_hours / half_life_hours))`.

Text output, one block per hit:

```
## 2026-09-21T12:05:05Z eng-001 backend-engineer seam seq=41 score=0.68
goal: make the CSV importer stream instead of loading the whole file
…
```

`--json` is a list of `{id, ts, t, role, pod, item, stream, kind, seq, outcome, score,
similarity, document}` — `id` is the episode id, `item` is the agent. No match prints
`HX-MEMORY no episodes matched` and exits 0. `index` prints `HX-MEMORY indexed=<n> total=<count>`.
`stats` prints `HX-MEMORY total=<n> queued=<n> path=<abs>` then indented `role|kind|id <name>: <n>`
lines. `list` prints `<ts> <id> <role> <kind> seq=<n> <stream>[ <outcome>]`, newest first.
chromadb missing or unopenable: one `hx: memory: <reason>` line on stderr, exit 2.

`hx compose` adds section **Memory episodes** after **Step state**, source
`state/memory/chroma`, body `Search more: \`hx memory search "<query>"\`` (with ` --all-roles` in
the Partner's file only: the flag is the Partner's, `hx help`) then one
`- <ts> <id>/<kind> s=<0.xx>: <document>` line per hit, truncated to
`companion.memory_episode_chars`. Any failure is the single line
`_memory unavailable: <reason>_`. `companion.memory_inject_k: 0` removes the section entirely;
the other two fields are `memory_episode_chars` (default 700) and `memory_half_life_h`
(default 24).

## `hx help [--all | --audience A] [--json]` (exposure)

`hx.cli.AUDIENCES` is the one table of who sees what (TOOL_SKILL_PLAN §Inventory): columns
`operator`, `partner`, `worker`, `subagent`, `companion` (empty) and `runtime` (never listed or
gated), each a set of `command`, `command sub` or `command sub --flag` entries. The audience
is `operator` with no `HARNESS_ID`, `partner` for `HARNESS_ID=partner`, `companion` when
`HX_ROLE=companion`, else `worker`. `hx help --json`:

```json
{"audience": "worker", "caller": "eng-001", "role": "backend-engineer",
 "commands": ["code search", "complete", "learn propose", "memory search", "task", "…"]}
```

`hx help --all --json` is `{"audiences": {"operator": [...], "partner": [...], "worker": [...],
"subagent": [...], "companion": []}}`, each list sorted. Text output is one header line, then
one `  <command>  <sub> | <sub>` line per command family. `hx --help` prints the usage text
for the operator and the Partner, and the worker view for any other `HARNESS_ID`.

Authority backs it: before dispatch, a caller whose `HARNESS_ID` is set and is not `partner`
is refused (exit 1, `refuse: \`hx <entry>\` is the Partner's|the operator's, and HARNESS_ID is
\`<id>\`…`) every entry outside `worker ∪ subagent ∪ runtime`. `plan audit|finish|materialize`
defer to planning's own-assignment check; an unknown subcommand of a family with a worker
entry is left to that command's argparse.

## `src/hx/skills/manifest.json` (skill manifest)

```json
{"hx-worker": {"audiences": ["worker"], "roles": ["*"], "optional": false},
 "hx-memory": {"audiences": ["partner", "worker"], "roles": ["*"], "optional": false, "planned": false}}
```

One row per shipped skill directory, keyed by its name: `audiences` (non-empty, from
`operator|partner|worker|subagent|companion`), `roles` (`"*"` or `harness.json` role names; `[]`
installs nowhere), `optional` (installed only when `config/hx.json` `skills` names it), and
optional `planned` (default `true`; `false` keeps it out of a planned `HX_CONTINUITY_RUN`
home). `python -m hx.skill_manifest select --skills DIR --root ROOT --id ID [--companion]`
prints the names one home receives, one per line, in manifest order; every adapter's
`install.sh` copies exactly those (exit 2 and no install on an invalid manifest). The
`skills` row of `hx doctor` checks the manifest against the directories, each `SKILL.md`
against the Agent Skills rules (name 1–64 lowercase letters, digits and single hyphens,
equal to the directory; description 1–1,024 characters; only `name`, `description`,
`license`, `compatibility`, `metadata`, `allowed-tools`; values plain YAML scalars; file ≤32
KiB) and lints that no skill a worker or subagent carries names an `hx` command a worker is
refused. One `ok` line: `<n> skill(s) valid (frontmatter, size, worker lint); <k> over the 8
KiB facade bound; optional on: <names|none>`; otherwise one `fail` line per problem.
hx's skills carry `metadata: {hx-audience, hx-roles, hx-loads-when}`.

## `hx models [--provider P] [--expect current] [--json]`

Operator and Partner only (`hx help`). One GET per provider, in order `anthropic`, `openai`,
`xai`, `meta`, then `typesafe` (no request), 5 s each, no retry, no fallback; writes nothing.
Credential order per provider: the process environment, the env file of `config/auth.json`,
then the adapter's `seed/` file (`seed/token` only when it starts with `sk-ant-api`; the OAuth
token is never sent). `--json`:

```json
{"ts": "2026-10-12T09:00:00Z", "docs": "/abs/docs/models.md", "env_file": null,
 "expect": "current",
 "providers": {
   "anthropic": {"status": "ok", "source": "https://api.anthropic.com/v1/models?lifecycle=active&limit=1000",
                 "credential": "env-file:ANTHROPIC_API_KEY",
                 "ids": ["claude-opus-5-5", "…"], "added": ["claude-haiku-5-5"], "removed": [],
                 "deprecated": ["claude-opus-4-1"], "window": {"claude-opus-5-5": 1000000},
                 "effort": {"claude-opus-5-5": ["low", "medium", "high"]},
                 "configured": ["claude-opus-5-5"], "documented": ["claude-opus-5-5"],
                 "missing": [], "window_over": {}},
   "openai": {"status": "unverified", "credential": null, "reason": "no credential (…)", "…": "…"},
   "xai": {"status": "error", "http": 401, "reason": "HTTP 401 Unauthorized", "…": "…"},
   "typesafe": {"status": "pinned", "source": "hx.jev.MODEL", "ids": ["jev-1.13.0"], "…": "…"}},
 "stale": ["anthropic: claude-opus-4-1 is configured but not in the active list"]}
```

`status` is `ok`, `unverified` (no credential; no request made), `error` (`http` status or
`null`, and `reason`; the response body is never kept) or `pinned`. `ids` are the active IDs
returned, `deprecated` the returned IDs the provider flags (`lifecycle`/`status` deprecated or
legacy, `deprecated: true`, or a deprecation/retirement date); `added` are active IDs of the
provider's family (`claude-`, `gpt-`/`o1`/`o3`/`o4`/`codex-`, `grok-`, `muse-`/`llama`) not
in `docs/models.md`; `removed` are documented or configured IDs the provider no longer returns;
`missing` are configured IDs not active; `window_over` maps a configured ID to its configured
`window` and the provider's `max_input_tokens` when the first is larger. `window` and `effort`
are read from the record or its `capabilities`/`limits` (`max_input_tokens`, `context_window`,
`context_length`, `max_prompt_length`, …; effort lists or `{level: {supported: true}}` maps).
`documented` is `null` when `docs/models.md` is not beside the package (an installed wheel);
configured IDs are attributed to a provider by that same family prefix. Text output is one
block per provider and a last line `HX-MODELS ok=<n> unverified=<n> error=<n> pinned=<n>
stale=<n>`. `--expect current` exits 3 when `stale` is non-empty (only `ok` providers can be
stale); usage errors exit 2; a worker caller is refused (exit 1).

## Completion repeats (`state/learn/repeats.jsonl`, `learn_repeats`)

When `hx complete done`'s `### Checks` or a planned acceptance check fails, the first 2,000
bytes of its output are passed to `learn.repeat`. On `repeat` or `related` one line is appended
to `$HARNESS_ROOT/state/learn/repeats.jsonl`:

```json
{"id": "eng-001", "rule": "L3", "score": 0.84, "ts": "2026-10-12T09:00:00Z",
 "check": "### Checks", "verdict": "repeat"}
```

(`check` is `### Checks` for `hx complete`, the check ID for a planned run) and the line
`learnings: repeat of L3 (0.84)` is appended to the `HX-CHECK-FAILED` output (or to the pasted
acceptance-failure line; the native completion intent carries it as `learnings`). Nothing is
written on `new`. Advisory: the exit code and verdict are the check's. The fleet document of
`hx metrics [--json]` gains `summary.learn_repeats`: `{"L3": 2}` — records per rule whose `ts`
is later than that rule's last `new` or `merged` decision in `decisions.jsonl` (`{}` when
there are none); the text form adds a `learn_repeats(<total>): L3=2 …` line after `dead(…)`.

## `hx discipline pull --repo DIR [--expect fresh]`

Read-only git on local refs (it never fetches), 30 s timeout per call. The base is the first
of `dev`, `origin/dev`, `main`, `master`, `origin/main`, `origin/master` that has a merge-base
with `HEAD` (the order `commit_range` uses). Prints one object:

```json
{"base": "main", "base_sha": "4f1c2d3e5a6b", "merge_base": "9a8b7c6d5e4f",
 "behind": 2, "ahead": 1, "diverged_files": ["src/a.py", "src/b.py"],
 "base_age_s": 86400, "fresh": false,
 "instruction": "pull main: 2 commit(s) behind, 2 file(s) changed there"}
```

`behind` is `git rev-list --count HEAD..BASE`, `ahead` is `BASE..HEAD`, `diverged_files` is
`git diff --name-only HEAD...BASE` (what the base changed since the fork point), `base_age_s`
is seconds since the merge-base commit's committer date, and `fresh` is `behind == 0`
(`instruction` is then `"fresh"`). Exit 0; with `--expect fresh`, exit 3 and one
`hx: discipline pull: --expect fresh: <instruction>` line on stderr when `behind > 0`. No
principal branch, or a directory that is not a Git worktree, exits 2. A worker may run it.

## `hx discipline orient --post-seam ID`

The main stream of ID after its last `seam` record (exit 2 when there is none):

```json
{"id": "eng-001", "stream": "eng-001-main", "seam_seq": 812, "seam_ts": "2026-09-20T12:50:00Z",
 "source": "clear", "tool_calls": 14, "first_edit_seq": 820,
 "searches_before_first_edit": 1, "modules_read_before_first_write": 3, "task_reprints": 1}
```

Counted from `post_tool` records. A search is a Grep/Glob-type tool or a shell command running
`grep`, `rg`, `ag`, `ack`, `fd`, `find`, `git grep`, `hx code search|symbols|impact|edges|callers|type|signature`,
`hx memory search`, `hx content search` or `hx recall`; an edit is an Edit/Write-type tool or a
shell `apply_patch`, `sed -i`, `perl -i`, `cat >`/`>>` or `tee`; a module read is a distinct path
read by a Read-type tool or a shell `cat|head|tail|less|bat|nl`, never the `*.context.md` file.
`searches_before_first_edit` and `modules_read_before_first_write` stop at the first edit
(`first_edit_seq`, `null` when there is none, in which case the whole window counts);
`task_reprints` counts every `hx task` after the seam. The same three fields are added to every
entry of `seams` in `hx metrics <id>` (each seam's window ends at the next seam record) and
appended, in that order, to its text line.

## `hx metrics --commands [--since TS] [--json]`

`hx <command> [<sub>]` invocations per command and per agent, from the shell-type tool inputs
(`Bash`, `exec_command`, …) of every `logs/<id>/*.jsonl` stream, main and subagent:

```json
{
  "task": {"calls": 2, "agents": {"eng-001": 1, "qa-002": 1}},
  "code search": {"calls": 1, "agents": {"eng-001": 1}},
  "discipline pull": {"calls": 1, "agents": {"qa-002": 1}}
}
```

The key is `command sub` when that pair is an `hx help` (AUDIENCES) entry, else `command`;
names that are not `hx` commands are not counted. Ordered by `calls` descending, then key.
Records before `--since TS` are skipped; without it each id's floor is its `dispatched` in
`tasks.json` (no floor when there is none). Text form: one `<calls>  <key>  <id>=<n> …` line
per command, then `HX-METRICS-COMMANDS commands=<n> calls=<n>`. An ID or `--watch` with
`--commands`, `--since` without it, or an unparseable `TS` exits 2. Operator and Partner only.
