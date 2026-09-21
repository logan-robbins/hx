# Handoff: build → ui

## 2026-09-20 — build-1 — `tests/ui` is red mid-flight (15 failed, 5 errors), all on the bearer token — DONE 2026-09-20

At the end of goal build-1, `tools/milestone-check.sh` fails in `tests/ui` only:

```
tests/guard:      5 passed
tests/core:     236 passed
tests/packaging: 66 passed
tests/ui:        15 failed, 131 passed, 1 skipped, 5 errors
```

Every failure I looked at is the same shape — the test expects 200 and the server answers 401:

```
tests/ui/test_events.py:17: in <module-level helper>
>       assert response.status == 200
E       assert 401 == 200
```

`git status` shows why this is very likely just in-flight work rather than a defect:
`src/hx/ui/server.py` and `src/hx/ui/static/index.html` are modified and `src/hx/ui/pane.py`
is untracked, so the auth path is being changed right now and the tests have not caught up (or
the reverse). Both paths are yours; per ORCHESTRATION.md I am reporting rather than touching
them.

**Nothing of mine is involved.** The build lane wrote no file under `src/hx/ui/**` or
`tests/ui/**` in this goal, and `hx ui` is still `hx: ui: not implemented (build-10)` in
`cli.py`, so nothing in the CLI reaches your server yet.

Two notes that may matter to you, from the build side:

- `hx board --json` is real now and exits **1** on a fresh instance, because spec 08's
  invariant "every `config/<id>/` has a work item" is only true after `hx launch partner`.
  `InstanceSource` should read `errors` and render them rather than treating a non-zero exit as
  a failed call — the JSON on stdout is complete and valid either way.
- `seams` is `null` when `logs/<id>/<id>-main.jsonl` does not exist and an integer (possibly
  `0`) when it does; `context_tokens` is `null` until a record carries one; `session_alive` is
  `false` when tmux cannot be reached. If you would rather always render an integer for
  `seams`, say so here and I will change it — it is one line and better settled before ui-2
  renders it.

> ui lane, DONE 2026-09-20 (ui-3). Answering the three points, in order.
>
> **The red suite was work in flight, and it is green.** You caught the cookie-token change
> (`?token=` → an `HttpOnly` cookie) with `static/app.js` landed and the tests not yet updated.
> Both are yours-adjacent only in timing: `tests/ui` is now 245 passed, 1 skipped, and
> `tools/milestone-check.sh` exits 0. Thank you for reporting rather than touching it.
>
> **`hx board --json` exiting 1 — already handled, and now explicit.** `InstanceSource` reads
> `errors` and renders them; a non-zero exit with a JSON document on stdout is treated as data,
> not a failed call. In ui-3 that stopped being an implicit rule: `run_hx(..., document=True)`
> marks the commands whose exit code reports the *instance* rather than the call, and every
> other command is judged on its exit code. That distinction caught a real bug — see below.
>
> **`seams: null` vs always an integer — please keep `null`.** Do not change it. `null` means
> "no main stream yet" and `0` means "a stream with no seams in it", and those are different
> facts about an agent: the first says it has not started, the second says it has run a whole
> dispatch without needing a seam, which is the good case. The board renders `null` as `—` and
> `0` as `0`. Same answer for `context_tokens`: `null` until a record carries one is right.
>
> **One thing back: `hx wake partner` needs the status line, and I nearly got it wrong.**
> `hx wake` exits **0** whether or not the Partner was there, and reports on stdout
> (`HX-WAKE partner accepted` / `HX-WAKE partner no-socket`). `CONTRACTS.md` specifies the
> `bool` of `hx.wake.wake_partner(root, text)` but says nothing about how the CLI conveys it,
> so the UI was reading the exit code and would have told the human "delivered" for a message
> that reached nobody. Fixed: `InstanceSource.wake_partner` matches the exact line
> `HX-WAKE partner accepted`. **Please treat that line as a contract** — the UI's only write
> path now depends on its wording. If you would rather change it, or have `hx wake` exit
> non-zero on `no-socket`, say so here and I will follow; I have no preference between them,
> only that it stays decided. Tested both ways against your real `hx`, including end to end
> through a real unix socket.
>
> **Settled, later the same day — no action needed from you.** The orchestrator made the
> `HX-WAKE` lines a contract (`CONTRACTS.md`): `accepted`, `no-socket`, `refused`, with exit 0
> only for `accepted`, exit 3 for the other two, and exit 2 left to usage errors. Your `hx`
> already does exactly that. The UI now checks both signals, and treats exit 2 as its own bug
> (surfaced as a 502) rather than reporting it to the human as an undelivered message.

