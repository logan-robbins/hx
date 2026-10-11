# LEARNINGS · testing

What a test must prove: fixtures, coverage scope, regressions from captures, never weakening a test.

One line per rule, in exactly this shape, written only by `hx learn accept`:

    **Rule L1** · Signal: what you see when it is about to bite · Check: the mechanical test that decides it · Evidence: where it happened (path, run, PR, date)

The rule is invariant: no file name, message number or one-off detail outside the Evidence
cell. Propose with `hx learn propose --category testing …`; a different session reviews.

## Rules
