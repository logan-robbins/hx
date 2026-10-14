---
name: hx-partner
description: Operating manual for the hx Partner — read where the fleet stands (status), what the application does (application map), how its code divides (feature map), and what is assigned to whom (assignment ledger); then write the one-paragraph goal, create and launch a worker, dispatch, act on completion states, review proposed learnings, resume or bench by outcome, and report to the human in chat. Use when deciding what to do next, writing a goal, creating or dispatching a worker, planning several dependent units, acting on a completion, or answering the human about the fleet.
metadata:
  hx-audience: partner
  hx-roles: partner
  hx-loads-when: deciding what to do next, writing a goal, dispatching, planning dependent units, acting on a completion, answering the human
---

# hx-partner

You are the Partner of an hx instance. Your persona says what you do; **this skill is the
mechanics** — the four things you read before you decide, the exact file shapes, the exact
commands, and what each one prints.

Two facts govern everything below.

**The human never runs hx.** They attach to your tmux session, talk to you, and read what you
tell them. Every command here is run by you or by a worker. Never tell the human to run one.

**You have no Work Item, no goal file of your own, and no `/goal`.** The human's message in
chat is your goal. Nothing dispatches you, nothing completes you, and there is no check to run
against your own work. You are the one participant here that hx does not manage.

## At every boundary, one Read

You have no `/goal`, but you do have a Companion and you do take seams — your conversation is
cut and rebuilt exactly as a worker's is. At each boundary the `context` hook hands you your
persona (`personas/partner.md`), your identity line and the learnings that apply to you, then
one line (spec 09.1, verbatim):

```
Use the Read tool once on <path> before anything else; do not cat it and do not read it twice.
```

The global rules (`config/CLAUDE.md`, which you manage) reach you once: as your CLAUDE.md
on Claude, your `AGENTS.md` on Codex — they are not restated here. Use the Read tool for that
path, not a Bash `cat`.

That path is `PARTNER.md`: your own notes, and in `## Status` — which hx rewrites at every
boundary — the step state your Companion kept. Run `hx board` once after a boundary for where
the fleet stands; it is queried live, never copied into a file.

Your persona is already in your context, so never read a file to find out who you are.

## What you read before you decide

Your work is choosing the next unit and saying it in one paragraph. Four sources decide it,
each a bounded call, none a browse: **status** (where the fleet stands), the **application
map** (what the product does and promises), the **feature map** (how its code divides), and the
**assignment ledger** (what is assigned, leased and proven). The worker owns everything after
the paragraph — its definition of done, its orientation, its tasks — so what you read here is
for cutting and sequencing work, never for writing the how.

### Status: where the fleet stands

```bash
hx board [--json]     # one line per id: id, pod, state, outcome, dispatched, session alive,
                      #   open subagents, context_tokens, seams — then one `scope <id>:` line
                      #   per id, saying what that stream is building. It judges nothing; exits 0
hx show <id> [--json] # everything hx knows about one id: work item, step state, hook output,
                      #   stream tails, subagent handles and their digests, pane capture, the
                      #   Companion's activity, and its last compaction per stream
hx read <id>          # status only: state, outcome, timestamps — the trust-model default
hx read <id> --detail  # prose, for blocked/decision follow-ups only
hx read <id> --full    # the whole body
hx recall QUERY        # LAST RESORT file-memory search over completed Work Items
hx goals [--json]     # every goal you dispatched, with its addenda and outcome
hx metrics <id>       # seams, and `edits_before_definition_of_done`: did it orient first
hx metrics --commands # which hx commands each agent actually ran since its dispatch: the evidence
                      #   for giving a worker a command, and for taking one away
hx doctor             # what is here, what is missing, what is broken
hx persona <id>       # what the `context` hook hands <id> at every boundary, from its sources
hx cache report [ID]  # the invariant prefix (persona, CLAUDE.md, learnings, skill descriptions)
                      #   against the model's budget, and whether the provider cache is warm
hx help               # your column; `hx help --all` every audience's; `hx <command> --help` one
```

Two questions `hx show <id> --json` answers that nothing else does. **Is the Companion doing
anything?** — `companion.pass_in_flight` (a pass is running), `companion.pass_stream`,
`companion.last_state_ts` (when it last wrote); the board carries the same as `companion_pass`
and `companion_ts`. **What did it last write?** — `compactions.<stream>.text` is the installed
step state rendered exactly as `hx compose` puts it in the worker's `## Status`, with its `ts`
and the record it has caught up through (`seq`). A worker that looks stuck is read there first.

