---
name: hx-worker
description: Operating manual for an hx HarnessAgent — read the context file once at every boundary, search the code graph and memory before building, keep the work item's Tasks section current, commit as you go, keep large outputs out of context (hx exec, content, sift, tool-exec, tools), use subagents, report typed progress in a planned run, and finish with hx complete and its HX-CHECK-FAILED loop. Use when starting or resuming under a /goal pointer, after a seam or restart, before writing new code, when a command's output is large, when spawning subagents, or when completing or failing a completion.
metadata:
  hx-audience: worker
  hx-roles: "*"
  hx-loads-when: starting or resuming under a /goal pointer, after a seam, before new code, completing or failing a check
---

# hx-worker

You are a HarnessAgent in an hx instance: a full Claude Code session, in your own tmux session,
with your own workdir, running under a `/goal`. This skill is the mechanics. Who you are came
with your system prompt at launch and is always there; you never look it up.

## Your lifecycle, start to finish

### 1. The boundary

Every conversation you have starts at a boundary: dispatch, resume, a seam, a restart, a
compaction. At every one of them a hook prints a single line (spec 09.1, verbatim):

```
Use the Read tool once on <path> before anything else; do not cat it and do not read it twice.
```

The global rules (`config/CLAUDE.md`) reach you once: as your CLAUDE.md on Claude, inside
your persona on Pi and Grok, and as the context file's first section (`## Invariants`) on
Codex and Meta — they are not restated here. One mechanics note the invariants do not carry:
use the Read tool (a Bash `cat` of that path does not count as the one Read the seam metric
measures).

That path is your context file. It holds, in order:

0. Invariants — Codex and Meta only; read them, do not restate them
1. Your memory — what you wrote below `## UPDATES BELOW ONLY` in your `AGENTS.md`
2. Your goal — the verbatim `## Goal` and every addendum
3. Your `## Tasks` section, as you last left it
4. Your step state — what your Companion recorded: open steps with the next action, closed
   steps with their commits, decisions, dead ends, the working set, blockers
5. Your open subagent handles

It is always current, it is composed fresh at each boundary, and it is the only file you need.
Do not search for context. Do not re-read a file your `working_set` already carries a note
about unless it has changed since — the note is there so you do not have to. One Read per
boundary, and the rest is work.

Your persona is not in it, because it is in your system prompt already.

**Reading section 4.** The step state is rendered dense: one tagged line per fact, no prose,
because you pay for it at every boundary. The tags:

```
goal:    what you are actually doing        commit <sha>: message
con:     a constraint that still holds      dirty:  uncommitted path
dec:     decision <- why [ev seqs]          file <path>: the fact from it — do not read it again
open <id>: an open step [ev seqs]           fail:   the last failure, verbatim
next <id>: the one action to take now       hypo:   the current theory about it
done <id> verified|UNVERIFIED <sha>: what   block:  something outside your task
dead:    tried, abandoned — do not retry    seq:    the stream cursor
```

`next` is the line to act on. `UNVERIFIED` means nothing proved it, so treat it as work, not
as done. `[1402,1409]` are evidence seqs in your raw stream; you never need them, but they are
what your Companion cited.

There may also be a **Memory episodes** section after it: what other agents in this instance
did on similar work. Read it before you go looking for anything — and see the `hx-memory`
skill for how to search further.

### 2. The goal

hx pastes a fixed pointer into your pane:

```
/goal The goal for <id> is in <abs path to work item>; read it first. Done when `hx complete <outcome>` has been run and its output line `HX-COMPLETE <id> <outcome>` appears.
```

The Work Item is the goal. Read it. `## Goal` and every `## Goal addendum` are the whole of
what you were asked for, `## Definition of done` is what you will be judged on, and
`### Checks` is what will actually be run against you. `hx task` reprints the goal and its
addenda at any time. The how is yours: decompose into `## Tasks`, fan out to subagents along
micro bounds with a clear task list each, and construct the deliverables yourself — the
Partner named the functional deliverables, never the procedure.

A `/goal` keeps you working past the end of a turn: after each turn an evaluator decides met /
not yet met / impossible. It reads tool results, so the proof of completion is a line hx
prints, not anything you claim.

### 3. Search before you build

The instance keeps a code graph of every repository it works on (`hx code`), an exact-text
store of docs and large outputs (`hx content`), and the fleet's episode memory (`hx memory`,
§6). Reading a file to edit it is fine; reading to explore goes through them. A recursive grep
or find over the tree, or a whole-file read of a large file, is a rule violation here, not a
style choice.

Before you write a type, function, service or interface, ask whether it already exists:

