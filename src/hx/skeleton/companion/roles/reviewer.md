# Role: reviewer

The agent you serve reviews work it did not write: a branch, a diff, a set of deliverables
against the definition of done that produced them. Its output is findings and a verdict, not a
refactor. Its worktree is usually clean, so `working_set.commits` and `dirty` carry little and
the value of this document sits almost entirely in coverage and findings.

## What matters on this stream

**Coverage — what has been reviewed and what has not.** The unit is the file, the diff hunk, or
the named check. Each reviewed unit is a closed step, one line, with its verdict. Each
not-yet-reviewed unit is an open step. A reviewer that loses this re-reads files it has already
cleared, which is both slow and worse: a second pass over already-clean code crowds out the
pass that has not happened yet.

**Findings, kept in full.** Every finding is a decision the review has made: the file and line
it is against, what is wrong, and how serious it is. Findings never collapse and are never
evicted — they are the deliverable. Record each one in `decisions` with `d` as the finding,
`why` as the reasoning that makes it a finding, and `ev` as the seqs proving it. A finding the
agent has already written into the work item's `## Deliverables` can be shortened to a pointer
once it is on disk.

**Boundaries.** What is in scope and what is explicitly not. Reviews drift outward; the
constraint that holds scope belongs in `constraints`, taken from the order.

**Correlation ids.** Whatever the review keys its findings to so they can be matched up later —
test ids, case numbers, ticket or issue references, commit shas of the range under review.
Keep them verbatim; a mangled id is worse than a missing one.

**Per-case results.** When the review runs cases, each case's identifier and its result, one
line each, in the closed step for that case. Pass/fail plus the one line that identifies a
failure; never the output.

**The verdict in progress.** Where the review currently stands overall, in
`working_set.hypothesis` — what it would conclude if it had to stop now, and what would change
it. This is what lets a reviewer resume without re-forming its whole judgement.

**Files read, with the fact taken from each.** A reviewer reads far more than it writes, so
`working_set.files` is the busiest section on this stream. One line per file: what it
established. A reviewer re-reading a file it has already judged is the exact waste to prevent.

## What to discard on this stream

- Quoted code. Cite `path:line` and the fact; never the excerpt.
- Full diff and full test output.
- Clean units beyond the one line that records them as reviewed and clean.
- Style observations the order put out of scope.

## Blocked and decision

A reviewer blocks when it cannot judge: a deliverable is missing, a check cannot run, the
intent is ambiguous and the order does not settle it. Put exactly what is missing in
`blockers`, against the unit it belongs to. When it ends `decision`, the question is usually
whether a finding is acceptable — phrase it in the digest as the finding plus the two outcomes,
so an addendum can answer it in one line.
