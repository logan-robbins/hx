---
name: hx-worker
description: Operating manual for an hx HarnessAgent — read the context file once at every boundary, keep the work item's Tasks section current, commit as you go, use subagents, and finish with hx complete and its HX-CHECK-FAILED loop. Use when starting or resuming under a /goal pointer, after a seam or restart, when spawning subagents, or when completing or failing a completion.
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

**Use the Read tool, exactly once, before anything else.** Not `cat`, not `head`, not any Bash
command. A `Bash cat` of that path spends the same tokens, does not count as the one Read the
seam metric measures, and is recorded against you as waste — the harness is trying to find out
whether one composed file is enough, and a shell read makes the answer look better than it is.
Do not read it a second time later in the turn.

That path is your context file. It holds, in order:

1. Your memory — what you wrote below `## UPDATES BELOW ONLY` in your `AGENTS.md`
2. Your task — the verbatim `## Order` and every addendum
3. Your `## Tasks` section, as you last left it
4. Your step state — what your Companion recorded: open steps with the next action, closed
   steps with their commits, decisions, dead ends, the working set, blockers
5. Your open subagent handles

It is always current, it is composed fresh at each boundary, and it is the only file you need.
Do not search for context. Do not re-read a file your `working_set` already carries a note
about unless it has changed since — the note is there so you do not have to. One Read per
boundary, and the rest is work.

Your persona is not in it, because it is in your system prompt already.

### 2. The goal

`hx goal` pastes a fixed pointer into your pane:

```
/goal The order for <id> is in <abs path to work item>; read it first. Done when `hx complete <outcome>` has been run and its output line `HX-COMPLETE <id> <outcome>` appears.
```

The work item is the task. Read it. `## Order` and every `## Order addendum` are the whole of
what you were asked for, `## Definition of done` is what you will be judged on, and
`### Checks` is what will actually be run against you. `hx task` reprints the order and its
addenda at any time.

A `/goal` keeps you working past the end of a turn: after each turn an evaluator decides met /
not yet met / impossible. It reads tool results, so the proof of completion is a line hx
prints, not anything you claim.

### 3. `## Tasks` is yours, and it is what survives

The work item body is your running task list. You own `## Tasks`, `## Deliverables`,
`## Commands`, and `## Open decision`; hx owns the filename state and the `## Order` section.

- Mark a task done **the moment** it is done. Not at the end of the turn, not at the end of
  the task.
- Add a task **the moment** you discover it.
- Put the fact you needed right next to the task that needed it. "auth is wired in
  `src/api/mw.py:40`, not in the router" is worth more after a seam than any amount of prose.

`## Tasks` is what your context file carries back to you after a seam. A stale `## Tasks` is
the single most expensive mistake available to you here.

### 4. Commit as you go

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

### 5. Read a file once

If you are opening a file a second time, the note you should have written the first time is
missing. Write it now, in `## Tasks` next to the task that needed it. Your Companion keeps
files-read-but-not-changed in its working set with a one-line note each, and after a seam those
notes come back to you — that only works if the fact was worth recording when you had it.

### 6. Subagents

Use them freely, for bounded separable pieces: a survey, an independent module, a test pass.

- Each one gets its own context file automatically, built from `config/<id>/SUBAGENTS.md`, its
  own step state, and a pointer to its task. A hook hands it the path with the same line you
  get at a boundary, so it reads one file with the Read tool and starts. You do not have to
  brief it on the harness.
- **Its task is the message you spawned it with**, which is already in its conversation — the
  context file says so rather than repeating it. So that message is the whole of what it knows
  about the job: write it as you would write an order, not as a one-line handle.
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

### 7. What happens at the end of every turn

You will not see any of this, but it explains the shape of the rest.

A hook runs after each of your turns. It records the turn and whether background work is still
running; it delivers a `/goal` pointer if one was owed to your pane (which is how the Partner
dispatches itself from inside its own turn); and it takes a seam if one is pending and nothing
is running in the background.

Two consequences for you. **Finishing your turn is what lets the harness act** — a turn that
never ends is a seam never taken and a goal never delivered. And **background work delays a
seam**, not forever, but a seam is only taken when nothing is running, so leaving background
tasks alive across many turns keeps pushing it out.

### 8. Seams, and the Companion that makes them cheap

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
work is committed. Everything in sections 3, 4 and 5 exists for this moment.

### 9. Finishing

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

### 10. `HX-CHECK-FAILED`

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

Do not edit the `### Checks` block to make it pass — it is in `## Order`, it is the Partner's,
and `hx complete` runs the copy in `tasks.json`, not the one in your file.

### 11. When `done` is not available

`blocked`, `decision`, and `exhausted` run no checks. They are honest answers and using one is
better than forcing a `done` that is not true.

- `blocked` — something outside your task stops you: a missing dependency, an access you do not
  have, a broken thing you were told not to touch. Put what would unblock you in
  `## Open decision` first; the digest leads with it and the Partner's addendum answers it.
- `decision` — the order is ambiguous or two valid paths diverge and the choice is not yours.
  State the question and the options in `## Open decision`, with what each one costs.
- `exhausted` — the task was too large. Say where the natural split is.

All three keep everything: your `## Tasks`, your step state, your memory, your workdir, your
logs. The Partner resumes you with an addendum appended to your order, and you pick up exactly
where you stopped. You are not restarted.

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

You run, for yourself: `hx task`, `hx complete`. That is also the whole list.
