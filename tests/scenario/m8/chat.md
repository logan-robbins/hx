# chat.md — what the human types, and what a correct Partner reply must contain

The whole human side of M8. The human is attached to `tmux attach -t partner` and types these
turns, in this order. **They run no hx command at any point** — that is one of M8's pass
criteria, not a stylistic preference, so a Partner reply that tells the human to run something
fails the run.

Each turn gives the exact text to send and what the reply must and must not contain. "Must
contain" is a check on substance, not on wording: the Partner writes its own prose.

---

## Turn 1 — the ask

```
I want two things on the greet CLI. First an --upper flag that shouts the greeting.
Then a --lang flag so it can speak French, and --upper should still work with it.
The language one builds on the uppercasing, so don't have two agents in that file at
the same time.
```

**The Partner takes no hx action before replying.** This message *is* its goal — there is no
order file for the Partner, no dispatch of itself, no `/goal`. It reads the ask, decides what
it is going to do, records it in `PARTNER.md`, and answers.

**The reply must contain:**

- that it has taken the work on, and what the two pieces are;
- that the second waits for the first — naming the dependency, not just the order of work.

**The reply must not contain:**

- any instruction for the human to run a command;
- a claim that anything is built, dispatched to a worker, or finished. Nothing has been
  dispatched to a worker yet at this point in the turn.

---

## Turn 2 — (no human input)

The Partner decomposes the ask, writes both order files, runs `hx launch eng-001` and
`hx launch eng-002` for ids that have no session yet, and dispatches **only the first**:

```
hx dispatch eng-001 <the eng-001 order file>
```

`eng-002` waits, and it waits in the Partner's head — not in hx. There is no `after` field and
no `queued` state; the Partner dispatches it when `eng-001`'s completion wakes it. Dispatching
both here is the failure M8 is looking for: `eng-002` would read a `greet.py` with no `--upper`
in it.

No human turn here. It is listed because M8 asserts that the second dispatch happens *after*
the first completion, and because the human seeing nothing at this point is correct behaviour.

---

## Turn 3 — the human asks how it is going

```
how's it going?
```

Sent while `eng-001` is `working` and `eng-002` is still `idle`, waiting for it.

**The reply must contain:**

- that the first piece is in progress and the second is waiting on it;
- something concrete about the state, from `hx board` or a digest — not a guess.

**The reply must not contain:**

- a claim that either item is finished;
- raw board text or ids as the whole answer. The human asked a question in English.

---

## Turn 4 — the decision comes back

`eng-001` completes `done` and wakes the Partner with `eng-001 done`, which trusts the
persona and dispatches `eng-002`. `eng-002` then completes `decision`, and `hx complete`
wakes the Partner a second time with `eng-002 decision`.

The Partner runs `hx read eng-002 --detail`, updates `PARTNER.md`, and **asks the human in chat**:

**The Partner's message must contain:**

- the question itself: what `--lang` should do for a language it does not know;
- both options — fall back to English, or fail — and what each one costs;
- that the uppercasing is already done and the rest of the language work is built and waiting
  on this one answer.

**It must not contain:**

- a recommendation presented as a decision already taken;
- any suggestion that the human run `hx resume` or edit a file.

The human answers:

```
fall back to English, but warn on stderr so a typo is still findable.
Exit 0 either way — I don't want a stale locale breaking someone's script.
```

**The Partner must then:** write `orders/eng-002.addendum.md` with that answer and run
`hx resume eng-002 orders/eng-002.addendum.md`. Not `hx bench` and a new order — the worker
keeps its `## Tasks`, its step state, its memory, and its workdir, and only the order grows.

---

## Turn 5 — the human asks whether it is done

```
are we good?
```

Sent after `eng-002` has completed `done` a second time and the Partner has benched both ids.
The Partner has no `hx complete` of its own to run: it answers the human, and that is the end
of the ask.

**The reply must contain:**

- that both pieces shipped, described in the human's terms — the flag that shouts and the flag
  that speaks French, composing with each other;
- what was decided about the unknown language, and that it was their decision;
- that it was verified by running the checks, not by an agent saying so.

**It must not contain:**

- ids, work-item filenames, or state names as the substance of the answer;
- hedging about whether it is finished. Each worker's `hx complete done` ran that item's
  `### Checks` and printed `HX-COMPLETE <id> done`, so the Partner knows.

---

## What the human never does

For the avoidance of doubt, across the whole run the human does not: run `hx` anything, edit
any file under `$HARNESS_ROOT`, attach to a worker's tmux session, or answer a permission
prompt. The only keystrokes are the four messages above.
