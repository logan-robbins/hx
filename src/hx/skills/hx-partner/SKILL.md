---
name: hx-partner
description: Operating manual for the hx Partner — write order files, dispatch a plan with after dependencies, read digests, resume or bench by outcome, and keep the fleet's invariants. Use when writing an order, dispatching or resuming any id including yourself, reading the board, acting on a completion, or answering the human about fleet state.
---

# hx-partner

You are the Partner of an hx instance. This skill is the mechanics: the exact files, the exact
commands, and what to do with each outcome. Who you are and how you should think is in your
system prompt; do not look for it here.

Two facts govern everything below.

**The human never runs hx.** They attach to your tmux session, talk to you, and read what you
tell them. Every command in this file is run by you, by a worker, or by `hx up` / `hx heartbeat`
on a timer. Never tell the human to run one. If something needs doing, do it.

**No task text is ever a command-line argument.** Orders and addenda are files. Agents receive
a pointer to a file, never prose in argv. If you are about to put a sentence of instruction
inside quotes on a command line, you are doing it wrong.

## The order file

`orders/<id>.md`. You write it with the Write tool. It has optional frontmatter and exactly two
sections:

````markdown
---
after: [eng-001]
---

## Order

<everything the agent needs, in full>

## Definition of done

<numbered acceptance criteria the goal evaluator can judge from the transcript>

### Checks

```bash
<commands that must all exit 0>
```
````

`hx dispatch` refuses the file if `## Order`, `## Definition of done`, or a non-empty
`### Checks` bash block is missing. The order has no length limit and is copied verbatim into
the work item.

### Writing `## Order`

The order is the whole task. The agent gets a pointer to its work item and nothing else; it
cannot ask you a question mid-flight, and anything you leave out it will either guess at or
burn context rediscovering. Put in:

- What to build or change, in the terms the codebase uses.
- The paths that matter, named. A path you know and withhold costs the agent a search.
- What you already know that it would otherwise have to derive — the shape of an existing
  module it should follow, the convention in force, the thing that looks wrong but is
  deliberate.
- What is explicitly out of scope, and what must not change.
- Why, when the why would change how it decides an ambiguous case.

Size it to finish inside one context window. Seams are backup, not plan: an order that assumes
five seams is an order that should have been two orders with an `after` between them.

### Writing `## Definition of done`

Numbered, concrete, and checkable from the transcript. The goal evaluator reads the
conversation including tool results, and judges met / not yet met / impossible against this
list. "Works correctly" is not a criterion; "`hx doctor --json` emits every check the text form
emits, and both exit 1 when any check fails" is.

### Writing `### Checks`

This is the contract. `hx complete done` runs the block with `bash -e` in the agent's worktree
(`HARNESS_ROOT` for your own item) and every command must exit 0. If any fails, the agent gets
`HX-CHECK-FAILED <id>` with the output, the item stays `working`, its goal stays active, and it
fixes and retries. You are not involved.

Write checks that fail on work that looks finished but is not:

```bash
.venv/bin/python -m pytest tests/test_doctor.py -q
.venv/bin/python -m hx doctor --json | .venv/bin/python -c 'import json,sys; json.load(sys.stdin)'
git diff --quiet HEAD -- docs
```

When nothing is executable, check the deliverable exists and is not empty: `test -s report.md`.
An empty block is refused at dispatch. Checks run in the worktree, so they see the agent's
branch, not yours.

Your own item's checks are usually `hx board --require-done eng-001 eng-002 qa-001`, which
exits 0 only when every listed id is `complete` with outcome `done`.

## Creating a worker

A fresh instance has exactly one agent: you. Every other id is one you create, and creating one
is three steps:

1. **Copy the template.** `templates/worker/` holds `AGENTS.md`, `SUBAGENTS.md`, and
   `harness.json`. Copy all three to `config/<id>/`, where `<id>` matches
   `[a-z]+-[0-9]{3}` — `eng-001`, `qa-002`, `rev-001`.
2. **Fill it in.** Replace `{{id}}` and `{{pod}}` throughout all three files. In `harness.json`
   set `role` to one that has a `companion/roles/<role>.md` (shipped:
   `engineer`, `reviewer`; `partner` is yours), and set `model` to a full id listed in
   `config/models.json` — never an alias. In `AGENTS.md`, **rewrite the indented paragraph**
   with what this id is actually for: its domain, the part of the codebase it owns, the
   judgement you want it to exercise. That paragraph is the whole difference between this agent
   and the next one, and it reaches it as system prompt in every turn.
3. **Leave everything below `## UPDATES BELOW ONLY` empty.** That section belongs to the agent.

Then `hx launch <id>`, and the id is ready to be dispatched. `hx doctor` will tell you if the
config does not validate.

Editing a persona later is rare and only on direct human instruction; it takes effect at that
worker's next `hx restart`.

## Dispatching a plan

```bash
hx launch eng-001                    # only if the id has no session yet; idempotent
hx dispatch eng-001 orders/eng-001.md eng-002 orders/eng-002.md qa-001 orders/qa-001.md
```

One call for the whole plan. Items whose `after` entries are all `done` go to `working` and get
their goal immediately; the rest go to `queued` with no goal, and hx promotes each one the
moment its last dependency completes `done`. You are not woken in between, and you do not
sequence dispatches by hand.

Readiness is `tasks.json[<dep>].outcome == "done"`. Re-dispatching a dependency resets its
outcome to `null`, so a stale completion never satisfies a newer dependent.

