# Operating hx

What you see and do day to day, once [getting started](getting-started.md) is behind you.

The short version: **you talk to the Partner and nothing else.** Everything below is either
something the Partner does for you, or something you are reading rather than running.

## Talking to the Partner

```bash
tmux attach -t partner
```

That is the interface. Tell it what you want in plain language; it asks back what is unclear,
decomposes the work, creates and dispatches workers, and reports when the ask is met. Detach
with `Ctrl-b d` — the fleet keeps working.

The Partner has no order file, no `/goal` and no work item. **Your message is its goal.** There
is nothing to complete and no check to run against it: when it says the ask is met, that is the
end of the ask, and the next thing you type starts the next one.

**If the Partner ever tells you to run an hx command, that is a bug in its behaviour, not an
instruction.** Running the fleet is its job; every command on this page is one it runs, or one
you run to *look*.

## What a `decision` looks like

A worker that hits something only a human can settle stops and completes `decision` rather than
guessing. You will see it as a message from the Partner, in chat, containing the question and
what each answer costs. For example:

> `be-001` has the `--json` flag working, but the order asks for two things that cannot both be
> true: the object on stdout and nothing else, *and* the human-readable line still printed
> first. Which wins? JSON only makes the pipeline work and breaks scripts that grep for
> `Hello,`; keeping the line breaks the pipeline, which was the point of the flag.

You answer in chat, in your own words. The Partner turns that into an addendum file and runs
`hx resume be-001 <file>`; the worker picks up from its own `## Tasks` and step state with the
answer appended to its order. It does not start again.

Two things worth knowing about that exchange:

- **The Partner should not have a recommendation it has already acted on.** If it tells you
  what it chose rather than asking, say so — the whole point of `decision` is that the choice
  was not the agent's to make.
- **Your answer should retract whatever it replaces.** "JSON only; drop criterion 3" is a
  better answer than "JSON only", because the definition of done is what the goal evaluator
  judges and two contradictory criteria make the item unsatisfiable.

## What a seam looks like in a pane

If you attach to a worker (`tmux attach -t be-001` — read-only curiosity, nothing more), you
will eventually watch its conversation vanish and immediately restart. That is a **seam**, and
it is normal: hx cut the context on purpose at a quiet moment and handed the agent one file to
come back from.

What you see, in order: the agent finishes a turn; `/clear` runs; a line appears telling it to
read one path; the agent makes exactly one `Read` call; its `/goal` is pasted again; it carries
on. No summary, no "continuing from where we left off", no re-reading the files it had already
read.

The `/goal` you will see pasted is always the same fixed line, whatever the task is:

```
/goal The order for <id> is in <abs path to work item>; read it first. Done when `hx complete <outcome>` has been run and its output line `HX-COMPLETE <id> <outcome>` appears.
```

It is a **pointer**, not the task. The order itself never travels through the pane — it lives
in the work item that line names, which is why a seam costs nothing to re-send and why a pane
capture never leaks what an agent was asked to do. The same line is pasted on every
conversation start of a working item: dispatch, resume, seam, `hx restart`, and `hx launch` of
an item that was already working.

That last part is the whole design. If you see an agent after a seam re-opening files it
worked on before the cut, the Companion's notes were not good enough — that is the thing the
[Companion evaluation](companion-eval.md) measures, and it is worth mentioning rather than
shrugging at.

Claude Code's own compaction should never happen to a worker. hx seams long before the native
window is reached; if you ever see a compaction summary in a pane, something is wrong.

## Looking without touching

Everything here is read-only and safe to run yourself.

```bash
hx board
```

One line per worker id. There are no invariants and no error lines: it is a listing of what is
on disk, and it always exits 0.

```
be-001  backend  working  -  2026-09-21T03:19:45Z  alive  subagents=0  context=-  seams=-
```

