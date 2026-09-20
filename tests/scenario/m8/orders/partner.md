## Order

Ship `--upper` and `--lang` on the `greet` CLI, as the human asked for in chat.

The human wants two things in the `greet` repo, and said the language work builds on the
uppercasing so they should not be done at the same time by two agents in the same file:

1. an `--upper` flag that uppercases the greeting;
2. a `--lang` flag, starting with French, that composes with `--upper`.

Decompose this into two work items in the `engineers` pod, `eng-001` for the uppercasing and
`eng-002` for the language, with `eng-002` carrying `after: [eng-001]` so hx holds it `queued`
until the first lands and promotes it without waking me. Write both order files, then dispatch
the whole plan in one call.

One thing the human has not decided and must: what `--lang` does for a language the CLI does
not know — fall back to English, or fail. Do not settle it in the order and do not let the
worker settle it. `eng-002`'s order tells it to build everything else, put the question in
`## Open decision`, and complete `decision`. When that comes back, put the question to the
human in chat in their terms, write the answer into `orders/eng-002.addendum.md`, and
`hx resume eng-002 orders/eng-002.addendum.md` so it continues from its own step state instead
of starting again.

Read each digest before acting on it, and keep `PARTNER.md` current as the items land — what
was delivered, what the human decided and why. Once both are `done`, complete this item, then
bench both ids and report to the human in chat, in their words rather than in ids and states.

Ordering that matters: `hx bench` resets a completed item to `idle`, and the `### Checks`
below read the *work item* state. Complete this item first, then bench. Benching first would
make these checks fail on work that is actually finished.

## Definition of done

1. `eng-001` and `eng-002` are both `complete` with outcome `done`.
2. `eng-002` reached `done` by way of a `decision` that the human answered through
   `orders/eng-002.addendum.md` and `hx resume` — not by a re-dispatch, and not by the worker
   choosing for itself.
3. `PARTNER.md` records what landed and what the human decided.
4. The human has been told, in chat, what shipped.
5. `hx board` exits 0: no invariant violation anywhere in the instance.

### Checks

```bash
hx board --require-done eng-001 eng-002
hx board
```
