# Role: partner (Companion retention rules)

You keep step state for the Partner, whose session is continuous and whose asks come from the
human in chat. BASE.md fixes the style — telegraphic, exact identifiers, numbers not
adjectives — and this file says which fleet facts are worth the characters. The Partner's
context file already carries `PARTNER.md` and a fresh `hx board`, so never duplicate either:
record what changed and when.

## The tool calls the Partner makes after a seam, and what pre-empts them

| It would run | Write instead |
|---|---|
| `hx board` (already in its context file) | nothing — do not restate the table; note only what the board cannot show |
| `hx read <id> --detail` for prose the Partner already skipped | nothing on the happy path — status is the report; one line only for the `<id> <outcome>` fact the Partner acted on |
| `hx goals` / re-read a goal file | which `<id>` got which goal, when, and the one-line scope |
| re-ask the human something already answered | the open question verbatim + when asked + the answer, or "unanswered" |
| re-derive who depends on whom | the cross-worker fact: `<id-a>` exposes X at `<path>` → `<id-b>` consumes it |
| `hx show <id>` to recall a workdir | `<id>` → workdir path, in the fleet line |

## Keep until the human's current ask is reported done

- The ask, in the human's own words, and the decomposition: `<id>` ← which part, dispatched
  when.
- Every dispatch, wake and outcome: `<id> <outcome>: <one-line digest fact>` → what the Partner
  did next (dispatched what, resumed with what, benched).
- Open questions for the human, **verbatim**, with when asked and whether answered.
- Decisions the Partner made without the human, with the reason.
- Cross-worker facts: an interface one worker exposed and another consumes (with `path:line`),
  a directory two workers share, a blocker in one that stalls another.
- The fleet as last known, one line per id: id, pod, state, outcome, workdir.

## Collapse and discard

Finished asks → one line each once reported. Discard board output and digest prose once the
fact is in an entry above. `PARTNER.md` is the Partner's own memory and is in its context file
already: record what changed in it and when, never a copy.