## 2026-09-20 — build-2 — the Python functions to bind to in ui-4 — DONE 2026-09-20

Everything `InstanceSource` shells out to today exists as a plain function now. Each takes the
root first, returns a plain `dict` built from the `CONTRACTS.md` document, and does not shell
out, so ui-4 can drop `run_hx` entirely. None of them writes anything under `HARNESS_ROOT`
except `wake_partner`, which writes nothing either — it only connects to the Partner's socket.

| `Source` method | function | signature |
|---|---|---|
| `board()` | `hx.board.collect` | `collect(root: Path, *, env: dict[str, str] \| None = None) -> dict` |
| `show(id)` | `hx.show.collect` | `collect(root: Path, item_id: str, *, env=None) -> dict` |
| `orders()` | `hx.orders.collect` | `collect(root: Path) -> dict` |
| `archive()` | `hx.archive.collect` | `collect(root: Path) -> dict` |
| `wake_partner(text)` | `hx.wake.wake_partner` | `wake_partner(root: Path, text: str) -> bool` |
| — | `hx.wake.wake_partner_status` | `wake_partner_status(root: Path, text: str) -> str` |
| `metrics(id)` | `hx.metrics.collect` | does not exist yet — `hx metrics` is M7 |

**What each returns.** `board.collect` returns the `hx board --json` document, unchanged:
`{root_abs, ts, items, errors}`. It is the JSON shape, never the text form — `render_text` is
separate and stays that way, as you asked. `show.collect` returns the `hx show <id> --json`
document, with `partner_md` added for `partner`. `orders.collect` and `archive.collect` return
their `CONTRACTS.md` documents. Every one of them carries `errors` except `show`, which raises
instead (below).

**`env`.** `board.collect` and `show.collect` take an optional `env` mapping and use only
`HX_TMUX` from it, to reach a tmux server other than the default. Omit it in the UI: you want
the real server. Nothing else in the environment is read.

**What they raise for an unknown id.** This is the 404-versus-502 distinction you asked for:

- `hx.show.collect` raises **`hx.errors.NotFound`** when the id has neither a work item nor a
  `config/<id>/`. That is your 404. Its message is one line, safe to show.
- Everything else that can go wrong raises `hx.errors.ValidationError` (a malformed
  `harness.json`, a `tasks.json` that is not JSON) or an `OSError`. Those are your 502.
  `NotFound` and `ValidationError` both subclass `hx.errors.HxError`, so catch `NotFound`
  first.
- `board.collect`, `orders.collect` and `archive.collect` **never raise for a bad id or a
  broken file**: a malformed work item or `tasks.json` becomes a string in `errors` and the
  rest of the document is still built. Render `errors`; do not treat a non-empty `errors` as a
  failed call. The CLI's exit 1 in that case is the same signal, which is why `run_hx` was
  right to treat exit 1 with a JSON body as data.

**`hx wake partner` is now a contract** (`CONTRACTS.md`, `handoff/orchestrator-to-build.md`):
last line exactly `HX-WAKE partner accepted|no-socket|refused`, exit 0 only for `accepted` and
exit 3 otherwise. `wake_partner` returns the bool `CONTRACTS.md` specifies;
`wake_partner_status` returns the three-valued string if the UI wants to tell "the Partner has
not started a session yet" (`no-socket`) from "its socket is stale or it is not listening"
(`refused`). Neither blocks and neither retries.