```bash
hx code type OrderRepository --expect exists        # the declaration: path, line, signature
hx code signature Namespace.OrderRepository.Add     # the exact header, whitespace-normalised
hx code callers Add --count                         # fan-in before you change a signature
hx code tests-for src/OrderRepository.cs            # the tests that already cover a path
hx code conform IOrderRepository                    # implementations, their tests, member diffs
hx code precedent AddScoped --language csharp       # a verbatim snippet of an existing usage
hx code contract IOrderRepository                   # a published interface, in every namespace
hx code search "integration event" --sift "Does this publish integration events?"
hx code map --budget 5000 --focus src/Orders/       # one ranked orientation map before the first edit
```

Every answer is JSON, capped (`--limit`, default 20), and names its provenance: `lane`
(`resolved`, `syntax`, `text` or `imported`), `extractor`, `anchor_sha` and `stale`. A stale
row is shown, not hidden — read the path at its current head before you trust it.
`--namespace REPO_ID` picks one repository when a name exists in several; `--owners` adds the
worker currently holding a lease on the path. Reuse the path and declaration the graph gives
you, and ask another scoped question only for a fact you still lack. A graph hit is a
candidate fact and a Jev score is a ranking: neither validates an application-map relation
or completes your goal, and nothing here replaces running the code.

`--expect exists|absent|one|clean|fresh` turns any query into a lint: exit 0 when the
expectation holds, 3 when it does not, 2 on a usage error. The Partner puts such lines in
your `### Checks` — no second interface (`hx code type NAME --expect absent`), a test boundary
(`hx code tests-for PATH --expect exists`), fresh write scope (`hx code fresh PATH --expect
fresh`). They run at `hx complete done`, and you never remove or weaken one; if a duplicate
name is the problem, rename onto the existing declaration. You never index: the graph is
rebuilt after each merge by the operator's hook and refreshed with your changed paths when
you complete.

For words rather than code — a spec, a vendor page, a design note — `hx content search
"cleanup pattern"` finds indexed text, and `hx content fetch URL --source NAME` indexes a page
and returns a preview, never the page.

### 4. `## Tasks` is yours, and it is what survives

The Work Item body is your running task list. You own `## Tasks`, `## Deliverables`,
`## Commands`, and `## Open decision`; hx owns the filename state and the `## Goal` section.

- Mark a task done **the moment** it is done. Not at the end of the turn, not at the end of
  the task.
- Add a task **the moment** you discover it.
- Put the fact you needed right next to the task that needed it. "auth is wired in
  `src/api/mw.py:40`, not in the router" is worth more after a seam than any amount of prose.

`## Tasks` is what your context file carries back to you after a seam. A stale `## Tasks` is
the single most expensive mistake available to you here.

### 5. Commit as you go

Commit each finished sub-task immediately, with a message that says what it does. `git log
--oneline` is your memory of what is done, and your Companion records the commit sha on the
closed step instead of describing the change.

Do not leave work uncommitted across a long stretch. `hx complete done` refuses a dirty
workdir when it is a git repository, and an uncommitted pile is the one state a seam cannot
carry for you.

You work in the directory `config/<id>/harness.json` names as your `workdir`. hx did not
create it, does not manage it, and will not clean it up: it is an ordinary directory, often a
checkout somebody already had. If it is a git repository, `hx complete done` requires it clean,
and that is the only git hx ever runs. Branching, pushing and merging are yours and the
human's, not the harness's.

### 6. Read a file once

If you are opening a file a second time, the note you should have written the first time is
missing. Write it now, in `## Tasks` next to the task that needed it. Your Companion keeps
files-read-but-not-changed in its working set with a one-line note each, and after a seam those
notes come back to you — that only works if the fact was worth recording when you had it.

Before you explore a part of the repository you do not know, spend one call on memory:

```bash
hx memory search "how the media routes build responses"
```

It searches every step state every agent in this instance has written, filtered to your own
role and weighted toward the recent. Another agent may already have learned what you are about
to spend ten Reads learning, or found out why the obvious approach does not work here. The
`hx-memory` skill has the flags, `--all-roles` among them; load it when you need more than the
one-line form above.

### 7. Subagents

Use them freely, for bounded separable pieces: a survey, an independent module, a test pass.

The boundary line names the read tool this session actually has: `Read` on Claude Code,
`read` on Pi. Delegation is the `Agent` tool on Claude Code and the `subagent` tool on Pi
(`prompt`, and an optional `agent_type`). The rest of this section is the same for both.

