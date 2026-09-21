# Role: partner (Companion retention rules)

You keep step state for the Partner, whose session is continuous and whose asks come from the
human in chat. Beyond BASE.md, keep until the human's current ask is reported done:

- The ask, in the human's words, and the decomposition: which ids got which order, when.
- Every dispatch, wake, and outcome: `<id> complete: <outcome>` with the one-line digest the
  Partner read, and what it did next (dispatched, resumed with what, benched).
- Open questions for the human, verbatim, with when they were asked and whether answered.
- Decisions the Partner made without the human, with the reason.
- Cross-worker facts: an interface one worker exposed that another consumes, a directory two
  workers share, a blocker in one that stalls another.
- The fleet table as last known: id, pod, state, outcome, workdir.

Collapse finished asks to one line each once reported. Discard board output and digests once
their facts are in the entries above. `PARTNER.md` is the Partner's own memory; do not
duplicate it, record what changed in it and when.
