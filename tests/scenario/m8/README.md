# The M8 scenario pack

The concrete data spec 13's M8 runs on: a human, a Partner, two HarnessAgents with a real
dependency between them, one `decision` that only the human can answer, and a tiny product repo
to do the work in. It exists so that the end-to-end test is written against files that were
checked, rather than invented at the last minute by whoever gets to M8.

```
README.md            this file: the sequence, command by command
chat.md              what the human types, turn by turn, and what a correct reply must contain
orders/eng-001.md    --upper
orders/eng-002.md    --lang, and the decision it must stop on
orders/eng-002.addendum.md   the human's answer, as the Partner should write it
config/eng-001/AGENTS.md     the two personas, header and all
config/eng-002/AGENTS.md
expected/NN-*.txt    the `hx board` text at each observation point below
repo/                the product repo, with two tripwires that must never load
```

`../test_m8_pack.py` holds it to all of this: every order parses with `hx.orders.parse_order`
(the function `hx dispatch` validates with), both personas carry `## UPDATES BELOW ONLY`, the
fixture repo runs and does *not* already do the work, no cut feature survives anywhere in the
pack, and — the part that matters most — each `expected/` file is compared against what the
real `hx board` prints for an instance built in that state. None of the board text below is
hand-drawn.

## What M8 is proving

From spec 13: the human tells the Partner what to do in its tmux session, and that message is
the Partner's goal — it has no work item and no order file of its own. It decomposes the ask,
dispatches `eng-001`, and waits. The workers work with subagents and take seams; one item ends
`decision`; the human answers in chat; `hx resume` continues that item from its own step state
rather than starting it again; everything completes with a Digest; the Partner wakes on each
completion, reads, updates `PARTNER.md`, and benches. **The human runs no hx command at any
point** and `hx board` exits 0 throughout — it is a listing, and exiting 0 is all it ever does.

## The sequence

`hx board` is read at seven quiet points — between commands, never mid-turn. Timestamps show as
`<ts>` and the liveness column as `<alive>`; the test normalises both before comparing.

**The Partner is not on the board.** It has no work item, no order file and no `/goal`: the
human's message in chat is its goal, and nothing in hx dispatches or completes it. Every row
below is a worker.

| # | Who runs it | Command | Transition (spec 06) | Board |
|---|---|---|---|---|
| — | Partner | `hx launch eng-001`, `hx launch eng-002` | each gets an `-idle` item | — |
| 1 | Partner | `hx dispatch eng-001 <order file>` | eng-001 `idle → working` | `expected/01-eng-001-working.txt` |
| 2 | eng-001 | `hx complete done` | eng-001 `working → complete` | `expected/02-eng-001-done.txt` |
| 3 | Partner | `hx dispatch eng-002 <order file>` | eng-002 `idle → working` | `expected/03-eng-002-working.txt` |
| 4 | eng-002 | `hx complete decision` | eng-002 `working → complete`, outcome `decision` | `expected/04-eng-002-decision.txt` |
| 5 | Partner | `hx resume eng-002 <addendum file>` | eng-002 `complete → working` | `expected/05-eng-002-resumed.txt` |
| 6 | eng-002 | `hx complete done` | eng-002 `working → complete`, outcome `done` | `expected/06-all-done.txt` |
| 7 | Partner | `hx bench eng-001`, `hx bench eng-002` | both `complete → idle` | `expected/07-benched.txt` |

### Steps 1–3: the dependency is the Partner's to keep

`eng-002` needs `eng-001`'s `--upper` to exist before it can make `--lang` compose with it.
**Nothing in hx knows that.** There is no `after` field, no `queued` state and no promotion:
the Partner dispatches `eng-001`, waits for the completion wake, reads the digest, and *then*
dispatches `eng-002` — exactly as a human running two sessions would.

That is the point of the sequence. The old pack had hx sequencing this and the Partner asleep
through it; now the judgement is the Partner's, and the failure mode to watch for is a Partner
that dispatches both at once and lets `eng-002` read a `greet.py` that has not grown `--upper`
yet.

Each order file is **consumed and deleted** by the dispatch that reads it. The order then
lives in `tasks.json` and the work item and nowhere else, so the files in `orders/` here are
inputs to the run, not a directory the instance keeps.