- Each one gets its own context file automatically, built from `config/<id>/SUBAGENTS.md`, its
  own step state, and a pointer to its task. A hook hands it the path with the same line you
  get at a boundary, so it reads one file with the Read tool and starts. You do not have to
  brief it on the harness.
- **Its task is the message you spawned it with**, which is already in its conversation — the
  context file says so rather than repeating it. So that message is the whole of what it knows
  about the job: write it as a goal with its own deliverables, not as a one-line handle.
- Each one gets its own stream, `logs/<id>/<id>-sNNN-open.jsonl`, renamed to `-closed.jsonl`
  when it stops, and its own Companion state. Handles are assigned under a lock, so three
  subagents starting at once get three streams rather than colliding on one.
- **Size each to finish inside one window.** No hook fires on a subagent's own compaction, so a
  subagent that compacts mid-task loses the harness's continuity support. Tell it to commit as
  it goes; `SUBAGENTS.md` already does, but scoping is yours.
- Results come back as a completion notification in a later turn, with a Companion-written
  digest of what it did, what it committed, and what it left open, handed to you on the
  `Agent` tool result. **That digest is all that crosses back** — not its transcript, not its
  stream, not its step state. Anything you need that the digest omits is gone.
- Subagents share your workdir. Give them non-overlapping scopes.

`hx complete` refuses while any subagent stream is still open, so let them finish.

### 8. What happens at the end of every turn

You will not see any of this, but it explains the shape of the rest.

A hook runs after each of your turns. It records the turn and whether background work is still
running, wakes your Companion, and takes a seam if one is pending and nothing
is running in the background.

Two consequences for you. **Finishing your turn is what lets the harness act** — a turn that
never ends is a seam never taken and a goal never delivered. And **background work delays a
seam**, not forever, but a seam is only taken when nothing is running, so leaving background
tasks alive across many turns keeps pushing it out.

### 9. Seams, and the Companion that makes them cheap

Your conversation will be cut and rebuilt. That is a **seam**: `/clear`, then the same boundary
you started at — the hook line, one Read, and your `/goal` again. It happens at a turn boundary
with no background work running, either because your context crossed a threshold or because
your Companion saw a good moment.

**Claude Code's own compaction is not what happens to you.** hx takes a seam long before the
native window is reached, so nothing summarises your conversation and decides for you what
mattered. What survives is what is on disk: your `## Tasks`, your commits, your memory below
the header, and the step state.

The **Companion** is a second session, paired with you, that reads your tool calls and keeps
that step state current. You never talk to it and it never talks to you. What it produces is
sections 4 and 5 of your context file:

- **open steps with a `next` action** — the sentence that tells you where to put your hands
  when you come back;
- **a working set** — commits, what is dirty, and *files you read but did not change, each with
  the one fact you took from it.*

That last part is why **re-reading a file your working set already covers is waste**, and it is
measured: the metric for whether all this works is what you do in the ten turns after a seam,
counting re-Reads of noted files against it. If a note is there, trust it. If a note turned out
to be wrong or the file has changed since, read it again and say so in `## Tasks` — that is not
the waste, that is the system working.

You do not trigger a seam, you do not prevent one, and you will not notice it happening — you
will simply find yourself at step 1 again. It costs nothing if `## Tasks` is current and your
work is committed. Everything in sections 4, 5 and 6 exists for this moment.

### 10. When something trips you up

A wrong assumption, a correction a reviewer had to make, a wasted build cycle, a gate that
passed on a subset — the moment you understand what happened, propose the rule, before you
move on and before you finish:

```bash
hx learn propose --category <c> --signal "<what you saw>" --check "<the test that decides it>" --evidence "<path, run, PR, date>"
hx learn propose --evidence-for L3 --evidence "<where it happened again>"   # an existing rule already says it
hx learn classify --signal "<what you saw>" --check "<the test>"            # which of the eleven kinds it is
```

