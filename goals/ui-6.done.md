# ui-6 done — real streams, subagents and the board columns, from records the hooks wrote

All five items done. The headline is item 4: **no stream record in `tests/ui/fixtures/` is
hand-written any more.** `tests/ui/regen_fixtures.py` drives `python -m hx.hooks` — the same
entry point `install.sh` bakes into an agent's settings — through a whole M4-shaped run and
writes what `hx show --json` returns for it.

## What was built

### 1. The real record vocabulary in the stream tail

`handoff/build-to-ui.md` (build-5) documents eight `event` values. All of them render, each as
what it actually is rather than as a generic row:

| `event` | How it reads |
|---|---|
| `boundary` | a marker: "session start · startup", and the context file it handed over |
| `post_tool` | tool, exit code, input/output excerpts, `ref`, context tokens |
| `spawned` | the handle, the agent type, the `agent_id` |
| `closed` | the handle, "stream closed", the digest file |
| `subagent_result` | the handle, "result returned to the parent" — or "returned with no digest" |
| `open` / `close` | the subagent's own first and last record |
| `seam` | unchanged from ui-3; `hx seam` is build-7, see below |

Four of these came directly from what build-5 told me, and each changed the view for a reason:

- **Excerpts are labelled as excerpts.** `input`/`output` are head excerpts capped at 2000
  characters; where one ends in the ellipsis the row says "head excerpt — the rest is in the
  transcript". Presenting a truncated tool result as the whole of it is exactly the quiet
  wrongness a read-only view must not commit.
- **`ref` is shown** — the `tool_use_id` and the transcript's file name — so a reader knows
  where the rest lives. No "show full result" affordance: that would mean the UI reading Claude
  Code's transcripts directly, which is a bigger decision than ui-6.
- **A subagent's tool call on the main stream is badged `from <agent_id>`.** build-5 flagged
  this as "one thing that will look odd and is correct"; unbadged it would look like the agent
  making calls it never made, which matters most for the Partner, whose subagents have no hooks
  at all.
- **`_pending companion_` renders as the placeholder it is**, and a contract test asserts it is
  still exactly that — so when build-6 writes real digests the test fails, the fixture is
  regenerated, and the view never shows a stale placeholder.

**"last turn" in the Agent header.** `hx show --json` does not carry `run/<id>/turn`. The board
does, as `turn_ts`, and the Agent view already fetches the board for its id switcher — so the
header reads "last turn 23:42:12Z" with no instance file read directly, which keeps the rule I
set in ui-5 that everything comes through the build lane's functions.

### 2. Subagent handles

A table per id: handle, `claude agent_id`, its stream's state and record count, and its digest.
Built from `run/<id>/subagents.json` (`{agent_id: sNNN}`) joined to the `-open`/`-closed`
streams. A closed stream's digest also renders on its own stream card, as markdown.

### 3. The board columns from real data

`open_subagents`, `turn_ts`, `context_tokens` and `seams`, asserted against an instance the
hooks produced — not against a fixture:

- `open_subagents` is 1, and equals the number of `-open` subagent streams.
- `context_tokens` is 61,300, which is `12_400 + 48_900` — **input plus cache reads, not
  output**, as build-5 specifies, taken from the latest `post_tool`.
- `seams` is `0` for an id whose stream exists with no seam records in it, and `null` for
  `partner`, which has no stream at all. That is the distinction ui-3 asked the build lane to
  keep, now asserted from both ends against real data.
- The rendered board shows `—` for the `null`s and the real figures otherwise.

### 4. Fixtures regenerated, and the contract test rewritten

```
$ .venv/bin/python -m tests.ui.regen_fixtures
wrote tests/ui/fixtures/show-eng-001.json
  streams   : ['eng-001-main', 'eng-001-s001', 'eng-001-s002', 'eng-001-s003']
  closed    : ['eng-001-s002', 'eng-001-s003']
  subagents : {'claude-agent-eng-001-00': 's001', …-01': 's002', …-02': 's003'}
  main tail : ['boundary', 'post_tool', 'post_tool', 'post_tool', 'spawned', 'spawned',
               'closed', 'subagent_result', 'spawned', 'closed', 'subagent_result']
```

