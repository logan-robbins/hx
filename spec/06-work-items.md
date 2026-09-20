## 6. Work items

- **Filename:** `^(?<id>partner|[a-z]+-[0-9]{3})-(?<state>idle|queued|working|complete)\.md$`
- **Invariants:** one work item per id, the Partner included; every `working` item has a live tmux session and a `run/<id>/goal` marker; every `queued` item has a non-empty `after` with at least one entry whose `tasks.json` outcome is not `done`, and no goal marker. A live pane on a `working` item with no goal marker is a violation.

| From | To | Actor | Command |
|---|---|---|---|
| — | idle | Launcher | `hx launch <id>` |
| idle | working | Partner (itself included) | `hx dispatch <id> orders/<id>.md` when `after` is empty or every entry is `done` |
| idle | queued | Partner | `hx dispatch <id> orders/<id>.md` when an `after` entry is not yet `done` |
| queued | working | `hx` | inside `hx complete done` of the last unmet dependency |
| working | complete | HarnessAgent, as the last action of its goal | `hx complete <outcome>`; for `done`: checks pass, worktree clean, no open subagent stream |
| complete (`blocked`, `decision`) | working | Partner | `hx resume <id> orders/<id>.addendum.md` — keeps `## Tasks`, step state, memory, worktree, logs; appends the addendum to `## Order`; sends the goal |
| complete | idle | Partner | `hx bench <id>` (archives the body to `pods/<pod>/archive/<id>-<ts>.md`, resets from `templates/work-item.md`) |

**The order is a file.** `orders/<id>.md` is written by the Partner, its own `orders/partner.md` included, and contains exactly two sections, `## Order` and `## Definition of done`, plus optional frontmatter `after: [<id>…]`. `hx dispatch` refuses an order that lacks either section or the `### Checks` block. The order has no length limit; it is copied verbatim into the work item and recorded in `tasks.json`. No task text is ever a command-line argument.

**The task is a `/goal`, delivered by pointer.** What `hx goal` pastes is a fixed short form that never grows with the task:

```
/goal The order for <id> is in <abs path to work item>; read it first. Done when `hx complete <outcome>` has been run and its output line `HX-COMPLETE <id> <outcome>` appears.
```

The same pointer is sent on every conversation start of a `working` item: dispatch, resume, seam (`context` hook on `clear`), `hx restart`, and `hx launch` of an item that is already working. Claude Code is launched bare; nothing is passed as a prompt argument.

The evaluator reads the conversation including tool results, but calls no tools itself, so completion is proven by a deterministic line hx prints to stdout, which appears in the transcript, not by the agent's claims. The evaluator's no-progress guard (several turns with no tool use) never trips on a working agent. The Partner's job at dispatch is scoping: a definition of done the agent can satisfy and `hx complete` can check, sized to finish inside one context window with seams as backup rather than plan, and self-contained so the work item is the whole order.

**Definition of done** has two parts, both Partner-written in the order file:

1. An acceptance checklist the goal evaluator can judge from the transcript.
2. A `### Checks` fenced `bash` block. `hx complete done` runs it in the worktree with `bash -e`; every command must exit 0. When nothing is executable the check verifies the deliverable exists (`test -s report.md`); an empty block is refused at dispatch.

**Outcome mapping.** `outcome ∈ {done, blocked, decision, exhausted}`, set by `hx complete`. Goal evaluator verdict → outcome: met → `done`; impossible → `blocked` (cannot be done) or `decision` (needs the Partner or human to choose); check-in retries run out → `exhausted`. `hx complete done` is refused with `HX-CHECK-FAILED <id>` and the failing output when a check fails, the worktree is dirty, or a subagent stream is open; the item stays `working`, the goal stays active, and the agent fixes and retries. A refused completion is the agent's problem, never the Partner's.

**Body** (HarnessAgent-maintained; sections defined by `templates/work-item.md`):

`templates/work-item.md`, rendered by `hx dispatch` with the id, pod, `after`, timestamp, and the order file filled in:

```markdown
---
id: <id>
pod: <pod>
after: [<ids from the order frontmatter>]
outcome:
dispatched: <ts>
---
<orders/<id>.md verbatim: ## Order, then ## Definition of done with its ### Checks block>

## Standing instructions
- Keep `## Tasks` current: mark a task done the moment it is done, add tasks the moment you discover them. This section is what you get back after a seam.
- Commit each finished sub-task immediately with a descriptive message. `git log --oneline` is your memory of what is done; do not leave the worktree open.
- Read a file once. Note the fact you needed in `## Tasks` next to the task that needed it.
- Use subagents freely; each gets its own context file.
- Before finishing: write what should outlive this task below `## UPDATES BELOW ONLY` in your `AGENTS.md`.
- `hx complete done` runs `### Checks` and requires a clean worktree. On `HX-CHECK-FAILED`, fix and run it again.
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
- For `partner` the worktree rules do not apply (no worktree); its checks run in `HARNESS_ROOT`, typically `hx board --require-done <id>…`. `hx dispatch partner` and `hx resume partner` archive and wipe nothing: the Partner's session, streams, and state are continuous, and the pointer is queued for its next turn through `run/partner/goal-pending`.