Every rule already in force is in your system prompt — the `hx:learnings` region of your
persona, whole, category by category — at every start and after every compaction. Never read
the files (`LEARNINGS/<category>.md`, or the fleet's shared directory); `hx learn list <key>`
prints one rule when you want to quote it. You never write them: the proposal is reviewed by
a different session, which may refile it under another category, and that is what keeps each
lesson one rule. Signal is what the next agent will *see*; Check is something a script or a
reviewer can decide; the one-off detail goes in Evidence and nowhere else. A proposal that is
a story rather than a rule gets rejected with a reason you will be shown.

### 11. Finishing

```bash
hx complete done        # checks run, workdir must be clean, no open subagent stream
hx complete blocked     # something outside your task is in the way
hx complete decision    # someone else has to choose
hx complete exhausted   # the task was larger than one agent
```

**Before you run it**, write what should outlive this task below `## UPDATES BELOW ONLY` in
your `config/<id>/AGENTS.md`. That section is yours alone, it is not wiped by a dispatch, and
it travels in every future context file. Write what you would otherwise rediscover: how this
codebase is laid out, which commands actually work here, what surprised you. Nothing above
that header is yours; `start.sh` re-derives the persona from it at every launch.

`hx complete <outcome>` is your **last action**. Nothing after it. It prints
`HX-COMPLETE <id> <outcome>` as its last line, and that line, in your transcript, is what
proves you are done.

Report back simply: completed or blocked. The Partner trusts the persona and reads status,
not prose — the Digest your Companion writes is file memory for last-resort recall, not a
report you must compose. Put remaining how-to-reproduce facts in `## Open decision` only
when blocked or deciding; otherwise finish.

### 12. `HX-CHECK-FAILED`

`hx complete done` is machine-checked. If a check exits non-zero, the workdir is dirty, or a
subagent stream is open, it prints:

```
HX-CHECK-FAILED <id>
<the failing output>
```

and **changes nothing**. The item stays `working` and your goal stays active.

This is yours to fix. Not the Partner's, not a reason to complete with a different outcome.
Read the output, fix the actual problem, commit, and run `hx complete done` again. Retry as
many times as it takes.

Do not edit the `### Checks` block to make it pass — it is in `## Goal`, it is the Partner's,
and `hx complete` runs the copy in `tasks.json`, not the one in your file.

### 13. When `done` is not available

`blocked`, `decision`, and `exhausted` run no checks. They are honest answers and using one is
better than forcing a `done` that is not true.

- `blocked` — something outside your task stops you: a missing dependency, an access you do not
  have, a broken thing you were told not to touch. Put what would unblock you in
  `## Open decision` first; the digest leads with it and the Partner's addendum answers it.
- `decision` — the goal is ambiguous or two valid paths diverge and the choice is not yours.
  State the question and the options in `## Open decision`, with what each one costs.
- `exhausted` — the task was too large. Say where the natural split is.

All three keep everything: your `## Tasks`, your step state, your memory, your workdir, your
logs. The Partner resumes you with an addendum appended to your goal, and you pick up exactly
where you stopped. You are not restarted.

## The tools that keep your context small

No raw payload over about 5 KB belongs in your context; what you need from it is retrieved
exactly, on demand. Two of these work anywhere. The rest exist only inside a planned run
(`HX_CONTINUITY_RUN`, next section), because the ledger holds the events they refer to.

Anywhere:

- **`hx exec`** programs the analysis instead of reading the data: `hx exec --language python
  --code "..."` runs in a disposable directory and returns stdout only; `hx exec file big.log
  --language python --code "print(FILE_CONTENT.count('ERROR'))"` hands the file's bytes to the
  program without putting them in front of you; `hx exec batch --file commands.json --queries
  "failing tests,HTTP 500"` runs labelled shell commands, indexes every output and answers the
  queries in one call. Output over 100 KB always comes back as an index pointer.
- **`hx sift triage --file failed-output.txt`** asks the decision model (Jev) for a tentative
  failure category and which candidate checks to run first; `hx sift candidates --question
  "..." --file candidates.json` ranks a bounded set you nominated. Advisory only: a score never
  establishes absence, validates an edge or completes a task, and a provider failure hands
  your candidates back unsifted rather than guessing.

In a planned run:

- **`hx tool-exec --request ID -- CMD`** runs a command whose output may be large — a test
  suite, a build — inside your assignment and captures it (1 MiB per run, 16 MiB per task).
  You get a bounded verdict and event ids; `hx evidence EVENT_ID --offset 8000 --limit 8000`
  pages the captured text when one part matters. The same `--request` id with the same command
  returns the earlier result instead of running twice; the same id with a different command is
  refused, so use a fresh id per distinct run.
- **`hx tools`** is the facade over the MCP servers and operator skills registered in the
  instance's `config/capabilities.json`: `hx tools discover --query lint` lists matches,
  `hx tools load ID` brings one tool's schema (or a skill's body, up to 8 KiB) into reach,
  `hx tools call mcp:project:NAME --request lint-1 --input input.json` runs it with bounded
  arguments and a bounded response, and `hx tools status` shows what is loaded. Nothing is
  loaded until you ask, so the facade costs no prompt tokens until you use it.

## When your assignment came from the ledger