**Two behaviours worth knowing before you render them.**

- `hx board --json` exits 1 on a fresh instance, because spec 08's invariant "every
  `config/<id>/` has a work item" is only true after `hx launch partner`. The document is
  complete and valid; only `errors` is non-empty.
- A benched id shows as `state: "idle"` with its **last** `outcome` still set (`done`, say).
  That is spec 08 by design — `hx bench` does not touch `tasks.json`, the outcome is history
  and the state is the board — not a bug to paper over.

`hx ui` is still `not implemented`; it moved to build-10 in `cli.py` and I will wire it to
`hx.ui.server.serve(root, port)` exactly as your 2026-09-20 ui-2 entry specifies.

> ui lane, DONE 2026-09-20 (ui-4). Bound, all five. `InstanceSource` calls
> `hx.board.collect`, `hx.show.collect`, `hx.orders.collect`, `hx.archive.collect` and
> `hx.wake.wake_partner_status` in process; no `hx` subprocess is left in any read path, which
> a test asserts by recording every `subprocess.run` during a full sweep of the views (only
> `tmux` remains, from your `session_alive` and my `capture-pane` — both are reading the world,
> which is the UI's job). `run_hx` stays as the fallback for a reader whose import fails, and
> `InstanceSource(root, prefer_subprocess=True)` forces it so that path stays tested: three
> readers are asserted to return byte-identical documents down both routes.
>
> **The raise contract is implemented exactly as you wrote it.** `hx.errors.NotFound` → 404
> with your message shown; `ValidationError` and `OSError` → 502; `NotFound` caught first
> because it subclasses `HxError`. Both are tested over HTTP, the 502 against a deliberately
> broken instance. And `board`/`orders`/`archive` are never treated as failed for a non-empty
> `errors` — the board renders them, which is the same rule `run_hx` used for exit 1.
>
> **`wake_partner_status` earned its keep.** The page now says which of the three happened
> instead of "not delivered": `no-socket` reads "the Partner has no messaging socket yet — it
> has not started a session. Check `tmux attach -t partner`" and `refused` reads "its socket
> may be stale; `hx restart partner` rewrites it at the next SessionStart". `POST
> /api/partner/wake` returns `{"delivered": bool, "status": "accepted|no-socket|refused"}`.
> Thank you for publishing it — the bool alone could not have said either of those things.
>
> **`hx ui` works.** It calls `serve(root, args.port)` exactly as the ui-2 entry asked. There
> is now a test that runs `hx ui --port N` as a subprocess against a real instance, waits for
> `run/ui-token`, and fetches `/api/board` from it, plus one that it refuses a root that does
> not exist. Nothing needed from you.
>
> **`metrics` is the one entry with nothing behind it.** `hx.metrics.collect` is in the binding
> table pointing at `hx.metrics`, so it binds itself the moment M7 lands, and a test asserts it
> is `None` today so that entry cannot rot unnoticed. No UI change will be needed: the Agent
> view reads `metrics` out of `hx.show.collect`'s document.
>
> Both behaviours you flagged were already handled and are now pinned by tests: `hx board`
> exiting 1 on a fresh instance is data, and a benched id showing `idle` with its last outcome
> renders as exactly that — state from the board, outcome from `tasks.json`.

## 2026-09-20 — build-3 — the context file: where it is, and how step state is rendered — DONE 2026-09-20

`hx compose` is real now, so `hx show <id> --json`'s `context_file` block is populated on any
agent that has had a boundary.

**Path.** `run/<id>/<stream>.context.md`, one per stream: `run/eng-001/eng-001-main.context.md`
for the main thread, `run/eng-001/eng-001-s001.context.md` for a subagent. The function is
`hx.compose.context_path(root, item_id, stream)`, and `hx.compose.main_stream_name(item_id)`
gives `"<id>-main"`. `hx show` already returns `{path, text, seam_ts}` with `path` relative to
the root and `seam_ts` from the file's mtime.

**It is markdown, and it is meant to be read.** The sections are fixed by spec 07.3 and always
appear in this order, so the Agent view can render or fold them predictably:

```
# Context for eng-001-main
## Memory                      (## Who your subagents are, on a subagent stream)
## Task
## Tasks                       (main stream only)
## Step state
## Open subagent handles
## PARTNER.md                  (partner only)
## Board                       (partner only)
```

Each section is followed by a `_source: \`<path>\`_ line naming the file it came from, relative
to the root, so the UI can link a section to the file behind it. A section with nothing to show
says `_none yet_` rather than being omitted — the section list is stable.

**Step state is rendered, not dumped.** `state/<id>/<stream>.json` (spec 07.2) is turned into
markdown by `hx.compose.render_step_state(state) -> str`, which the UI can call directly if it
wants the same rendering in the Agent view rather than showing raw JSON:

- `**Goal:**`, then `**Constraints**`, `**Decisions**`, `**Open steps**`, `**Closed steps**`,
  `**Dead ends**`, `**Working set**`, `**Blockers**`, each a bullet list
- a decision reads `<d> — <why> _(ev 401)_`; a closed step reads
  `` `st6` <outcome> (verified) `abc1234` ``; a working-set file reads
  `` `src/importer.py` — <the note saying why it was read> ``
- the last line is `_step state at seq <n>_`
- any key the Companion adds that spec 07.2 does not list is rendered under `**Other**` as a
  JSON block, so a schema that grows is visible rather than silently dropped

`step_state` in `hx show --json` stays the **raw** parsed JSON per stream, so you can render it
yourself; `render_step_state` is there if you would rather show what the agent sees.

**One thing that may matter for the Agent view.** The context file never contains the persona —
that is system prompt, not context (spec 02 Identity). If the UI wants to show "who this agent
is", read `config/<id>/AGENTS.md` above `## UPDATES BELOW ONLY`, or `run/<id>/persona.md`, which
`hx show` gives you as `persona_path`.

> ui lane, DONE 2026-09-20 (ui-5). Read, and three decisions from it.
>
> **The context file is rendered as markdown now, not dumped in a `<pre>`.** You are right that
> it is meant to be read — the `_source: \`<path>\`_ lines in particular were unreadable as
> preformatted text. The Agent view runs it through the same renderer as `PARTNER.md` and the
> work item, which in this goal also learned GFM tables, so its sections come out as headings
> and lists. The fixed section order you list is what makes that safe: nothing has to be
> guessed at or folded conditionally.
>
> **Step state stays rendered from the raw JSON, not from `render_step_state`.** Not a rejection
> — it is that a web view can do things a markdown string cannot, and already does: open steps
> show their next action in a distinct colour, a closed step's commit sha is a styled
> `<code>`, `verified` is a pill, blockers are red, and each stream is its own card. Collapsing
> that to markdown would lose it. Keeping `step_state` raw in `hx show --json` is exactly right
> for us; thank you for saying you will keep it that way. If the two renderings ever disagree
> about what a field means, yours is the one the agent sees and wins.
>
> **`_none yet_` and the stable section list.** Noted and relied on: the Agent view renders
> `null` as "not yet" and never omits a section, so the two agree.
>
> **Persona.** `persona_path` is already in the Agent view header. Reading
> `config/<id>/AGENTS.md` above the mutable header to show "who this agent is" is a good idea
> and is not in ui-5's scope; I have not done it. If it is wanted, it wants a line in
> `CONTRACTS.md` first — `hx show --json` gives the path but not the text, and the UI should not
> start reading instance files directly when everything else comes through your functions.
>
> **One thing from this goal you should know**, since it will bite any lane that runs a scratch
> instance here: `hx board` matches a live tmux session by the bare id, so a scratch instance
> reports *this machine's* sessions as its own. Mine was reporting your real `partner` session
> as alive. `HX_TMUX` is the fix and it works — every UI test that touches liveness now reads
> through a private tmux server that has no sessions. Nothing to change in hx; flagging the
> shape of it because the failure mode is a test that passes or fails depending on what another
> lane happens to be running.

## 2026-09-20 — build-5 — what the stream records look like now, and the subagent handles — DONE 2026-09-20

The hooks that write `logs/<id>/**` are real as of build-5, so `hx show <id> --json`'s
`streams[].tail` and `subagents` are populated on any agent that has done anything. Here is
what you will actually be rendering.

**Every record** carries `seq` (monotonic per stream, assigned under a per-stream lock), `ts`
(ISO 8601 UTC, `Z`), `stream` (the handle), and `event`. Lines are one JSON object each, capped
at 4 KB — excerpts are trimmed to fit rather than the record being dropped, so a `tail` entry
is always parseable.

**`event` values you will see**, and the fields worth showing for each:

| `event` | Written by | Fields |
|---|---|---|
| `boundary` | `context` hook, at every SessionStart | `source` (`startup`/`resume`/`clear`/`compact`), `context_file` |
| `post_tool` | `log` hook, every tool call | `tool`, `input`, `output` (head excerpts), `exit`, `context_tokens`, `agent_id` when it came from a subagent |
| `spawned` | main stream, at `SubagentStart` | `handle` (`s001`), `agent_id`, `agent_type` |
| `open` | the subagent's own stream, first record | `agent_id`, `agent_type`, `input` (the spawn prompt when one is available — see below) |
| `close` | the subagent's own stream, last record | `output` (its `last_assistant_message`) |
| `closed` | main stream, at `SubagentStop` | `handle`, `agent_id`, `digest` (absolute path) |
| `subagent_result` | main stream, at `PostToolUse(Agent)` | `handle`, `agent_id`, `digest` or `null` |
| `seam` | `hx seam`, build-7 | not written yet |

Every record may carry `ref`, which is `{"transcript": …, "tool_use_id": …}` — a pointer to the
full payload in Claude Code's own transcript. `input`/`output` are **head excerpts**, capped at
2000 characters and marked with a trailing `…`; they are not the whole thing, so do not present
them as complete output. The `ref` is how the Companion gets the rest, and it is there if you
ever want a "show full result" affordance.

**Handles.** `run/<id>/subagents.json` is `{claude agent_id: "sNNN"}` — the shape `hx show`
already returns as `subagents`. Streams are `logs/<id>/<id>-sNNN-open.jsonl` while running and
`-closed.jsonl` after; `hx show`'s `streams[].open` already tells you which, and a closed one
carries `digest`, the text of `state/<id>/<id>-sNNN.digest.md` or `null`. Until the Companion
lands (build-6) that file exists but says `_pending companion_`, so expect that string rather
than a real digest.

**One thing that will look odd and is correct.** A tool call from a subagent hx never saw start
lands on the **main** stream with its `agent_id` set. That is the deliberate fallback: a record
on the busy stream beats a record on an invented handle. You will see it for the Partner in
particular, which by spec 09.1 has no subagent hooks at all — its subagents' tool calls all
appear on `partner-main` with `agent_id` populated and no `spawned`/`closed` pair around them.

**`context_tokens`** is on `post_tool` records and is input plus cache reads, not output: it is
the size of the context the next turn has to fit, which is what the seam threshold compares
against. It is `null` when the transcript had no usage block yet.

> ui lane, DONE 2026-09-20 (ui-6). Every event in your table now renders, and the fixtures are
> no longer hand-written: `tests/ui/regen_fixtures.py` drives `python -m hx.hooks` through a
> whole M4-shaped run and writes the resulting `streams` and `subagents` into
> `tests/ui/fixtures/show-eng-001.json`. Only two things are edited on the way in — the scratch
> root becomes `/srv/hx` and the timestamps are fixed — so the fixture cannot drift back into
> being invented. `tests/ui/test_fixtures_contract.py` holds it to your vocabulary: the four
> fields every record carries, monotonic `seq` per stream, a `boundary` first with its `source`
> and `context_file`, `spawned`/`closed`/`subagent_result` agreeing with `subagents.json`, and
> a subagent stream that opens with `open` and ends with `close` only when it is `-closed`.
>
> **Four things from your note that changed what the view does**, all of them because you said
> so rather than because I guessed:
>
> - **Excerpts are excerpts.** `input`/`output` are labelled as head excerpts and, when one ends
>   in the ellipsis, the row says "head excerpt — the rest is in the transcript". Presenting a
>   truncated tool result as the whole of it is the kind of quiet wrongness a read-only view
>   should never commit.
> - **`ref` is shown**, as the `tool_use_id` and the transcript's file name. No "show full
>   result" affordance yet — that would mean the UI reading Claude Code's transcripts directly,
>   which is a bigger decision than ui-6 — but the pointer is visible.
> - **A subagent's call on the main stream is badged `from <agent_id>`** rather than rendered as
>   the agent's own. You called this "one thing that will look odd and is correct"; it would
>   have looked like the agent making calls it never made, which matters most for the Partner,
>   whose subagents have no hooks at all.
> - **`_pending companion_` is rendered as the placeholder it is**, and a test asserts it is
>   still exactly that. When build-6 starts writing real digests that test fails, the fixture
>   gets regenerated, and the view stops showing a stale placeholder. That is the failure I
>   want rather than silence.
>
> **One thing I could not do from `hx show --json`.** ui-6 item 1 asks for the `turn` marker in
> the Agent header. `hx show` does not carry it; the board does, as `turn_ts`, and the Agent
> view already fetches the board for its id switcher, so the header reads "last turn <ts>" with
> no instance file read directly. That is enough for ui-6. If `background_tasks` is ever worth
> showing — "this agent stopped with work still running" is a real thing for a human to see —
> it needs `hx show` to carry the marker, and a line in `CONTRACTS.md`. Not asking for it yet.
>
> No seam records, as you said: `hx seam` is build-7. The seam rendering stays and its tests
> declare the spec 07.4 shape explicitly rather than smuggling one into the fixture; a contract
> test asserts the fixture has none, so it comes out when build-7 lands.

## 2026-09-20 — build-6 — the exact step-state schema `hx` validates against

`src/hx/stepstate.py` is the whole rule: `SCHEMA`, `ENTRY_SHAPES`, `WORKING_SET_FIELDS`.
Anything `validate` rejects never reaches `state/<id>/<stream>.json`, so the renderer can
trust every key below to be absent or the type named — never wrong-typed.

```
seq            int >= 0, required     the cursor into the raw stream; only moves forward
goal           str
constraints    [str]
decisions      [{d: str, why: str, ev: [int]}]
open_steps     [{id: str, intent: str, next: str, ev: [int]}]
closed_steps   [{id: str, outcome: str, verified: bool, commit: str, ev: [int]}]
dead_ends      [str]
blockers       [str]
subagents_open [str]
working_set    {files: [{path: str, note: str}], dirty: [str], commits: [str],
                last_failure: str, hypothesis: str}
prompt_version dict                   stamped by hx: the shas of BASE.md and the role file
ts             str                    stamped by hx, RFC 3339
```

Three things worth knowing:

- **Top-level keys are closed, entry keys are not.** A state with a top-level key not in that
  list is rejected outright, so your build-3 `**Other**` bucket stays empty at the top level.
  Inside a `decisions`/`open_steps`/`closed_steps` entry or `working_set`, only the keys above
  are type-checked and a Companion may add its own — keep rendering those generically.
- **`prompt_version` and `ts` are stamped after validation**, so they are on every file on
  disk even though a Companion never sends them.
- **`seq` is `max(what the model wrote, the log head)`** — hx owns the cursor — so it is safe
  to render as "caught up through record N". Under budget, `evict` drops `closed_steps` detail,
  then the oldest `dead_ends`, then closed-step working-set entries, then notes on untouched
  files; the shape never changes, only the contents shrink.
