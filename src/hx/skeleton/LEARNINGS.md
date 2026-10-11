# LEARNINGS

Category rules this instance has paid for. One line per rule, in exactly this shape:

    **Rule L1** · Signal: what you see when it is about to bite · Check: the mechanical test that decides it · Evidence: where it happened (path, run, PR, date)

The rule is invariant: no file name, message number or one-off detail outside the Evidence cell.
Nobody edits this file by hand. An agent that was tripped up runs `hx learn propose …`; a
different session reviews the proposal with `hx learn pending` and `hx learn accept` or
`hx learn reject`, so the file never grows with duplicates. `hx learn check` lints it.

## Rules