Some work reaches you not as a goal file but as a planned unit: the Partner validated a plan
with `hx plan`, the ledger assigned you one behaviour unit and took leases on its paths, and
the runtime launched you with `HX_CONTINUITY_RUN` set. The packet you read at the boundary
then carries the unit's `behavior` and `acceptance`, your `workdir`, the exact `write_paths`
you hold, the `checks` the runtime will run and the `outputs` you must commit. Three things
differ from a goal-file dispatch:

- **Progress is typed.** `hx progress --file /tmp/progress.json` submits a sparse update: the
  step you closed and the one now active (one active at a time), the exact `next` action, a
  `blocker` or `null`, the deliverable paths you declare, cited evidence ids — with the
  `expected_revision` the previous call returned (`hx progress --run RUN` reads it). These are
  claims; receipts come from the runtime's checks, not from you.
- **Scope is enforced.** A write outside `write_paths` pauses the run and keeps your leases;
  reporting progress does not clear it. Put what you needed to change, and why, in the
  `blocker`, and the Partner revises the plan. `hx complete blocked|decision` is refused under
  a run for the same reason: blockers and decisions are progress, and the Partner stops the
  unit.
- **Memory is off.** `hx memory` and `hx recall` are refused in a planned run; the packet, its
  map inputs and `hx evidence` are your sources, and a fact the packet should have carried is
  a packet defect to report, not something to go searching for. `hx complete done` is still
  your last action: it requests completion, you end the turn, and the runtime runs the
  assigned checks, drains the Companion and publishes the unit. `hx plan audit|finish|
  materialize` accept only your own run.

## What is not yours

`config/` other than the part of your own `AGENTS.md` below the header; `companion/`; `logs/`,
`state/`, `run/` and `archive/`; `tasks.json`; and every work item except your own `-working`
file.

**Nothing stops you.** There is no permission prompt and no guard hook: you run with bypass
permissions, and the only thing between you and another agent's files is this paragraph. That
is deliberate — a prompt nobody is there to answer is a hung agent — and it means the rule is
yours to keep rather than the harness's to enforce. Writing into `state/` or another id's work
item corrupts a running agent's memory, silently, and nothing will tell either of you.

Your own files are your `workdir`, your `-working` work item, and your `AGENTS.md` below the
header. That is the whole list.

You run, for yourself: `hx task`, `hx complete`, and `hx progress` in a planned run. That is
also the whole list.

## One example per command

Those act on your own record. The rest of your `hx help` column reads, searches and records, and
each one's `--help` ends with the same example as here (the `hx code` family prints plain
argparse usage instead; its flags are the same on every query):

```
hx task                                          # your own Work Item
hx complete done                                 # runs your checks
hx progress --file /tmp/progress.json            # planned runs: a sparse update
hx code type OrderRepository --expect exists      # the declaration: path, line, signature
hx code signature Namespace.OrderRepository.Add
hx code callers Add --count
hx code register IOrderRepository
hx code tests-for src/OrderRepository.cs --expect exists
hx code conform IOrderRepository --expect clean
hx code precedent AddScoped --language csharp
hx code contract IOrderRepository --expect absent
hx code fresh src/OrderRepository.cs --expect fresh   # the write scope is indexed at HEAD
hx code search "integration event" --sift "Does this publish integration events?"
hx code symbols 'RUN.CHECK-INPUT'                 # Natural and other case-folded names
hx code impact src/OrderRepository.cs             # symbols, referencing files, candidate tests
hx code edges src/OrderRepository.cs              # candidate import, call, implements and tests edges
hx code map --budget 5000 --focus src/OrderRepository.cs
hx tool-exec --request tests-1 -- python3 -m pytest -q   # planned run
hx evidence EVENT_ID --offset 8000 --limit 8000  # planned run: the next page of a captured output
hx tools discover --query lint                   # planned run; then: hx tools load ID
hx tools call mcp:project:NAME --request lint-1 --input input.json
hx tools status
hx exec --language python --code "print(1)"
hx sift triage --file failed-output.txt
hx content search "cleanup pattern" --limit 3
hx content fetch https://example.com/docs --source example-docs
hx memory search "conditional GET 304" --k 5
hx recall "alembic revision for the assets table"
hx discipline commit --repo .
hx discipline pull --repo . --expect fresh       # exit 3 while behind the principal branch
hx learn repeat --signal "tests pass locally, fail in CI"
hx learn classify --signal "tests pass locally, fail in CI" --check "run the suite with the CI env file"
hx learn propose --category testing --signal "tests pass locally, fail in CI" --check "run the suite with the CI env file" --evidence "PR 42, 2026-10-12"
hx learn list --category testing
```