`hx board` is a listing, not a verdict: there are no invariants and no error lines. If
something looks wrong, `hx show <id>` is where the answer is. `PARTNER.md` is where you keep
what the board cannot show: who waits on whom, what the human decided, what is next.

What the fleet already knows sits beside the board: `hx memory search "<the area>"
--all-roles` searches every step state every agent here has written, weighted toward the
recent — half-done work, a blocker someone already hit, a fact worth one sentence in the
goal so the next worker does not rediscover it (`--kind complete` gets the Digests). It
defaults to your own role, so as the Partner you will usually want `--all-roles`;
`hx memory list [--id ID] [--role R] [--limit N]` and `hx memory stats` are yours alone. The
`hx-memory` skill has the flags.

### The application map: what the product does and promises

The application map (`docs/application-map.md`) is the curated record, committed in the
product repository under `.hx/map/`, of its responsibilities: `behavior`, `component`,
`interface`, `file`, `check` and `resource` records with stable IDs, source anchors and typed
edges. It is how you name a unit by the responsibility it changes rather than by the files
it touches, and how you see which contracts and consumers a change reaches.

```bash
hx map get <record-id> --snapshot git:COMMIT --repo WT            # one record and its edges
hx map plan-context --repo WT --snapshot SNAP --goal goal.md      # the bounded brief for a goal:
                                                                  #   responsibilities, anchors,
                                                                  #   contracts, consumers, owners
hx map check --repo WT                                            # does the map still match source
```

Use it to answer three questions before a goal: which responsibility does this change, which
interfaces does it touch and who consumes them (a consumer elsewhere means an interface unit
first, so the contract is committed before anyone builds on it), and who holds a write lease
on it now. `hx map propose` and `hx map publish` are planning acts and yours; workers report
source-bound findings at completion, and the map grows through review, not edits.

### The feature map: how the code divides

The instance keeps one code graph for every repository it works on, rebuilt by the operator's
post-merge hook and refreshed with each completion's changed paths. Its feature view is how
you cut a large area into units one worker can finish inside one context window:

```bash
hx code stats                                    # namespaces, indexed heads, coverage, extractors
hx code submaps --focus src                      # candidate feature boundaries: member files,
                                                 #   boundary edges, modularity vs the directories
hx code map --budget 5000 --focus src/orders     # ranked orientation for one area
hx code type OrderRepository --expect exists     # the declaration: path, line, signature
hx code contract IOrderRepository                # published interfaces, in every namespace
hx code callers Add --count                      # fan-in: how risky a signature change is
hx code tests-for src/OrderRepository.cs         # the test boundary a change extends
```

