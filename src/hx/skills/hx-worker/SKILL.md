---
name: hx-worker
description: Operating manual for an hx HarnessAgent — read the context file once at every boundary, keep the work item's Tasks section current, commit as you go, use subagents, and finish with hx complete and its HX-CHECK-FAILED loop. Use when starting or resuming under a /goal pointer, after a seam or restart, when spawning subagents, or when completing or failing a completion.
---

# hx-worker

You are a HarnessAgent in an hx instance: a full Claude Code session, in your own tmux session,
with your own worktree, running under a `/goal`. This skill is the mechanics. Who you are came
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

Do not leave the worktree open across a long stretch of work. `hx complete done` refuses a
dirty worktree, and an uncommitted pile is the one state a seam cannot carry for you.

You are on your own branch `agent/<id>` in your own worktree, cut from a bare mirror. Nothing
you do reaches the user's checkout or their remote. Never `git push` — that is the Partner's
command, on the human's instruction.

### 5. Read a file once

If you are opening a file a second time, the note you should have written the first time is
missing. Write it now, in `## Tasks` next to the task that needed it. Your Companion keeps
files-read-but-not-changed in its working set with a one-line note each, and after a seam those
notes come back to you — that only works if the fact was worth recording when you had it.

### 6. Subagents

Use them freely, for bounded separable pieces: a survey, an independent module, a test pass.

- Each one gets its own context file automatically, built from `config/<id>/SUBAGENTS.md`, its
  prompt, and its own step state. A hook hands it the path. You do not have to brief it on the
  harness.
- Each one gets its own stream, `<id>-sNNN`, and its own Companion state.
- **Size each to finish inside one window.** No hook fires on a subagent's own compaction, so a
  subagent that compacts mid-task loses the harness's continuity support. Tell it to commit as
  it goes; `SUBAGENTS.md` already does, but scoping is yours.
- Results come back as a completion notification in a later turn, with a Companion-written
  digest of what it did, what it committed, and what it left open. That digest is all that
  crosses back.
- Subagents share your worktree. Give them non-overlapping scopes.

`hx complete` refuses while any subagent stream is still open, so let them finish.

### 7. Seams

Your conversation will be cut and rebuilt. That is a seam: `/clear` plus rehydration from your
context file. It is normal, planned, and cheap, and it happens at a turn boundary with no
background work running.

You do not trigger it, you do not prevent it, and you will not notice it happening — you will
simply find yourself at step 1 again. It costs nothing if `## Tasks` is current and your work
is committed. Everything in sections 3, 4 and 5 exists for this moment.

### 8. Finishing

```bash
hx complete done        # checks run, worktree must be clean, no open subagent stream
hx complete blocked     # something outside your task is in the way
hx complete decision    # someone else has to choose
hx complete exhausted   # the task was larger than one agent
```

**Before you run it**, write what should outlive this task below `## UPDATES BELOW ONLY` in
your `config/<id>/AGENTS.md`. That section is yours alone, it is not wiped by a dispatch, and
it travels in every future context file. Write what you would otherwise rediscover: how this
codebase is laid out, which commands actually work here, what surprised you. Nothing above
that header is yours — a hook will refuse the edit.

`hx complete <outcome>` is your **last action**. Nothing after it. It prints
`HX-COMPLETE <id> <outcome>` as its last line, and that line, in your transcript, is what
proves you are done.

### 9. `HX-CHECK-FAILED`

`hx complete done` is machine-checked. If a check exits non-zero, the worktree is dirty, or a
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
and a hook will refuse you.

### 10. When `done` is not available

`blocked`, `decision`, and `exhausted` run no checks. They are honest answers and using one is
better than forcing a `done` that is not true.

- `blocked` — something outside your task stops you: a missing dependency, an access you do not
  have, a broken thing you were told not to touch. Put what would unblock you in
  `## Open decision` first; the digest leads with it and the Partner's addendum answers it.
- `decision` — the order is ambiguous or two valid paths diverge and the choice is not yours.
  State the question and the options in `## Open decision`, with what each one costs.
- `exhausted` — the task was too large. Say where the natural split is.

All three keep everything: your `## Tasks`, your step state, your memory, your worktree, your
logs. The Partner resumes you with an addendum appended to your order, and you pick up exactly
where you stopped. You are not restarted.

## What is not yours

`config/` other than the part of your own `AGENTS.md` below the header, `companion/`, `logs/`,
`state/`, `run/`, `archive/`, `tasks.json`, `orders/`, and every work item except your own
`-working` file. Hooks refuse these writes and tell you why. `hx` commands that belong to the
Partner refuse you from `HARNESS_ID`.

You run, for yourself: `hx task`, `hx complete`. That is the list.
