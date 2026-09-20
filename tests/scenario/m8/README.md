# The M8 scenario pack

The concrete data spec 13's M8 runs on: a human, a Partner, two HarnessAgents with a real
dependency between them, one `decision` that only the human can answer, and a tiny product repo
to do the work in. It exists so that the end-to-end test is written against files that were
checked, rather than invented at the last minute by whoever gets to M8.

```
README.md            this file: the sequence, command by command
chat.md              what the human types, turn by turn, and what a correct reply must contain
orders/partner.md    the Partner's own order, as it should write it from that chat
orders/eng-001.md    --upper
orders/eng-002.md    --lang, after: [eng-001], and the decision it must stop on
orders/eng-002.addendum.md   the human's answer, as the Partner should write it
config/eng-001/AGENTS.md     the two personas, header and all
config/eng-002/AGENTS.md
expected/NN-*.txt    the `hx board` text at each observation point below
repo/                the product repo, with two tripwires that must never load
```

`../test_m8_pack.py` holds it to all of this: every order parses with `hx.orders.parse_order`
(the function `hx dispatch` validates with), the `after` graph is acyclic, both personas carry
`## UPDATES BELOW ONLY`, the fixture repo runs and does *not* already do the work, and — the
part that matters most — each `expected/` file is compared against what the real `hx board`
prints for an instance built in that state. None of the board text below is hand-drawn.

## What M8 is proving

From spec 13: the human tells the Partner what to do in its tmux session, the Partner writes
`orders/partner.md` and dispatches itself, decomposes, dispatches a whole plan with an `after`
chain in one call, the workers work with subagents and take seams, one item ends `decision`,
the human answers, `hx resume` continues it from its own step state, everything completes with
a Digest, the Partner wakes, reads, updates `PARTNER.md`, benches, and its own `hx complete
done` passes `hx board --require-done`. **The human runs no hx command at any point** and
`hx board` exits 0 throughout.

## The sequence

`hx board` is read at eight quiet points — between commands, never mid-turn. Timestamps are
shown as `<ts>`; the test normalises them before comparing.

| # | Who runs it | Command | Transition (spec 06) | Board |
|---|---|---|---|---|
| 1 | Partner | `hx dispatch partner orders/partner.md`, then `hx launch eng-001`, `hx launch eng-002` | partner `idle → working`; the two workers get `-idle` items | `expected/01-partner-working.txt` |
| 2 | Partner | `hx dispatch eng-001 orders/eng-001.md eng-002 orders/eng-002.md` | eng-001 `idle → working`; eng-002 `idle → queued` | `expected/02-plan-dispatched.txt` |
| 3 | eng-001 | `hx complete done` | eng-001 `working → complete`; hx promotes eng-002 `queued → working` and sends its goal | `expected/03-eng-001-done.txt` |
| 4 | eng-002 | `hx complete decision` | eng-002 `working → complete`, outcome `decision` | `expected/04-eng-002-decision.txt` |
| 5 | Partner | `hx resume eng-002 orders/eng-002.addendum.md` | eng-002 `complete → working` | `expected/05-eng-002-resumed.txt` |
| 6 | eng-002 | `hx complete done` | eng-002 `working → complete`, outcome `done` | `expected/06-all-done.txt` |
| 7 | Partner | `hx complete done` | partner `working → complete`, outcome `done` | `expected/07-partner-done.txt` |
| 8 | Partner | `hx bench eng-001`, `hx bench eng-002` | both `complete → idle` | `expected/08-benched.txt` |

### Step 1 — the Partner dispatches itself

The human's first message (`chat.md`, turn 1) becomes `orders/partner.md`. The Partner writes
the file, runs `hx dispatch partner orders/partner.md` from its own Bash tool, and because its
pane is mid-turn the pointer lands in `run/partner/goal-pending`; its `stop` hook pastes it at
the end of that turn. Nothing is archived or wiped for `partner` — its session, streams and
state are continuous (spec 08).

Still inside that first goal, it creates `config/eng-001/` and `config/eng-002/` from
`templates/worker/` and runs `hx launch` for each, which is what gives them their `-idle` work
items. The board is read after all of that, so both workers are present and idle.

### Step 2 — one dispatch, one `after` chain

The whole plan goes in **one** `hx dispatch` call. `eng-002`'s order carries
`after: [eng-001]`, so hx renders its body and leaves it `queued` with no goal marker. The
Partner is not woken when it is promoted — that is the point of `after`.

### Step 3 — promotion without a wake

`eng-001`'s `hx complete done` runs its `### Checks` in `wt/eng-001`, requires a clean
worktree and no open subagent stream, writes the Digest, and prints `HX-COMPLETE eng-001 done`.
Inside that same command hx promotes `eng-002` from `queued` to `working` and sends its goal.
`hx complete` then wakes the Partner with `eng-002 complete: …` only for the item that
completed — the promotion is silent.

Note the goal column: a completed item has no `run/<id>/goal` marker, because `hx complete`
removes it.

### Step 4 — the decision

