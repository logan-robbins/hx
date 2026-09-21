---
id: {{id}}
pod: {{pod}}
outcome:
dispatched: {{dispatched}}
---
{{goal}}

## Standing instructions
- Keep `## Tasks` current: mark a task done the moment it is done, add tasks the moment you discover them. This section is what you get back after a seam.
- Commit each finished sub-task immediately with a descriptive message. `git log --oneline` is your memory of what is done; do not leave the workdir dirty.
- Read a file once. Note the fact you needed in `## Tasks` next to the task that needed it.
- Use subagents freely; each gets its own context file.
- Before finishing: write what should outlive this task below `## UPDATES BELOW ONLY` in your `AGENTS.md`.
- `hx complete done` runs `### Checks` and requires a clean workdir. On `HX-CHECK-FAILED`, fix and run it again.
- Your last action is `hx complete <outcome>`. Nothing after it.

## Tasks
- [ ] …

## Deliverables

## Commands

## Open decision

## Digest
<written by the Companion at completion>