Use `after` for real dependencies only — one item genuinely needs another's output. Two items
that merely touch the same area are not dependent; give them separate write scopes instead and
run them together.

## Dispatching yourself

An ask from the human becomes your own order, exactly like a worker's:

1. Write `orders/partner.md`: `## Order` capturing what they want, `## Definition of done` with
   `### Checks` that prove it (`hx board --require-done …`).
2. `hx dispatch partner orders/partner.md`.

Your pane is mid-turn when you run it, so the pointer lands in `run/partner/goal-pending` and
your `stop` hook pastes it at the end of the turn. From the next turn you are working under a
goal. Nothing is archived and nothing is wiped — your session, streams, and step state are
continuous. Human prompts in the same chat keep working at any point; the goal just means you
are no longer waiting to be prompted.

## Watching

```bash
hx board                 # one line per id, partner first, then invariant errors; exit 1 on any error
hx board --json          # same, structured
hx show <id>             # work item, step state, context file, stream tails, metrics, subagents
hx metrics <id>          # per-seam tool-call counts; how well continuity is actually working
hx doctor                # tmux, git, pinned claude binary and version, credentials, homes, mirror
```

You are woken when something changes: `hx complete` sends you `<id> complete: <outcome>;
hx read <id>`, and `hx heartbeat` (system cron, every 15 minutes) wakes you with a board diff
when a `working` or `queued` item exists and the board changed. You do not need to poll. Do not
build a loop that checks the board on a timer — one already exists, outside your session, and
it survives your seams.

**Board invariant errors are yours to fix.** A `working` item with a dead session or no goal
marker means that agent is not actually working: `hx restart <id>`. It flushes, recomposes the
context file, relaunches the pane bare, and sends the goal once the pane is ready.

## On a completion

```bash
hx read <id>             # the Digest the Companion wrote for you
hx read <id> --full      # the whole work item body when the Digest is not enough
```

Read it before acting, then update `PARTNER.md` — what landed, what is open, what you learned.
`PARTNER.md` is your memory and it is the only place a benched item's story survives.

Then act on the outcome:

| Outcome | What it means | What you do |
|---|---|---|
| `done` | Checks passed, worktree clean, no open streams | Dependents were already promoted by hx. `hx bench <id>` once you have consumed the digest, freeing the id for the next order |
| `blocked` | Something outside the task is in the way | If an addendum can lift it, `hx resume`. If the work belongs to another id or another shape, `hx bench <id>` and dispatch a new order |
| `decision` | Someone has to choose, and it is not the agent | Ask the human in chat; record the question in `PARTNER.md`. When they answer, write `orders/<id>.addendum.md` and `hx resume <id> orders/<id>.addendum.md` |
| `exhausted` | The task was bigger than one agent | `hx bench <id>`, split into two order files with an `after` between them, dispatch both |

### Resume vs. bench

```bash
hx resume <id> orders/<id>.addendum.md
```

`hx resume` continues the *same* task: the agent keeps its `## Tasks`, its step state, its
memory, its worktree, and its logs. Only the order grows — the addendum is appended beneath
`## Order` as `## Order addendum <ts>`, the outcome is cleared, the item goes back to
`working`, and the goal is sent. This is the right move whenever the work so far is still
good. It requires the item to be `complete` with outcome `blocked` or `decision`.

The addendum file is prose. It does not repeat the order and it carries no `##` headings of
its own — it lands underneath `## Order` in the work item. Say what changed, answer the
question that was asked, and tell the agent explicitly what still stands so it does not
re-derive it.

```bash
hx bench <id>
```

`hx bench` archives the body to `pods/<pod>/archive/<id>-<ts>.md`, resets the item from the
template, and returns it to `idle`. It is a fresh start and it does not touch `tasks.json`.
Use it when the task itself changes or moves to another id — never as a way to "retry", which
throws away everything the agent learned.

You can resume yourself the same way: `orders/partner.addendum.md`, then
`hx resume partner orders/partner.addendum.md`. The goal lands in `goal-pending` and your
`stop` hook delivers it, exactly as with self-dispatch.

## Finishing your own item

When the plan is done, run `hx complete done` yourself. Your `### Checks` — the
`hx board --require-done …` — prove it, your Companion writes your `## Digest`, and then you
report to the human in chat, in their terms, not in ids and states. When their next ask
arrives: `hx bench partner`, a new `orders/partner.md`, and dispatch yourself again.

If the human is away and you hit something only they can settle, `hx complete decision` pauses
your goal cleanly rather than burning check-ins. Nothing is lost. When they answer, write
`orders/partner.addendum.md` and resume yourself.

## Rarely

```bash
hx push <id>             # git push upstream agent/<id> from the mirror — the ONLY command that
                         # touches the user's remote. Only on explicit human instruction.
hx repo add <url|path>   # add a bare mirror and record it in config/repo.json
```

Editing a worker's persona — the part of `config/<id>/AGENTS.md` above `## UPDATES BELOW ONLY`
— or its `SUBAGENTS.md`, is yours, but only on direct human instruction. It takes effect at
that worker's next `hx restart`. Never touch anything below the header: that is the agent's own
memory. Never edit another id's work item, `tasks.json`, or anything under `logs/`, `state/`,
`run/`, or `archive/`; hooks will refuse you and they are right to.

## What never to do

- Tell the human to run an hx command.
- Put task text on a command line.
- Do a worker's work yourself. You have no worktree. The fix is a better order.
- Poll the board on a timer; the heartbeat already does it.
- `hx bench` an item whose digest you have not read.
- Report a dispatch as a result. A dispatched item is not a delivered one.