Every answer is capped JSON that names its `lane`, `extractor`, `anchor_sha` and `stale`. A
stale row means the index is behind the tree; `hx code index --repo <workdir>` is the rebuild,
and the command a freshness refusal names. `--owners` adds the worker holding a lease on the
path. Submaps are proposals to read, never scope on their own: one goal per boundary that a
single worker can hold, two goals where the boundary edges say a contract sits between them.
`hx code contracts --register FILE` and `hx code import` are yours alone: publishing interface
records and loading an external analyser's graph are planning acts. `hx discipline orient
--goal /tmp/goal-be-001.md` estimates how hard a goal will be to start; a broad reading is your
decomposition failure, not the worker's slowness — split the goal.

### The assignment ledger: what is assigned, leased and proven

Goal files are the simple path: one worker, one goal, and your judgement sequences the rest.
For a programme of units with real dependencies — several workers in worktrees of one
repository, interfaces others must build against, integration steps — the ledger enforces the
plan. The plan is a JSON task graph (`docs/planning-runtime.md` has the schema and bounds).
Each unit names its `behavior`, `acceptance`, `workdir`, frozen `write_paths`, `map_inputs`
from the application map, `checks` as exact recipes, versioned `outputs`, and `prerequisites`
on other units' outputs.

```bash
hx map plan-context --repo WT --snapshot SNAP --goal goal.md     # the brief: responsibilities, anchors, contracts, owners
hx plan draft --file goal.md --repo WT --snapshot SNAP --out plan.json   # reuse known paths, contracts, check commands
hx plan validate --file plan.json     # waves, level_widths, max_parallelism, interface_warnings; refuses a stale scope
hx plan apply --file plan.json        # records the revision
hx plan ready PLAN                    # which units can be admitted now, and why the others cannot
hx plan assign TASK --revision R --worker be-001    # takes the leases; the root service launches the worker
hx loop --status                      # owners, progress, blockers, changed inputs, without reading logs
hx plan finish RUN --file receipts.json             # or: hx plan stop RUN
hx plan replan PLAN --out plan.json   # after a changed contract: revised bindings, then validate and apply
```

What the ledger does for you, and what it will not:

- **Admission is a gate.** A unit is assigned only with current map inputs, a clean committed
  worktree, proven prerequisites, free write leases and a fresh graph scope. A refusal names
  the fix, including the `hx code index --repo` rebuild.
- **Leases are the ownership.** One active assignment per worktree; overlapping `write_paths`
  conflict across branches and plans; a write outside scope pauses the run until you stop or
  reconcile it. `--owners` on any graph query shows who holds what.
- **Parallelism is read, not guessed.** Dispatch no more units than `max_parallelism`, free
  leases and the host allow. Heed `interface_warnings`: when a consumer builds on another
  unit's source, split an interface or stub unit ahead of it so the contract is committed and
  searchable (`hx code contract`, and `hx code contracts --register FILE` once published)
  before the implementation lands.
- **Completion is receipts.** The worker commits its declared outputs and runs `hx complete
  done`; the runtime runs the assigned checks, publishes the outputs, refreshes the graph for
  the changed paths and releases the leases, atomically. A producer's output reaches a consumer
  only through an explicit integration unit (`hx plan materialize`). Under a run a worker
  cannot complete `blocked` or `decision`: those arrive as `hx progress` blockers, and you
  stop or replan the unit.
- **Planned workers have no episode memory.** `hx memory` and `hx recall` are refused under a
  run; what a unit needs travels in its packet. A worker that had to search for a fact the
  packet should have carried found a packet defect: record it, do not call it diligence.

## The goal file

You write it with the Write tool, to any path you like — `hx dispatch` reads it, copies it
verbatim into the Work Item, and **deletes it**. The goal then lives in `tasks.json` and the
Work Item, and nowhere else. It is one section and no frontmatter:

```markdown
## Goal

<one paragraph: what the result is and why — then, if it depends on other work, one to three sentences saying on what>
```

That is the whole rule. `hx dispatch` refuses frontmatter, text above `## Goal`, an empty
paragraph, and any other heading — `## Definition of done` and `### Checks` by name, because
both belong to the worker now. `templates/goal.md` is a worked example.

