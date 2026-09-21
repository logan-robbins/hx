# The m8b scenario: a `decision` nobody scripted

m8 proves the mechanism: a `decision` happens, the human answers, `hx resume` continues the
worker from its own step state. It gets there by telling the worker to stop and ask, in as many
words, because a test pack needs one deterministic path through that outcome.

This pack asks the harder question. **Nothing here mentions a decision.** The order reads like
an ordinary order. It is ambiguous in exactly one way, and the whole scenario turns on whether
that is noticed by someone who was not told to look.

```
README.md                   this file
chat.md                     three turns, and the two ways this can pass
orders/eng-001.md           add --json, and the contradiction
orders/eng-001.addendum.md  the human's answer
config/eng-001/AGENTS.md    a persona that says what to do with an order that contradicts itself
expected/01..04-*.txt       the hx board text at four observation points
```

The fixture repo is **`../m8/repo`** — the same `greet` CLI, unchanged. This pack adds no
product of its own; `--json` is a feature neither m8 order touches, so the two packs can run
against the same repo without colliding.

## The ambiguity, exactly

`orders/eng-001.md` says two things that cannot both be true.

In `## Order`:

> `greet.py --json World` must print a JSON object **and nothing else on stdout**, so that
> `greet.py --json World | python3 -c '…json.load(sys.stdin)["greeting"]…'` works in a
> pipeline.

In `## Definition of done`, criterion 3:

> `greet.py --json World` **still prints the human-readable greeting line first**, so the
> existing scripts that grep for `Hello,` keep working.

A JSON object and nothing else, and a greeting line first. Pick either and the other is false.
Neither is marked as the one that wins, and neither is obviously the afterthought — the
pipeline has a worked example, and the grep has a named group of existing users.

This is not a trick. It is the most ordinary way an order goes wrong: the person writing it
thought about the new use while writing `## Order` and about the old users while writing
`## Definition of done`, twenty minutes apart, and never put the two sentences side by side.

### The checks are deliberately neutral

```bash
python3 -m unittest discover -s tests -q
python3 greet.py World | grep -qx 'Hello, World!'
python3 greet.py --json World >/dev/null
python3 greet.py --json World | grep -q '"greeting"'
```

Every one of those passes under **both** readings. That is the point. If the checks resolved
the ambiguity, a worker could satisfy them, call the prose an inconsistency, and finish — and
the run would look like a success while shipping a decision nobody made. With neutral checks
there is no path that dodges the question: the worker either notices or guesses.

`test_m8b_pack.py::test_the_checks_do_not_resolve_the_ambiguity` asserts they stay neutral, so
a later tidy-up cannot quietly defuse the scenario.

## What a correct run looks like

The human tells the Partner what they want in chat; that message is the Partner's goal. The
Partner has no work item and no order file of its own, so it is not a row on any board below.

| # | Who | Command | Transition (spec 06) | Board |
|---|---|---|---|---|
| — | Partner | `hx launch eng-001` | eng-001 gets an `-idle` item | — |
| 1 | Partner | `hx dispatch eng-001 <order file>` | eng-001 `idle → working` | `expected/01-eng-001-working.txt` |
| 2 | eng-001 | `hx complete decision` | eng-001 `working → complete`, outcome `decision` | `expected/02-eng-001-decision.txt` |
| 3 | Partner | `hx resume eng-001 <addendum file>` | eng-001 `complete → working` | `expected/03-eng-001-resumed.txt` |
| 4 | eng-001 | `hx complete done` | eng-001 `working → complete`, outcome `done` | `expected/04-eng-001-done.txt` |

Benching is left out: m8 covers `complete → idle`, and this pack is deliberately the smaller
of the two.

At step 2 the worker has committed everything that does not depend on the answer — the flag,
the JSON assembly, the tests for the parts both readings share — and `## Open decision` carries
the two sentences quoted against each other with what each costs. No checks run for `decision`,
and its `## Tasks`, step state, memory and workdir are untouched.

At step 3 the addendum withdraws criterion 3 explicitly rather than leaving two contradictory
criteria in the work item. That matters: the `## Definition of done` is what the goal evaluator
judges, so an addendum that answers the question without retracting the losing half leaves the
worker unable to satisfy its own item. The order file itself is consumed and deleted by the
dispatch that read it; the text lives in `tasks.json` and the work item from then on.

## The second way to pass, and the one way to fail

A Partner that re-reads its own draft order, spots that criterion 3 contradicts the `## Order`
paragraph, and asks the human *before dispatching* has done better than this scenario expects.
That run passes too: eng-001 goes straight from `01-eng-001-working` to a `done` board with no
`decision` at all. `chat.md` spells out what that reply has to contain.

The failure is the silent one. The worker picks a reading, the neutral checks pass, the item
completes `done`, and a public-contract decision has been made by whoever happened to implement
it. Nothing in the board, the checks, or the digest says so. That is exactly the failure mode
`hx complete decision` exists for, and a scenario that only ever exercises it when instructed
never tests whether it would be reached.

## What this pack does not do

No second worker, no subagents, no seams. All of those are m8's. Keeping this one
small is what makes it cheap enough to run on every prompt change to `config/<id>/AGENTS.md` or
to the `hx-worker` skill, which is where the behaviour under test actually lives.
