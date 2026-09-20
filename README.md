# hx — HarnessAgent Runtime

A control plane for a fleet of Claude Code sessions that keeps working when you are not
watching.

Each agent is a **full Claude Code instance** — subagents, hooks, skills, compaction, all of
it — in its own tmux session, running under a `/goal`. hx gives it a task, keeps its context
coherent across every boundary, checks its work by running commands rather than by believing
it, and tells the supervisor when it is done.

You talk to one of them. The rest is theirs.

```bash
uv tool install hx
hx install
tmux attach -t partner
```

Then say what you want built. That is the entire human interface —
[docs/deploy.md](docs/deploy.md) is the ten-minute version of it.

## The idea

Three problems stand between an agent and unattended work. hx is three answers.

**An agent stops at the end of a turn.** So every agent runs under a `/goal`: a session-scoped
evaluator that decides, after each turn, whether the task is met, not yet met, or impossible.
The goal is never prose in a command line — it is a fixed pointer to a work item on disk, and
the order it points at can be as long as it needs to be.

**An agent forgets when its context is cut.** So hx never lets Claude's compaction summarizer
decide what survives. Each agent is paired one-to-one with a small **Companion** model that
reads the agent's tool-call stream and maintains a bounded, structured *step state*: open steps
with their next action, closed steps with their commit shas, decisions with reasons, dead ends,
and the facts the agent had to read a file to learn. At a boundary, hx composes that plus the
agent's memory, its order, and its own task list into **one file**, and a hook hands over the
path. The agent reads one file and continues. It does not search, and it does not re-read what
it already knew.

The cut itself is a **seam**: `/clear` plus rehydration, taken at a quiet turn boundary either
when a step closes or when context crosses a threshold. It is planned, not survived.

**An agent that says it is done may not be.** So `hx complete done` is machine-checked. The
order carries a `### Checks` bash block; it runs in the agent's worktree, the worktree must be
clean, and no subagent stream may be open. Any failure prints `HX-CHECK-FAILED` with the
output, changes nothing, and leaves the goal active — the agent fixes it and retries. Only
success prints `HX-COMPLETE <id> done`, and that line, in the transcript, is what the goal
evaluator reads. Not a claim; a result.

## How work flows

You talk to the **Partner**. It is a HarnessAgent like any other — its own work item, its own
Companion, the same commands — and it is the only one you talk to.

1. You say what you want. The Partner writes `orders/partner.md` from the conversation and
   dispatches *itself*. From the next turn it is working under a goal, not waiting on you.
2. It decomposes the work into one order file per item, each with a definition of done and
   checks that can actually run, sized to finish inside one context window.
3. `hx dispatch eng-001 orders/eng-001.md eng-002 orders/eng-002.md …` — the whole plan in one
   call. Items with unmet `after` dependencies land `queued`; hx promotes each one the moment
   its last dependency completes.
4. Workers work, spawn subagents, commit as they go, take seams, and finish with
   `hx complete <outcome>`.
5. Completion wakes the Partner over cross-session messaging. It reads the Companion-written
   digest, updates its memory, and acts: bench a `done`, resume a `blocked` or `decision` with
   an addendum that keeps the agent's step state and worktree intact, split an `exhausted`.
6. When the plan's checks pass, the Partner completes its own item and tells you.

You run no hx command at any point.

## What it will not do

hx never writes to your `~/.claude`, never opens your checkouts, and never pushes to your
remote. Agents work in sparse worktrees cut from a **bare mirror**, on branches that exist only
in that mirror, without the repo's own `.claude/`. One command in the entire system touches
your remote, and only when you tell the Partner to run it.

This is enforced, not promised: a guard test records your Claude configuration surface before
the suite runs and fails on any difference, and hx refuses a `HARNESS_ROOT` that resolves
inside `~/.claude`. [docs/two-worlds.md](docs/two-worlds.md) has the line-by-line account.

## Shape of an instance

```
$HARNESS_ROOT/
  config/CLAUDE.md            the one CLAUDE.md that loads
  config/<id>/AGENTS.md       persona above the header → system prompt
                              the agent's own memory below it → context file
  orders/<id>.md              Partner-written; ## Order + ## Definition of done
  tasks.json                  the control-plane record, written only by hx
  pods/<pod>/<id>-<state>.md  the work item; state is the filename suffix
  logs/<id>/…                 raw stream, read only by the Companion
  state/<id>/…                step state, written only by the Companion
  run/<id>/…                  context files, per-agent Claude home, markers
  wt/<id>/                    sparse worktree from repos/<name>.git
```

`config/` is the part worth committing to your own git. Everything else is runtime state.

## Status

Pre-release, built against the spec in [`spec/`](spec/) —
[`spec/HARNESS_SPEC.md`](spec/HARNESS_SPEC.md) is the compiled read; the numbered section files
beside it are authoritative. Milestones and their acceptance tests are in
[`spec/13-build-order.md`](spec/13-build-order.md).

- Python 3.14, standard library only. No runtime dependencies.
- Requires `tmux`, `git`, and a `claude` binary at a version in
  [`packaging/tested-claude-versions.json`](packaging/tested-claude-versions.json).
- Tested on macOS and Linux.

## Docs

- [docs/deploy.md](docs/deploy.md) — the one-time setup, then talk to the Partner
- [docs/two-worlds.md](docs/two-worlds.md) — how your own Claude stays untouched
- [docs/github-plan.md](docs/github-plan.md) — how this repository gets published and released
- [CONTRACTS.md](CONTRACTS.md) — the JSON shapes shared between the CLI and the UI
- [CHANGELOG.md](CHANGELOG.md)

## License

MIT. See [LICENSE](LICENSE).