Only two things are edited on the way in, both to keep the file stable in git: the scratch root
becomes `/srv/hx`, and timestamps become fixed instants. Nothing about a record's shape or
content is touched.

`test_fixtures_contract.py`'s stream section was replaced. It now holds the fixture to the
hooks' vocabulary: the four fields every record carries, `seq` monotonic and unique per stream,
a `boundary` first with a valid `source` and a `context_file`, every `spawned` handle agreeing
with `subagents.json`, nothing closing that never spawned, every closed handle having its
`subagent_result`, a subagent stream opening with `open` and ending with `close` only when its
path says `-closed`, and open streams carrying no digest.

**`step_state` and `metrics` stay hand-written**, because nothing writes them yet — the
Companion is build-6 and `hx metrics` is M7. The contract test says so in as many words, so it
is a recorded decision rather than an oversight.

### 5. Harness coverage, and the seam records that do not exist yet

`tests/ui/test_m4_streams.py` is new: 18 tests over an instance the hooks produced, covering
every item above. No browser pass is possible in this session (unchanged from ui-4 and ui-5 —
the only fetch tool refuses localhost), so the DOM harness is the coverage.

The seam rendering had to move. A real run writes no `seam` record — `hx seam` is build-7 — so
the regenerated fixture has none, and eight tests that read one out of the old hand-written
fixture broke. The rendering is still required (spec 16.2's marker, and CONTRACTS.md's metrics
document is keyed on seam `seq`), so those tests now **declare** the spec 07.4 record and layer
it onto the real document, and a contract test asserts the fixture contains no seam. When
build-7 lands, that test fails, the fixture is regenerated, and the declaration comes out. The
alternative — quietly leaving a hand-written seam in a file whose whole point is that it is
real — would have undone item 4.

## How it was verified

```
$ ./tools/milestone-check.sh ui
== required: tests/guard
.....                                                                    [100%]
== required:  tests/ui
...................s...                                                  [100%]
MILESTONE-CHECK PASSED for ui (own paths; add --all for the advisory run)
EXIT=0

$ .venv/bin/python -m pytest tests/guard
5 passed in 0.37s

$ .venv/bin/python -m pytest tests/ui
368 passed, 1 skipped in 14.02s
```

Up from 338 at the close of ui-5. Every instance is read through a private tmux server, as
since ui-5: `hx board` matches a live session by the bare id and other lanes run real ones here.

### `/api/show/eng-001`, served from the real instance

Two closed subagent streams and one open, as ui-6 item 5 asks. Excerpts clipped to 64
characters for this paste and the work item body dropped; everything else is verbatim.

