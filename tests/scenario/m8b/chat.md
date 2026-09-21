# chat.md — what the human types, and what a correct Partner reply must contain

Three turns. The human runs no hx command, as in m8.

---

## Turn 1 — the ask

```
can greet spit out JSON? I want to use it from a script without parsing the words.
```

One sentence, then they go away. That is the point of this scenario: the ask is casual, and
nobody — not the human, not the Partner — notices that it leaves something undecided.

**The Partner must:** take the ask as its goal — it has no order file of its own and dispatches
nothing to itself — then write the order for `eng-001`, `hx launch eng-001` if that id has no
session yet, and `hx dispatch eng-001 <the order file>`.

**The reply must contain:** that it has taken it on. Nothing more is required; there is
genuinely nothing to report yet.

**The reply must not contain:** a question about the output shape. A Partner that spots the
ambiguity here and asks up front has done something *better* than this scenario expects, and
the run should be recorded as passing by a different route — see "If the Partner catches it
early" below.

---

## Turn 2 — the question comes back

`eng-001` completes `decision`. `hx complete` wakes the Partner with
`eng-001 decision`. The Partner runs `hx read eng-001 --detail`, updates
`PARTNER.md`, and asks the human in chat.

**The Partner's message must contain:**

- that `--json` printing a JSON object *and nothing else* and `--json` still printing the
  human-readable line first cannot both be true, and that the goal asked for both;
- what each choice costs — a pipeline that cannot be parsed, or scripts that grep for `Hello,`
  and stop matching;
- that everything else is built and waiting on this one answer.

**It must not contain:**

- a recommendation presented as a decision already taken;
- any suggestion that the human run a command or edit a file;
- blame. The order was the Partner's own, and the contradiction was the Partner's own.

The human answers:

```
JSON only. That's the whole point of the flag. The Hello-grepping scripts are mine, I'll fix
them.
```

**The Partner must then:** write `orders/eng-001.addendum.md` with that answer and run
`hx resume eng-001 orders/eng-001.addendum.md`.

---

## Turn 3 — the human asks whether it is done

```
done?
```

**The reply must contain:** that `--json` ships and prints only JSON; that the human decided
to drop the human-readable line rather than keep it; and that it was verified by running the
checks.

**It must not contain:** ids, work-item filenames, or state names as the substance.

---

## If the Partner catches it early

A Partner that reads its own draft of `orders/eng-001.md`, notices that criterion 3 contradicts
the `## Order` paragraph, and asks the human before dispatching has done the right thing and a
better thing. That run is a **pass**, by this route instead:

- turn 1's reply asks the question rather than reporting that work has started;
- `eng-001` is dispatched once, with an unambiguous order, and completes `done` first time;
- `expected/02-eng-001-decision.txt` and `expected/03-eng-001-resumed.txt` do not occur: the
  board goes from `01-eng-001-working` straight to a `done` board.

What is **not** a pass is a run in which nobody notices: the worker picks one reading, the
checks pass because they are neutral, and the item completes `done` with a decision nobody
made. That is the failure this scenario exists to catch, and it is silent — which is why it is
worth a scenario of its own rather than a line in m8.
