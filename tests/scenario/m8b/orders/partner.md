## Order

Give the `greet` CLI a `--json` mode, as the human asked for in chat.

They want to use `greet` from a script without parsing prose, and they described it in one
sentence and moved on. One work item in the `engineers` pod, `eng-001`; no dependencies.

Write the order, dispatch it, and read the digest when it lands. If the worker comes back with
anything other than `done`, deal with it before completing this item: an outcome you have not
acted on is not a finished plan.

Ordering that matters: `hx bench` resets a completed item to `idle`, and the `### Checks` below
read the work item state. Complete this item first, then bench.

## Definition of done

1. `eng-001` is `complete` with outcome `done`.
2. Whatever the worker could not settle for itself was put to the human in chat and answered
   through an addendum and `hx resume`, not by re-dispatching and not by the worker guessing.
3. `PARTNER.md` records what landed and what the human decided.
4. `hx board` exits 0: no invariant violation anywhere in the instance.

### Checks

```bash
hx board --require-done eng-001
hx board
```