### Step 4 — the decision

`eng-002` builds everything that does not depend on the open question, commits it, writes the
question and both options into `## Open decision`, and runs `hx complete decision`. No checks
run for `decision`. Its logs, step state, `## Tasks`, memory and workdir stay exactly as they
are.

**This is scripted, not emergent.** `orders/eng-002.md` tells the worker in as many words to
stop and ask, and says why. A test pack needs one deterministic path through the `decision`
outcome; m8b asks the harder question of whether it would be reached unprompted.

### Step 5 — resume, not re-dispatch

The Partner puts the question to the human in chat (`chat.md`), writes the answer to an
addendum file, and runs `hx resume eng-002 <file>`. The addendum is appended beneath
`## Order`, the outcome is cleared, and the goal is re-sent. `hx bench` plus a fresh order
would throw away the `## Tasks`, the step state and the commits that already exist — M8 fails
if that is the path taken.

### Step 7 — benched, and still showing `done`

After `hx bench`, both ids are `idle` and ready for the next order, but the board still shows
their outcome as `done`: `hx bench` does not touch `tasks.json`, and the outcome column comes
from there. It is cleared by the next `hx dispatch` of that id.

## What was cut from this pack

It is worth knowing what this scenario *stopped* testing, so nobody reintroduces it:

- **`orders/partner.md` and the Partner's own work item.** The Partner is not dispatched, not
  completed, and not on the board. Its goal is the human's message.
- **`after: [eng-001]`, the `queued` state and promotion.** The Partner sequences by waiting.
- **`hx board --require-done` and the invariant errors.** `hx board` is a listing that exits 0;
  there is nothing for it to judge.
- **The mirror, the sparse worktree and the branch.** A worker's `workdir` is a directory
  somebody chose. The fixture repo is still a git repository because the orders' checks run
  `git`-free commands in it and `hx complete done` wants it clean — not because hx made it.

## The fixture repo and its tripwires

`repo/` is a ten-line Python CLI with a stdlib test suite — small enough that M8 is about the
harness and not about the software, real enough that both `### Checks` blocks run actual
commands. `eng-001` adds `--upper`; `eng-002` adds `--lang fr` on top of it, which is what
makes the dependency real rather than decorative: `--upper --lang fr World` must print
`BONJOUR, WORLD !`, and it cannot unless `eng-001`'s work is already in the workdir.

Two files in it must never reach a harness session, and both fail loudly rather than quietly:

- `repo/CLAUDE.md` — kept out by `claudeMdExcludes` and instruction-files mode `claude-md` in
  the settings `install.sh` writes into `run/<id>/home/`. It tells the agent to ignore its work
  item, never run `hx complete`, and prefix every reply with `TRIPWIRE-CLAUDE-MD-LOADED`.
- `repo/.claude/settings.json` — registers a `PreToolUse` hook that denies every tool call, so
  an agent that loaded it stops dead on its first action rather than running with the wrong
  rules.

M8's assertion: the run completes, and neither tripwire string appears in any transcript or log.

**The second tripwire has no hx-side defence any more.** It used to be kept off disk by
`git sparse-checkout set --no-cone '/*' '!/.claude/'` in the worktree hx built. D25 cut git
management, so the workdir is a directory somebody chose and whatever is in it is in it. The
tripwire is still the right shape — it is exactly the thing that must not load — but whether
`hx launch` has to do anything about a `.claude/` in the workdir is an open question for the
spec author, and M8 is where it would be caught. Recorded in `handoff/gtm-to-build.md`.

## Driving it

Everything M8 needs exists: `hx install`, the seed token, `launch`, `dispatch`, `goal`,
`complete`, `resume`, `bench`, and the hooks that write the streams. This is the sequence to
drive, with the line each command prints so a test can assert on it rather than on exit status
alone.

### Setup, once

```bash
export HARNESS_ROOT=<scratch>/hx
export HX_TMUX="tmux -L hx-m8-$$"          # never the default server: a leaked agent holds a token
export HX_CLAUDE_BIN=<real claude>         # M8 is a live run; the fake cannot take a /goal
hx install --root "$HARNESS_ROOT"
#   stops with exit 4 → write seed/token (mode 0600) → run it again
cp -R ./repo <scratch>/greet              # the workdir; hx does not create it
cp -R config/eng-001 config/eng-002 "$HARNESS_ROOT/config/"   # the personas in this pack
```