```json
{
  "context_file": {
    "path": "run/eng-001/eng-001-main.context.md",
    "seam_ts": "2026-09-20T23:42:11Z",
    "text": "# Context for eng-001-main\n\nThis file is\u2026"
  },
  "file": "pods/engineers/eng-001-working.md",
  "id": "eng-001",
  "metrics": null,
  "pane": {
    "alive": false,
    "error": "can't find session: eng-001",
    "lines": [],
    "session": "eng-001",
    "source": "none"
  },
  "pod": "engineers",
  "role": "engineer",
  "state": "working",
  "step_state": {},
  "streams": [
    {
      "digest": null,
      "handle": "eng-001-main",
      "open": true,
      "path": "logs/eng-001/eng-001-main.jsonl",
      "records": 11,
      "tail": [
        {
          "context_file": "/srv/hx/run/eng-001/eng-001-main.context.md",
          "event": "boundary",
          "ref": {
            "transcript": null
          },
          "seq": 1,
          "source": "startup",
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "context_tokens": 40000,
          "event": "post_tool",
          "input": "{\"file_path\": \"spec/08-hx-cli.md\"}",
          "output": "{\"content\": \"## 8. `hx` CLI\\nZero-dependency Python\\u2026\"}",
          "ref": {
            "tool_use_id": "toolu_read_08",
            "transcript": "/srv/hx/run/eng-001/t-early.jsonl"
          },
          "seq": 2,
          "tool": "Read",
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "context_tokens": 40000,
          "event": "post_tool",
          "exit": 0,
          "input": "{\"command\": \"python3 -m unittest discover -s tests -q\"}",
          "output": "{\"stdout\": \"....\\n\", \"stderr\": \"\", \"exit_code\": 0}",
          "ref": {
            "tool_use_id": "toolu_bash_tests",
            "transcript": "/srv/hx/run/eng-001/t-early.jsonl"
          },
          "seq": 3,
          "tool": "Bash",
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "context_tokens": 61300,
          "event": "post_tool",
          "exit": 1,
          "input": "{\"file_path\": \"greet.py\", \"old_string\": \"def greet\", \"new_string\u2026",
          "output": "{\"stdout\": \"\", \"exit_code\": 1, \"stderr\": \"no match\"}",
          "ref": {
            "tool_use_id": "toolu_edit_greet",
            "transcript": "/srv/hx/run/eng-001/t-late.jsonl"
          },
          "seq": 4,
          "tool": "Edit",
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-00",
          "agent_type": "general-purpose",
          "event": "spawned",
          "handle": "s001",
          "seq": 5,
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-01",
          "agent_type": "general-purpose",
          "event": "spawned",
          "handle": "s002",
          "seq": 6,
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-01",
          "digest": "/srv/hx/state/eng-001/eng-001-s002.digest.md",
          "event": "closed",
          "handle": "s002",
          "seq": 7,
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-01",
          "digest": "/srv/hx/state/eng-001/eng-001-s002.digest.md",
          "event": "subagent_result",
          "handle": "s002",
          "ref": {
            "tool_use_id": null
          },
          "seq": 8,
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-02",
          "agent_type": "general-purpose",
          "event": "spawned",
          "handle": "s003",
          "seq": 9,
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-02",
          "digest": "/srv/hx/state/eng-001/eng-001-s003.digest.md",
          "event": "closed",
          "handle": "s003",
          "seq": 10,
          "ts": "2026-09-20T23:42:12Z"
        },
        {
          "agent_id": "claude-agent-eng-001-02",
          "digest": "/srv/hx/state/eng-001/eng-001-s003.digest.md",
          "event": "subagent_result",
          "handle": "s003",
          "ref": {
            "tool_use_id": null
          },
          "seq": 11,
          "ts": "2026-09-20T23:42:12Z"
        }
      ]
    },
    {
      "digest": null,
      "handle": "eng-001-s001",
      "open": true,
      "path": "logs/eng-001/eng-001-s001-open.jsonl",
      "records": 2,
      "tail": [
        {
          "agent_id": "claude-agent-eng-001-00",
          "agent_type": "general-purpose",
          "event": "open",
          "input": "survey the exit-code assertions for claude-agent-eng-001-00",
          "ref": {
            "transcript": null
          },
          "seq": 1,
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-00",
          "context_tokens": 10300,
          "event": "post_tool",
          "input": "{\"pattern\": \"returncode ==\"}",
          "output": "{\"stdout\": \"tests/test_board.py:41\\n\"}",
          "ref": {
            "tool_use_id": "toolu_grep_claude-agent-eng-001-00",
            "transcript": "/srv/hx/run/eng-001/t-claude-agent-eng-001-00.jsonl"
          },
          "seq": 2,
          "tool": "Grep",
          "ts": "2026-09-20T23:42:11Z"
        }
      ]
    },
    {
      "digest": "_pending companion_\n",
      "handle": "eng-001-s002",
      "open": false,
      "path": "logs/eng-001/eng-001-s002-closed.jsonl",
      "records": 3,
      "tail": [
        {
          "agent_id": "claude-agent-eng-001-01",
          "agent_type": "general-purpose",
          "event": "open",
          "input": "survey the exit-code assertions for claude-agent-eng-001-01",
          "ref": {
            "transcript": null
          },
          "seq": 1,
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-01",
          "context_tokens": 10300,
          "event": "post_tool",
          "input": "{\"pattern\": \"returncode ==\"}",
          "output": "{\"stdout\": \"tests/test_board.py:41\\n\"}",
          "ref": {
            "tool_use_id": "toolu_grep_claude-agent-eng-001-01",
            "transcript": "/srv/hx/run/eng-001/t-claude-agent-eng-001-01.jsonl"
          },
          "seq": 2,
          "tool": "Grep",
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-01",
          "event": "close",
          "output": "claude-agent-eng-001-01: found two exit-code assertions; both al\u2026",
          "ref": {
            "transcript": null
          },
          "seq": 3,
          "ts": "2026-09-20T23:42:11Z"
        }
      ]
    },
    {
      "digest": "_pending companion_\n",
      "handle": "eng-001-s003",
      "open": false,
      "path": "logs/eng-001/eng-001-s003-closed.jsonl",
      "records": 3,
      "tail": [
        {
          "agent_id": "claude-agent-eng-001-02",
          "agent_type": "general-purpose",
          "event": "open",
          "input": "survey the exit-code assertions for claude-agent-eng-001-02",
          "ref": {
            "transcript": null
          },
          "seq": 1,
          "ts": "2026-09-20T23:42:11Z"
        },
        {
          "agent_id": "claude-agent-eng-001-02",
          "context_tokens": 10300,
          "event": "post_tool",
          "input": "{\"pattern\": \"returncode ==\"}",
          "output": "{\"stdout\": \"tests/test_board.py:41\\n\"}",
          "ref": {
            "tool_use_id": "toolu_grep_claude-agent-eng-001-02",
            "transcript": "/srv/hx/run/eng-001/t-claude-agent-eng-001-02.jsonl"
          },
          "seq": 2,
          "tool": "Grep",
          "ts": "2026-09-20T23:42:12Z"
        },
        {
          "agent_id": "claude-agent-eng-001-02",
          "event": "close",
          "output": "claude-agent-eng-001-02: found two exit-code assertions; both al\u2026",
          "ref": {
            "transcript": null
          },
          "seq": 3,
          "ts": "2026-09-20T23:42:12Z"
        }
      ]
    }
  ],
  "subagents": {
    "claude-agent-eng-001-00": "s001",
    "claude-agent-eng-001-01": "s002",
    "claude-agent-eng-001-02": "s003"
  }
}
```

