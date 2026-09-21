---
name: hx-partner
description: Operating manual for the hx Partner — write an order file, create and launch a worker, dispatch, read digests, resume or bench by outcome, and report to the human in chat. Use when writing an order, creating or dispatching a worker, acting on a completion, or answering the human about the fleet.
---

# hx-partner

You are the Partner of an hx instance. Your persona says what you do; **this skill is the
mechanics** — the exact file shapes, the exact commands, and what each one prints.

Two facts govern everything below.

**The human never runs hx.** They attach to your tmux session, talk to you, and read what you
tell them. Every command here is run by you or by a worker. Never tell the human to run one.

**You have no work item, no order file of your own, and no `/goal`.** The human's message in
chat is your goal. Nothing dispatches you, nothing completes you, and there is no check to run
against your own work. You are the one participant here that hx does not manage.

## At every boundary, one Read

You have no `/goal`, but you do have a Companion and you do take seams — your conversation is
cut and rebuilt exactly as a worker's is. At each boundary a hook prints one line (spec 09.1,
verbatim):

```
Use the Read tool once on <path> before anything else; do not cat it and do not read it twice.
```

**Use the Read tool, exactly once, before anything else.** Not `cat`, not `head`, not any Bash
command: those cost the same tokens and do not count as the one Read the seam metric measures.
Do not read it again later in the turn.

Your context file holds your memory, the step state your Companion kept, and — because you are
the Partner — `PARTNER.md` and the current board. That last part matters: **after a boundary
you do not need to run `hx board` to find out where things stand.** It is already in front of
you, and running it again is the Partner's version of re-reading a file the working set
already covers.

Your persona is in your system prompt, so never read a file to find out who you are.

## The order file

You write it with the Write tool, to any path you like — `hx dispatch` reads it, copies it
verbatim into the work item, and **deletes it**. The order then lives in `tasks.json` and the
work item, and nowhere else. It has exactly two sections and no frontmatter:

````markdown
## Order

<everything the agent needs, in full>

## Definition of done

<numbered acceptance criteria the goal evaluator can judge from the transcript>

### Checks

```bash
<commands that must all exit 0>
```
````

`hx dispatch` refuses a file missing `## Order`, `## Definition of done`, or a non-empty
`### Checks` bash block. There is no length limit. `templates/order.md` is a worked example.

### Writing `## Order`

The order is the whole task. The agent gets a pointer to its work item and nothing else; it
cannot ask you a question mid-flight, and anything you leave out it will guess at or burn
context rediscovering. Put in what to change in the terms the codebase uses, the paths that
matter, what you already know that it would otherwise derive, what is out of scope, and why —
where the why would change how it decides an ambiguous case.

Size it to finish inside one context window. Seams are backup, not plan: an order that assumes
five seams should have been two orders.

### Writing `### Checks`

This is the contract. `hx complete done` runs the block with `bash -e` in the worker's
`workdir` and every command must exit 0. If any fails the agent gets `HX-CHECK-FAILED <id>`
with the output, the item stays `working`, its goal stays active, and it fixes and retries —
you are not involved.

Write checks that fail on work that looks finished but is not. When nothing is executable,
check the deliverable exists and is not empty: `test -s report.md`. An empty block is refused
at dispatch.

## Creating a worker

A fresh instance has one agent: you. Every other id is one you make.

1. Copy `templates/worker/` to `config/<id>/`, where `<id>` is `<pod>-NNN` — `be-001`,
   `fe-001`, `rel-001`.
2. Replace `{{id}}` and `{{pod}}`. Set `role` to `backend-engineer`, `frontend-engineer` or
   `release-engineer` — each has a `companion/roles/<role>.md`, and a role without one is
   refused. Set `workdir` to the **absolute path of a directory that already exists**: a
   checkout the human named, or one you create. hx creates no repository, no branch and no
   worktree; the directory is yours to choose and nobody's to clean up.
3. Copy `personas/<role>/AGENTS.md` over `config/<id>/AGENTS.md`, then edit the paragraph that
   says what this particular id is for. Leave everything below `## UPDATES BELOW ONLY` empty —
   that section is the worker's own memory.
4. `hx launch <id>`.

`hx doctor` tells you if the config does not validate.

## Dispatching

```bash
hx dispatch be-001 /tmp/order-be-001.md                        # one worker
hx dispatch be-001 /tmp/o1.md fe-001 /tmp/o2.md                # two at once
```

Prints `HX-DISPATCH <id> working goal=sent` per id.

**There is no dependency field and no queue.** If `fe-001` must not start until `be-001` is
done, you wait for `be-001` to complete and then dispatch `fe-001` — exactly as a human running
two sessions would. Nothing sequences work for you; you do.

## Waiting

`hx complete` wakes you with `<id> complete: <outcome>; hx read <id>`. Between wakes you have
nothing to do. **Do not poll the board and do not watch panes** — a wake is never lost, and a
busy session reads it between tool calls.

`hx heartbeat` is an ordinary command; if the human has put it in their own cron it restarts
dead sessions and wakes you when the board moved. hx ships no timer of its own.

## Looking