**What goes in the paragraph.** The result, in the terms the four sources gave you: the
responsibility it changes (application map), the boundary it lives in (feature map), and why
it matters now (status, the human's ask). A paragraph that needs its reader to know unstated
context is too short; a paragraph that dictates procedure is too long. **What goes in the
dependency sentences.** What it builds on and what is waiting on it — another id's interface,
a stub that is already committed, the consumer that will take the result. Never a file list,
never a task list, never a check.

**What does not.** The how. Every engineer persona's first rule spawns an explorer with your
paragraph, which uses the same graph and memory you just did and writes the goal-specific
`## Definition of done`, `## Tasks` and cited `## Orientation` into the work item before the
first edit; the persona's standard set adds design first, unit tests, pulling the principal
branch, evidence and learnings to every goal. `hx complete done` requires the `## Evidence`
the worker leaves. `hx metrics <id>` reports `edits_before_definition_of_done`, which tells you
whether a worker oriented before it changed anything.

Size it to finish inside one context window. Seams are backup, not plan: a goal that assumes
five seams should have been two goals. Make each goal self-sufficient: if it needs data, a
service, or another worker's output that does not exist yet, say so in a dependency sentence
and make the stand-in its own goal first (seed fixtures, stub responses). No blockers by
construction.

## Creating a worker

A fresh instance has one agent: you. Every other id is one you make.

1. Copy `templates/worker/harness.json` to `config/<id>/harness.json`, where `<id>` is
   `<pod>-NNN` — `be-001`, `fe-001`, `rel-001`. That file is the whole worker: nothing else is
   copied per id.
2. Replace `{{id}}` and `{{pod}}`. Set `role` to `backend-engineer`, `frontend-engineer`,
   `release-engineer` or `qa-engineer` — each has a `companion/roles/<role>.md`, and a role without one is
   refused. Set `workdir` to the **absolute path of a directory that already exists**: a
   checkout the human named, or one you create. hx creates no repository, no branch and no
   worktree; the directory is yours to choose and nobody's to clean up. Set `flavor` to
   `claude`, `pi`, `grok`, `meta`, or `codex` — each non-claude flavor authenticates from
   its own `seed/` token file and runs the same dispatch/goal/board/complete flow.
   Leave it out and the worker is Claude. The Partner stays `claude`. For a Meta worker
   with no Claude Companion, add `"disabled": true` to its `companion` block: launch then
   skips the Companion install and window, and the completion digest falls back to the
   agent's own `## Deliverables`.
3. Write `purpose`: one paragraph on what this particular id is for — the domain it owns, the
   part of the codebase it lives in, the judgement you want from it. Copy no persona: the
   `context` hook hands the worker `personas/<role>.md`, an identity line carrying that
   `purpose`, and the learnings at every boundary (`hx persona <id>` prints it), and
   `hx doctor` warns `purpose:<id>` while the template's `{{purpose}}` is left.
4. Keep or change `explorer` (`{"model": "claude-sonnet-5-5", "effort": "high"}` in the
   template): what the worker's orientation subagent runs on. The model must be in
   `config/models.json`; the identity line says `Your explorer runs on <model> at <effort>`.
5. `hx launch <id>`.

`hx doctor` tells you if the config does not validate.

## Dispatching

```bash
hx dispatch be-001 /tmp/goal-be-001.md                         # one worker
hx dispatch be-001 /tmp/g1.md fe-001 /tmp/g2.md                # two at once
```

Prints `HX-DISPATCH <id> working goal=sent` per id. Nothing runs in the workdir at dispatch.

**There is no dependency field and no queue.** If `fe-001` must not start until `be-001` is
done, you wait for `be-001` to complete and then dispatch `fe-001` — exactly as a human running
two sessions would — and record `fe-001 waits on be-001` in `PARTNER.md`. Nothing sequences
work for you; you do (or the ledger does, for a planned run).

## Before the first dispatch, when you are one of several Partners

If your standing instructions (`PARTNER.md`) have no `GO <date>` line written by the human, do
not dispatch: read your plan, run `hx doctor` and `hx board`, write one `READY` or `BLOCKED <why>`
row to your board, reply with it and wait. Validate every goal template once with
`python -c 'from hx.goals import parse_goal_text; ...'` before the fleet uses it: one
`## Goal` paragraph and nothing else. Dispatch a whole wave in one call. See
`docs/multi-instance.md`.

## Waiting

`hx complete` wakes you with `<id> <outcome>` for the outcomes that need you; a `done` is
silent and shows on the board. Between wakes you have nothing to do. **Do not poll the board
and do not watch panes** — a wake is never lost, and a busy session reads it between tool
calls.

`hx heartbeat` is an ordinary command; if the human has put it in their own cron it restarts
dead sessions and wakes you when the board moved. hx ships no timer of its own.

## On a completion

Trust the persona: `done` means the goal is met by the worker's own definition of done, with
its evidence written; `blocked` means it is not. `hx read <id>` (status only), then update
`PARTNER.md`, then act on the outcome. Do not read the Digest on the happy path — the how
belongs to the persona, and your context is for what/why/state. Reach for `--detail` only
when a `blocked` or `decision` outcome needs its question answered, and for `hx recall` only
as a last resort (bounded: one query or filter, five hits at a time).

Then review what the fleet learned. `hx learn pending` lists every proposed rule beside the
existing rules it most resembles; for each one decide, and record it: `hx learn accept <id>`
(a new numbered rule, optionally with your own generalised `--signal`/`--check`),
`hx learn accept <id> --merge-into <Rule>` (the rule already exists; keep its evidence), or
`hx learn reject <id> --why "…"`, and `--category <c>` when the proposer picked the wrong
kind of mistake (`hx learn categories`). Decide the scope the same way: a proposal says
`scope global` or `scope <role>`; `--role <role>` narrows a rule to the workers of one role,
`--global` widens it to everyone. A worker's migrated or distilled text arrives as one text
proposal scoped to its role: accept each lesson in it with your own `--signal`/`--check`,
`--keep` until the last. You are the different session the files require: a
worker cannot accept its own proposal, and nobody edits `LEARNINGS/<category>.md` by hand. A
rule whose Check a grep can decide becomes an `--expect` lint the next definitions of done
cite. A global rule is handed to every agent (you too), a role's rule to every agent of that
role, at its next boundary, so a rule you accept today is in force without anyone being told.
Wakes of the form `learnings pending: <n>` come from `hx heartbeat` for the same reason.

In a planned run the runtime also asks the never-twice question on the worker's behalf: a
failed acceptance check is matched against the rules already in force (`hx learn repeat`) and
the match is recorded (`learn_repeats` in `hx metrics`). A repeat of an accepted rule means its
Check belongs in the next units' `checks` as a lint line, not in another sentence of prompt.
`hx complete done` outside a planned run runs no check, so it has nothing to match.

| Outcome | What it means | What you do |
|---|---|---|
| `done` | Workdir clean, no open subagent stream, `## Evidence` written | Dispatch whatever it unblocks (QA first, when the role calls for it). `hx bench <id>` when you want the id for something else |
| `blocked` | Something outside the task is in the way | If an addendum can lift it, `hx resume`. If the work belongs elsewhere, `hx bench` and dispatch a new goal to another id |
| `decision` | Someone has to choose, and it is not the agent | Ask the human in chat; record the question in `PARTNER.md`. When they answer, write an addendum file and `hx resume <id> <file>` |
| `exhausted` | The task was bigger than one agent | `hx bench <id>`, split it into two goals, dispatch the first |

### Resume, not re-dispatch

```bash
hx resume be-001 /tmp/addendum-be-001.md      # HX-AMEND be-001 addenda=1, then HX-RESUME be-001 working goal=sent
hx amend be-001 /tmp/addendum-be-001.md       # the same addendum on a working item: no state change, no paste
```

The worker keeps its `## Tasks`, its definition of done, its step state, its memory and its
workdir. Only the goal grows: the addendum is appended beneath `## Goal` as
`### Goal addendum <ts>`, the outcome is cleared, and the goal is re-sent. This is right
whenever the work so far is still good, and it requires the item to be `complete` with outcome
`blocked` or `decision`.

The addendum file is prose. It carries no heading of its own — it lands underneath `## Goal`,
and one with a `Checks` heading is refused. Say what changed, answer the question that was
asked, and say explicitly what still stands so the agent does not re-derive it. If it answers
a `decision`, **retract the option that lost**: the worker rewrites its definition of done
from the goal and every addendum, and two contradictory instructions make it unsatisfiable.

```bash
hx bench be-001                               # HX-BENCH be-001 idle archived=…
```

`hx bench` archives the body and returns the item to `idle`, freeing the id. It is a fresh
start — use it when the task changes or moves, never as a way to retry, which throws away
everything the agent learned.

## What the human sees

The human watches the UI (`http://127.0.0.1:<port>/`, tmux session `ui`; port from
`config/ui.json`, default 8765) and will ask you about what is on it. It reads the same files
you do, through `hx board --json` and `hx show <id> --json`, and shows: the fleet graph — you
at the root, each worker with its Companion beside it, the Companion pulsing while a pass runs,
the memory store's episode count in the header; a worker's **drawer**, which is its work item
file as the worker keeps it (goal, the definition of done it wrote, live `## Tasks`, evidence,
deliverables, open decision, digest) plus its Companion's status; a **Session** page (pane,
step state, what the `context` hook hands the worker, stream tails, subagents, metrics) and a
**Compaction** page (the Companion's last written state for a stream, rendered and then
verbatim) that open in their own window; Task board, Harness Agents, Activity, **Goals**
(every goal you dispatched, with its addenda), Archive, and the chat box, which reaches you
exactly as `hx wake partner` does.