`hx install` creates no repository, no worktree and no branch: a worker's `workdir` is a
directory somebody chose, and here that is a copy of `./repo`. Both workers point at the same
one, which is what makes the dependency real — `eng-002` reads the `greet.py` that `eng-001`
left.

The two `config/<id>/` directories are the ones in this pack, not `templates/worker/`: their
personas are part of the scenario. Each needs a `harness.json` with `workdir` set to that copy,
and a `SUBAGENTS.md`; copy them from `templates/worker/` and substitute, or take the ones the
Partner would have written.

The order files stay wherever you put them and are passed to `hx dispatch` by path. Dispatch
reads each one, folds it into the work item, and **deletes it**; the instance keeps no
`orders/` directory of its own.

### The sequence

| # | Run as | Command | Prints | Board |
|---|---|---|---|---|
| — | setup | `hx launch eng-001`, `hx launch eng-002` | `HX-LAUNCH eng-001 eng-001 goal=none` | — |
| 1 | partner | `hx dispatch eng-001 orders/eng-001.md` | `HX-DISPATCH eng-001 working goal=sent` | `expected/01-eng-001-working.txt` |
| 2 | eng-001 | `hx complete done` | `HX-COMPLETE eng-001 done` | `expected/02-eng-001-done.txt` |
| 3 | partner | `hx dispatch eng-002 orders/eng-002.md` | `HX-DISPATCH eng-002 working goal=sent` | `expected/03-eng-002-working.txt` |
| 4 | eng-002 | `hx complete decision` | `HX-COMPLETE eng-002 decision` | `expected/04-eng-002-decision.txt` |
| 5 | partner | `hx resume eng-002 orders/eng-002.addendum.md` | `HX-RESUME eng-002 working goal=sent` | `expected/05-eng-002-resumed.txt` |
| 6 | eng-002 | `hx complete done` | `HX-COMPLETE eng-002 done` | `expected/06-all-done.txt` |
| 7 | partner | `hx bench eng-001`, `hx bench eng-002` | `HX-BENCH eng-001 idle archived=…` | `expected/07-benched.txt` |

`HX-COMPLETE <id> <outcome>` is the **last** line of `hx complete`'s stdout, and it is what the
goal evaluator reads out of the transcript.

**Step 3 happens because of step 2.** The Partner is woken by `eng-001`'s completion, reads the
digest, and only then dispatches `eng-002`. Nothing promotes it; there is no queue. A run in
which both dispatches happen in step 1 has failed M8 even if every board afterwards matches.

Steps 2, 4 and 6 are run by the agents themselves, from inside their own sessions, with
`HARNESS_ID` set by `start.sh`. A test cannot run them on the agents' behalf and have M8 mean
anything: the point is that the agent reached `hx complete` under its own `/goal`.

### What to assert beyond the boards

- **The human ran no hx command.** Everything in the table above is run by the Partner or by a
  worker. `chat.md` is the whole of the human's side.
- **The Partner never appears on the board** and never runs `hx complete`. It has no work item.
- **Neither tripwire string appears anywhere** in any transcript, log, or pane capture:
  `TRIPWIRE-CLAUDE-MD-LOADED` and `TRIPWIRE-REPO-CLAUDE-DIR-LOADED`.
- **`eng-002` reached `done` by way of `decision` and a resume**, not by a re-dispatch: its
  `tasks.json` entry has an `addenda` array with one entry, and its `## Tasks` from before the
  pause survived.
- **At least one subagent stream** exists under `logs/eng-00{1,2}/` — spec 13 M8 says "with
  subagents", and the streams are how you know one ran.
- **Seams were taken**: `seam` records in the main streams, and `hx metrics <id>` reporting one
  context-file Read per seam.

### Until then

`../test_m8_pack.py` checks everything that does not need a live agent: every order parses with
the function `hx dispatch` validates with, the personas, the fixture repo, that no cut feature
survives, and all seven board states — built directly and compared against the real `hx board`.
