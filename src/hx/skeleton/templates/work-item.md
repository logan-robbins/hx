---
id: {{id}}
pod: {{pod}}
outcome:
dispatched: {{dispatched}}
---
{{goal}}

## Definition of done
<yours, before your first edit: what makes this goal done, on top of your role's standard set>

## Orientation
<your explorer's: starting files and symbols at path:line, the test boundary, precedents, known dependencies; every line cites a graph result>

## Standing instructions
- Write `## Definition of done` before your first edit, on top of your role's standard set; your explorer drafts it, with `## Tasks` and `## Orientation`.
- Keep `## Tasks` current: mark a task done the moment it is done, add tasks the moment you discover them. This section is what you get back after a seam.
- Commit coherent completed changes. Keep current steps, exact commands and unresolved failures in task state; do not reconstruct them from Git history.
- Reuse supplied current findings and commands. Read only missing or changed source ranges needed for the next action.
- Parallelize independent work only when dependencies, write ownership and available resources permit it.
- Preserve reusable current application facts in the shared map through the companion. Do not write personal worker memory or retain obsolete investigation.
- `hx complete done` requires a clean workdir, no open subagent, and a `## Evidence` line for each line of `## Definition of done`. On `HX-CHECK-FAILED`, fix and run it again.
- Your last action is `hx complete <outcome>`. Nothing after it.

## Tasks
- [ ] …

## Status
<rewritten by hx at every boundary: step state, memory episodes, open subagent handles>

## Evidence
<one line per line of your definition of done: the hx tool-exec receipt, hx evidence event, test command and result, or commit that proves it>

## Deliverables

## Commands

## Open decision

## Digest
<written by the Companion at completion>