`eng-002` builds everything that does not depend on the open question, commits it, writes the
question and both options into `## Open decision`, and runs `hx complete decision`. No checks
run for `decision`. Its logs, step state, `## Tasks`, memory and worktree all stay exactly as
they are.

**This is scripted, not emergent.** `orders/eng-002.md` tells the worker in as many words to
stop and ask, and says why: the unknown-language behaviour is a public-contract choice that is
expensive to undo. A test pack needs one deterministic path through the `decision` outcome, and
an order that genuinely withholds a decision is the honest way to get one. Its `### Checks`
deliberately do **not** mention `--lang xx` or the warning text, so the worker cannot read the
answer off its own definition of done.

### Step 5 — resume, not re-dispatch

The Partner puts the question to the human in chat (`chat.md`, turn 4), writes the answer into
`orders/eng-002.addendum.md`, and runs `hx resume eng-002 orders/eng-002.addendum.md`. The
addendum is appended beneath `## Order` as `## Order addendum <ts>`, the outcome is cleared,
and the item goes back to `working` with its goal re-sent. `hx bench` plus a fresh order would
throw away the `## Tasks`, the step state and the commits that already exist — M8 fails if that
is the path taken.

### Step 6 — the same definition of done

Nothing in `## Definition of done` changed. The addendum adds a behavioural requirement and
tells the worker to test it, and `python3 -m unittest discover -s tests -q` is already in the
`### Checks`, so the worker's own new test is what proves the answer. That is the mechanism
working as designed: checks stay the Partner's, the order grows, the agent supplies the
evidence.

### Step 7 before step 8 — the ordering that matters

`hx board --require-done` reads the **work item state**: it passes only while an id is
`complete` with outcome `done`. `hx bench` resets a completed item to `idle`. So the Partner
must run its own `hx complete done` *first* and bench afterwards.

Spec 12 reads the other way round — step 5 says bench a `done` item once the digest is
consumed, and step 8 has the Partner complete its own item — so following it literally would
make the Partner's own checks fail on work that is genuinely finished.
`orders/partner.md` says so explicitly, and it is raised in `handoff/to-orchestrator.md`.

### Step 8 — benched, and still showing `done`

After `hx bench`, both ids are `idle` again and ready for the next order, but the board still
shows their outcome as `done`. That is correct and deliberate: `hx bench` does not touch
`tasks.json` (spec 08), and the outcome column comes from there. It is cleared by the next
`hx dispatch` of that id, which is also what stops a stale completion from satisfying a newer
dependent. The work item's own frontmatter carries no outcome once it is `idle` — only a
`complete` item does — and `hx board` reports a violation if one does.

## Assumptions this pack makes, and what changes if they are wrong

**A1 — `hx goal` writes `run/<id>/goal` even when delivery defers.** At steps 1 and 5 the
Partner is dispatching or resuming from inside its own turn, so the pointer lands in
`run/partner/goal-pending` rather than being pasted. The board invariant is that every
`working` item has a goal marker, so the marker must be written when the item becomes
`working`, not when the paste lands — otherwise `hx board` would report a violation for the
length of that turn and M8's "exits 0 throughout" could not hold. `expected/01` and
`expected/05` show `<ts>` in the goal column on that basis. If the build lane resolves it the
other way, those two files change to `-` and the spec 06 invariant needs rewording; nothing
else in the pack moves.

**A2 — one pod, `engineers`, for both workers.** Nothing in M8 needs two pods, and the work
item path in every `expected/` file encodes it (`pods/engineers/eng-001-working.md`).

**A3 — no subagents are open at any observation point.** The open-subagent column is `0`
everywhere. M8 does run subagents (spec 13 names them), but only inside a turn; `hx complete`
refuses while any stream is still `-open`, so every quiet point has zero.

## The fixture repo and its tripwires

`repo/` is a ten-line Python CLI with a stdlib test suite — small enough that M8 is about the
harness and not about the software, real enough that both `### Checks` blocks run actual
commands. `eng-001` adds `--upper`; `eng-002` adds `--lang fr` on top of it, which is what
makes the dependency real rather than decorative: `--upper --lang fr World` must print
`BONJOUR, WORLD !`, and it cannot unless `eng-001`'s work is on the branch.

Two files in it must never reach a harness session, and both fail loudly rather than quietly:

- `repo/CLAUDE.md` — kept out by `claudeMdExcludes` and instruction-files mode `claude-md`. It
  tells the agent to ignore its work item, never run `hx complete`, and prefix every reply with
  `TRIPWIRE-CLAUDE-MD-LOADED`.
- `repo/.claude/settings.json` — kept out by `git sparse-checkout set --no-cone '/*'
  '!/.claude/'`, so it is not even on disk in a worktree. It registers a `PreToolUse` hook that
  denies every tool call, so an agent that loaded it would stop dead on its first action rather
  than run with the wrong rules.

M8's assertion: the run completes, and neither tripwire string appears in any transcript or log.

## Running it

M8 needs `hx dispatch`, `hx goal`, `hx complete`, `hx resume` and `hx bench` — build-2 and
later. Until then `../test_m8_pack.py` checks everything that does not need them, including the
eight board states, by building each instance directly and running the real `hx board`.