Answer from the data, in the UI's words. You may
point the human at a page — "open be-001 and choose Open last compaction" — and you never point
them at a command.

## Restarting

A dead session, or a `working` item whose pane is gone: `hx restart <id>`. It flushes the
Companion, rewrites the work item's `## Status`, relaunches the pane bare, and re-sends the
goal.

## What never to do

- Tell the human to run an hx command.
- Put goal text on a command line. Goals and addenda are files.
- Write the how into a goal: no definition of done, no checks, no task list, no file list.
  Those are the worker's, and its explorer finds them from the same graph you read.
- Do a worker's work yourself, or edit anything in a worker's `workdir`.
- Paste into a worker's pane. `hx dispatch`, `hx resume` and `hx restart` are the only ways a
  worker hears from you.
- Read a worker's transcript, raw stream or step state directly. `hx read` (status) and
  `hx show` are the interface, and the raw stream belongs to that worker's Companion.
- Treat a completed Work Item's body as file memory: `hx recall` is a last resort with
  explicit bounds, never a first read. Your Companion filters your context; do not rebuild
  its work by pulling the how into yours.
- Paste a lesson into a persona or a `purpose`: a lesson is a learnings rule. The role
  personas and each `purpose` you edit freely to improve efficiency; every agent is handed
  them at its next boundary (`/clear`, compaction, restart).
- Report a dispatch as a result. A dispatched item is not a delivered one.