That is: id, pod, state, outcome, when it was dispatched, whether its tmux session is alive,
open subagent streams, the context size of its last record, and seams taken this dispatch. A
fresh instance prints nothing at all, because it has no workers yet — the Partner is not a row
on the board.

```bash
hx show be-001          # everything hx knows about one id, including the pane
hx read be-001          # the Digest its Companion wrote, and any open decision
hx doctor               # what is here, what is missing, what is broken
```

`hx doctor` on a healthy instance is all `ok` and exits 0. The lines worth reading are the
`sandbox:` ones, because they check the *running* session rather than the configuration meant
to produce it:

```
ok    token            seed/token present, mode 0600
ok    home:be-001      settings.json
ok    home:partner     settings.json
ok    sandbox:be-001   IS_SANDBOX=1 on the tmux session
ok    sandbox:be-001   --dangerously-skip-permissions in the pane's argv
ok    sandbox:partner  IS_SANDBOX=1 on the tmux session
ok    sandbox:partner  --dangerously-skip-permissions in the pane's argv
```

`doctor` fails only on things that are actually broken — a missing `tmux` or `git`, a Python
below 3.14, a pinned `claude` that is not executable, a token that is missing or readable by
anyone else. Everything else is a `warn` naming the step that clears it.

### The UI

```bash
hx ui
```

serves a read-only view on `127.0.0.1` (port from `config/ui.json`, default 8765). It shows
the board, each worker's work item and step state rendered, the Partner's memory and a chat
box, and the panes. Reach for it when you want to see what an agent *believes* it is doing —
`## Tasks` and the step state side by side — without reading a transcript.

It observes. Full control, including slash commands and interrupts, is `tmux attach`.

## Keeping it running

`hx up` launches everything; `hx heartbeat` restarts dead sessions and wakes the Partner when
the board has moved. **hx ships no timer.** These are ordinary commands, and if you want them
at boot or on a schedule that is your cron, not something hx installed behind your back:

```cron
@reboot     hx up
*/15 * * * * hx heartbeat
```

`HARNESS_ROOT` has to be set in that environment, the same as it is in yours.

## Stopping everything

```bash
tmux kill-session -t be-001      # one worker
tmux kill-server                 # the lot, Partner included
```

Nothing is lost by killing a session. The work item, the order, the step state, the logs and
the agent's memory are all files; `hx launch <id>` brings a worker back, and a `working` item
gets its `/goal` again on the way up. What you do lose is the conversation the agent was in the
middle of, so prefer stopping between completions when you have the choice.

A worker whose session dies on its own is not a crisis either: `hx heartbeat` restarts it, or
the Partner does with `hx restart <id>`.

## When something looks wrong

| What you see | What it usually is |
|---|---|
| A worker `working` with a dead session | Nothing restarted it yet. The Partner runs `hx restart <id>`; so does `hx heartbeat` |
| A worker that has been `working` a long time | Look at `hx show <id>` — its `## Tasks` and open steps say what it thinks it is doing. Long is not the same as stuck |
| `HX-CHECK-FAILED` in a pane | Working as intended. The agent's checks did not pass; it fixes and retries. Nothing to do |
| The Partner idle with work outstanding | It is waiting for a wake, which arrives when a worker completes. If a worker has completed and nothing happened, `hx show <id>` and tell the Partner |
| A compaction summary in a worker's pane | Should not happen: hx seams first. Worth reporting |

## What the Partner is told

The Partner's instructions live in two files you can read, and edit if you mean to:

- `$HARNESS_ROOT/config/partner/AGENTS.md` — its persona, above the `## UPDATES BELOW ONLY`
  line. It reaches the Partner as system prompt at every launch. Below that line is the
  Partner's own memory; leave it alone.
- `$HARNESS_ROOT/PARTNER.md` — what it has learned: the fleet, open questions, decisions,
  what has shipped. Read this when you want to know what the Partner thinks the state of the
  world is.

A persona edit takes effect at the next launch of that agent, not immediately.