## Live against Claude Code vs. against the fake

Still nothing against Claude Code or the fake `claude` — the UI launches no agent. But this
goal moved a long way toward the real thing: the records above were written by **the real hook
binary**, from payloads shaped as Claude Code sends them (`PostToolUse`, `SubagentStart`,
`SubagentStop`, `PostToolUse(Agent)`, `Stop`, `SessionStart`), into a **real `HARNESS_ROOT`**,
and read back through **the build lane's own `hx.show.collect`**. What is still simulated is
only the payloads themselves — a real Claude session would produce them, and M6 onward is where
that happens.

`tests/ui/m4.py` holds those payloads and deliberately does not import from `tests/core/**`:
those helpers are the build lane's, and coupling the ui suite to them would turn a rename there
into a failure here. The subprocess contract — `--id <id> <event>`, JSON on stdin — is the part
that is public.

## Open questions

1. **`background_tasks` is not visible anywhere.** The stop hook records it in `run/<id>/turn`,
   and "this agent stopped with work still running" is a real thing for a human to see, but
   `hx show --json` does not carry the marker and the board only carries `turn_ts`. Raised with
   the build lane; it needs a `CONTRACTS.md` line before the UI can show it, and I have not
   asked for one yet because ui-6 does not need it.
2. **No "show full result" affordance.** `ref` is rendered as a pointer, but following it would
   mean the UI reading Claude Code's transcripts directly — outside everything else, which goes
   through the build lane's functions. Worth deciding deliberately rather than drifting into.
3. **Still no browser pass**, unchanged from ui-4 and ui-5.

## Handoff entries written

- `handoff/build-to-ui.md` — build-5 read, applied and marked `DONE 2026-09-20`, with what each
  of their four notes changed in the view, how the fixtures are now generated, the `turn`
  marker gap and why I did not ask them to close it yet, and confirmation that no seam records
  are expected until build-7.
- `handoff/orchestrator-to-ui.md` — their milestone-check answer marked `DONE 2026-09-20`;
  applied from ui-5 onward.
- Nothing new to `handoff/to-orchestrator.md` or `handoff/ui-to-gtm.md`: the open items above
  are questions in this file, and the static file list is unchanged as ui-3 promised.
