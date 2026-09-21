## 6. Work items

- **File:** `pods/<pod>/<id>-<state>.md`, one per worker id, state ∈ `idle`, `working`, `complete`. The state is the filename suffix: renaming the file is the transition, and reading the directory is reading the fleet. The Partner has no work item.

**Who renames.** `hx launch` creates the item `-idle`; `hx dispatch <id> <order-file>` renames `idle → working`; `hx complete <outcome>` renames `working → complete`; `hx resume <id> <addendum-file>` renames a `complete` item whose outcome is `blocked` or `decision` back to `working`; `hx bench <id>` renames `complete → idle`. The HarnessAgent may also rename its own item directly; the Partner and the agent never touch it at the same time. A `working` item has a live tmux session and a `run/<id>/goal` marker.

**The order is a file, and hx consumes it.** The Partner writes an order file — any path it likes — containing exactly two sections, `## Order` and `## Definition of done`. `hx dispatch` refuses an order that lacks either section or the `### Checks` block. The order has no length limit; it is copied verbatim into the work item and recorded in `tasks.json`, and the file is deleted once the dispatch succeeds. The order text then lives in exactly two places, `tasks.json` and the work item, and nowhere else. No task text is ever a command-line argument.

**The task is a `/goal`, delivered by pointer.** What `hx goal` pastes is a fixed short form that never grows with the task:

```
/goal The order for <id> is in <abs path to work item>; read it first. Done when `hx complete <outcome>` has been run and its output line `HX-COMPLETE <id> <outcome>` appears.
```

The same pointer is sent on every conversation start of a `working` item: dispatch, resume, seam (`context` hook on `clear`), `hx restart`, and `hx launch` of an item that is already working. Claude Code is launched bare; nothing is passed as a prompt argument. The Partner is never sent a `/goal`: the human tells it what to do in chat.

The evaluator reads the conversation including tool results, but calls no tools itself, so completion is proven by a deterministic line hx prints to stdout, which appears in the transcript, not by the agent's claims. The evaluator's no-progress guard (several turns with no tool use) never trips on a working agent. The Partner's job at dispatch is scoping: a definition of done the agent can satisfy and `hx complete` can check, sized to finish inside one context window with seams as backup rather than plan, and self-contained so the work item is the whole order.

**Definition of done** has two parts, both Partner-written in the order file:

1. An acceptance checklist the goal evaluator can judge from the transcript.
2. A `### Checks` fenced `bash` block. `hx complete done` runs it in the workdir with `bash -e`; every command must exit 0. When nothing is executable the check verifies the deliverable exists (`test -s report.md`); an empty block is refused at dispatch.

**Outcome mapping.** `outcome ∈ {done, blocked, decision, exhausted}`, set by `hx complete`. Goal evaluator verdict → outcome: met → `done`; impossible → `blocked` (cannot be done) or `decision` (needs the Partner or human to choose); check-in retries run out → `exhausted`. `hx complete done` is refused with `HX-CHECK-FAILED <id>` and the failing output when a check fails, the workdir is a git repository and is dirty, or a subagent stream is open; the item stays `working`, the goal stays active, and the agent fixes and retries. A refused completion is the agent's problem, never the Partner's.

**Body** (HarnessAgent-maintained; sections defined by `templates/work-item.md`):

`templates/work-item.md`, rendered by `hx dispatch` with the id, pod, timestamp, and the order file filled in:

```markdown
---
id: <id>
pod: <pod>
outcome:
dispatched: <ts>
---
<order file verbatim: ## Order, then ## Definition of done with its ### Checks block>

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
```

- `## Order` and `## Definition of done` are the Partner's, written once at dispatch; `hx resume` appends `## Order addendum <ts>` beneath `## Order`.
- `## Tasks`, `## Deliverables`, `## Commands`, `## Open decision` are the agent's, updated as it works. `## Tasks` is what the context file carries at a seam.
- `## Digest` is the Partner-facing summary, the only section written by the Companion, in its final pass inside `hx complete`. Two writers on this file is safe because the Companion writes only after the agent has stopped.
