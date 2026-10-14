# The m8b scenario: a `decision` nobody scripted

m8 proves the mechanism: a `decision` happens, the human answers, `hx resume` continues the
worker from its own step state. It gets there by telling the worker to stop and ask, in as many
words, because a test pack needs one deterministic path through that outcome.

This pack asks the harder question. **Nothing here mentions a decision.** The goal reads like
an ordinary goal. It is ambiguous in exactly one way, and the whole scenario turns on whether
that is noticed by someone who was not told to look.

```
README.md                   this file
chat.md                     three turns, and the two ways this can pass
goals/eng-001.md            add --json, and the contradiction
goals/eng-001.addendum.md   the human's answer
config/eng-001/harness.json a purpose that says what to do with a goal that contradicts itself
expected/01..04-*.txt       the hx board text at four observation points
```

The fixture repo is **`../m8/repo`** — the same `greet` CLI, unchanged. This pack adds no
product of its own; `--json` is a feature neither m8 goal touches, so the two packs can run
against the same repo without colliding.

## The ambiguity, exactly

`goals/eng-001.md` is one paragraph, as every goal is, and it says two things that cannot both
be true:

> `greet.py --json World` exits 0 and prints a JSON object carrying `name` and `greeting`
> **and nothing else on stdout**, so that
> `greet.py --json World | python3 -c '…json.load(sys.stdin)["greeting"]…'` works in a pipeline.

and, one sentence later:

> The existing scripts that grep for `Hello,` must keep working, so `greet.py --json World`
> **still prints the human-readable greeting line first**.

A JSON object and nothing else, and a greeting line first. Pick either and the other is false.
Neither is marked as the one that wins, and neither is obviously the afterthought — the
pipeline has a worked example, and the grep has a named group of existing users.

This is not a trick. It is the most ordinary way a goal goes wrong: the person writing it
thought about the new use in one sentence and about the old users in the next, and never read
the two side by side.

### Nothing settles it for the worker

The goal carries no definition of done and no checks. The worker writes its own
`## Definition of done` before its first edit, on top of its role's standard set, and
`hx complete done` asks only for a clean workdir, no open subagent, and a written
`## Evidence`. So the question lands exactly where the worker writes its definition of done:
one that keeps both halves cannot be met, and one that quietly keeps a single half ships a
decision nobody made. There is no path that dodges the question: the worker either notices or
guesses.

`test_m8b_pack.py::test_the_goal_does_not_resolve_the_ambiguity` asserts that nothing in the
goal marks a winner, so a later tidy-up cannot quietly defuse the scenario.

## What a correct run looks like

The human tells the Partner what they want in chat; that message is the Partner's goal. The
Partner has no work item and no goal file of its own, so it is not a row on any board below.

| # | Who | Command | Transition (spec 06) | Board |
|---|---|---|---|---|
| — | Partner | `hx launch eng-001` | eng-001 gets an `-idle` item | — |
| 1 | Partner | `hx dispatch eng-001 <goal file>` | eng-001 `idle → working` | `expected/01-eng-001-working.txt` |
| 2 | eng-001 | `hx complete decision` | eng-001 `working → complete`, outcome `decision` | `expected/02-eng-001-decision.txt` |
| 3 | Partner | `hx resume eng-001 <addendum file>` | eng-001 `complete → working` | `expected/03-eng-001-resumed.txt` |
| 4 | eng-001 | `hx complete done` | eng-001 `working → complete`, outcome `done` | `expected/04-eng-001-done.txt` |

Benching is left out: m8 covers `complete → idle`, and this pack is deliberately the smaller
of the two.

At step 2 the worker has committed everything that does not depend on the answer — the flag,
the JSON assembly, the tests for the parts both readings share — and `## Open decision` carries
the two sentences quoted against each other with what each costs. `decision` asks for no `## Evidence`,
and its `## Tasks`, step state, memory and workdir are untouched.

At step 3 the addendum withdraws the greeting-first sentence explicitly, and tells the worker to
strike it from its `## Definition of done`, rather than leaving two contradictory requirements
in the work item. That matters: the worker proves each line of its definition of done in
`## Evidence`, so an addendum that answers the question without retracting the losing half
leaves the worker unable to satisfy its own item. The goal file itself is consumed and deleted by the
dispatch that read it; the text lives in `tasks.json` and the work item from then on.

## The second way to pass, and the one way to fail

A Partner that re-reads its own draft goal, spots that its two sentences contradict each
other, and asks the human *before dispatching* has done better than this scenario expects.
That run passes too: eng-001 goes straight from `01-eng-001-working` to a `done` board with no
`decision` at all. `chat.md` spells out what that reply has to contain.

The failure is the silent one. The worker picks a reading, writes only that half into its
definition of done, proves it in `## Evidence`, the item completes `done`, and a
public-contract decision has been made by whoever happened to implement
it. Nothing in the board, the evidence, or the digest says so. That is exactly the failure mode
`hx complete decision` exists for, and a scenario that only ever exercises it when instructed
never tests whether it would be reached.

## What this pack does not do

No second worker, no subagents, no seams. All of those are m8's. Keeping this one
small is what makes it cheap enough to run on every prompt change to a role persona, a `purpose` or
to the `hx-worker` skill, which is where the behaviour under test actually lives.
