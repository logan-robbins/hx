# PARTNER.md

The Partner's current working state. Written only by the Partner and restored after a clear
or restart. Replace obsolete state instead of appending a conversation or dispatch journal.
Keep the current goal, applicable human instructions, unresolved questions, dependencies,
decision reasons and next scheduling action. Intermediate agent exchanges do not belong here.

Keep it current and keep it short. A fact that `hx board` regenerates does not belong here.

---

## The human

Who they are, what they are building, and how they want to be talked to. Fill this in from the
first conversation and correct it as you learn better.

- **Working on:**
- **Wants from this fleet:**
- **Prefers:**
- **Away when:** (so a `decision` is known to be waiting rather than assumed ignored)

## Standing instructions from the human

Things said once that apply from then on. Date each one. Remove one only when the human
withdraws it.

-

## The fleet

One line per id, what it is for, and how it has actually performed. This is what scoping the
next goal depends on, and none of it is recoverable from `hx board`.

| id | pod | role | what it is for | notes |
|---|---|---|---|---|
| partner | partner | partner | this agent | — |

## Pods

What each pod owns, and any boundary between pods that goals must respect.

-

## Open questions for the human

A worker that ended `decision`, or a question of your own. Keep the id, the question as it
will be asked, and the date it was raised. Delete a line only when the answer has gone out as
an addendum.

| date | id | question | asked in chat |
|---|---|---|---|

## Decisions made

Current decisions that still constrain the plan. Keep what was decided and why; delete or
replace decisions whose assumptions or purpose no longer apply.

| date | decision | why |
|---|---|---|

## Completed work

Only delivered outputs still needed by the current goal or its remaining dependencies.
Use validated completion status; omit successful check detail and obsolete completed work.

| date | id | outcome | what landed |
|---|---|---|---|

## Notes

Anything that does not fit above and is worth keeping.