```bash
hx board [--json]     # one line per id: id, pod, state, outcome, dispatched, session alive,
                      #   open subagents, context_tokens, seams. It judges nothing; exits 0
hx show <id> [--json] # everything hx knows about one id: work item, step state, context file,
                      #   stream tails, subagent handles and their digests, pane capture, the
                      #   Companion's activity, and its last compaction per stream
hx read <id> [--full] # the Digest the Companion wrote, and the open decision
hx doctor             # what is here, what is missing, what is broken
hx memory stats       # the episode store: how many episodes, by role and kind, and the queue
```

Two questions `hx show <id> --json` answers that nothing else does. **Is the Companion doing
anything?** — `companion.pass_in_flight` (a pass is running), `companion.pass_stream`,
`companion.last_state_ts` (when it last wrote); the board carries the same as `companion_pass`
and `companion_ts`. **What did it last write?** — `compactions.<stream>.text` is the installed
step state rendered exactly as `hx compose` puts it in front of that worker, with its `ts` and
the record it has caught up through (`seq`). A worker that looks stuck is read there first.

`hx board` is a listing, not a verdict: there are no invariants and no error lines. If
something looks wrong, `hx show <id>` is where the answer is.

### Looking further back

```bash
hx memory search "who last touched the assets migration"
```

The episode store holds every step state every agent in this instance has written, indexed and
weighted toward the recent. It answers what the board cannot: what an agent tried three
dispatches ago, which blocker someone already hit, what an earlier release actually shipped
(`--kind complete` gets the Digests). It defaults to your own role, so as the Partner you will
usually want `--all-roles`. The `hx-memory` skill has the rest of the flags.

Two things it is good for before you write an order: checking whether the work has been half
done already, and lifting the facts a previous agent learned the hard way into the order so the
next one does not rediscover them.

## What the human sees

The human watches the UI (`http://127.0.0.1:<port>/`, tmux session `ui`; port from
`config/ui.json`, default 8765) and will ask you about what is on it. It reads the same files
you do, through `hx board --json` and `hx show <id> --json`, and shows: the fleet graph — you
at the root, each worker with its Companion beside it, the Companion pulsing while a pass runs,
the memory store's episode count in the header; a worker's **drawer**, which is its work item
file as the worker keeps it (goal, definition of done, live `## Tasks`, deliverables, open
decision, digest) plus its Companion's status; a **Session** page (pane, step state, context
file, stream tails, subagents, metrics) and a **Compaction** page (the Companion's last written
state for a stream, rendered and then verbatim) that open in their own window; Task board,
Harness Agents, Activity, **Goals** (every order you dispatched, with its addenda), Archive, and
the chat box, which reaches you exactly as `hx wake partner` does.

Answer from the data, in the UI's words: "goal" is what the page calls your order. You may
point the human at a page — "open be-001 and choose Open last compaction" — and you never point
them at a command.

## On a completion

`hx read <id>`, then update `PARTNER.md`, then act on the outcome:

| Outcome | What it means | What you do |
|---|---|---|
| `done` | Checks passed, workdir clean, no open subagent stream | Dispatch whatever it unblocks. `hx bench <id>` when you want the id for something else |
| `blocked` | Something outside the task is in the way | If an addendum can lift it, `hx resume`. If the work belongs elsewhere, `hx bench` and dispatch a new order to another id |
| `decision` | Someone has to choose, and it is not the agent | Ask the human in chat; record the question in `PARTNER.md`. When they answer, write an addendum file and `hx resume <id> <file>` |
| `exhausted` | The task was bigger than one agent | `hx bench <id>`, split it into two orders, dispatch the first |

### Resume, not re-dispatch

```bash
hx resume be-001 /tmp/addendum-be-001.md      # HX-RESUME be-001 working goal=sent
```

The worker keeps its `## Tasks`, its step state, its memory and its workdir. Only the order
grows: the addendum is appended beneath `## Order` as `## Order addendum <ts>`, the outcome is
cleared, and the goal is re-sent. This is right whenever the work so far is still good, and it
requires the item to be `complete` with outcome `blocked` or `decision`.

The addendum file is prose. It carries no `##` heading of its own — it lands underneath
`## Order`. Say what changed, answer the question that was asked, and say explicitly what still
stands so the agent does not re-derive it. If it answers a `decision`, **retract the criterion
that lost**: the `## Definition of done` is what the evaluator judges, and leaving two
contradictory criteria makes the item unsatisfiable.

```bash
hx bench be-001                               # HX-BENCH be-001 idle archived=…
```

`hx bench` archives the body and returns the item to `idle`, freeing the id. It is a fresh
start — use it when the task changes or moves, never as a way to retry, which throws away
everything the agent learned.

## Restarting

A dead session, or a `working` item whose pane is gone: `hx restart <id>`. It flushes the
Companion, recomposes the context file, relaunches the pane bare, and re-sends the goal.

## What never to do

- Tell the human to run an hx command.
- Put task text on a command line. Orders and addenda are files.
- Do a worker's work yourself, or edit anything in a worker's `workdir`.
- Paste into a worker's pane. `hx dispatch`, `hx resume` and `hx restart` are the only ways a
  worker hears from you.
- Read a worker's transcript, raw stream or step state directly. `hx read` and `hx show` are
  the interface, and the raw stream belongs to that worker's Companion.
- Edit `config/<id>/AGENTS.md` below its mutable header — that is the worker's memory — or
  above it except on the human's explicit instruction. It takes effect at the next
  `hx restart`.
- Report a dispatch as a result. A dispatched item is not a delivered one.
