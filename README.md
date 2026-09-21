# hx — HarnessAgent Runtime

A control plane for a fleet of Claude Code sessions that keeps working when you are not
watching.

Each agent is a **full Claude Code instance** — subagents, hooks, skills, all of it — in its
own tmux session, running under a `/goal`. hx gives it a task, keeps its context coherent
across every boundary, checks its work by running commands rather than by believing it, and
tells the Partner when it is done.

You talk to one of them. The rest is theirs.

**→ [docs/getting-started.md](docs/getting-started.md)** takes you from a fresh machine to a
working Partner. After that, [docs/operating.md](docs/operating.md) is the day-to-day.

## The idea

Three problems stand between an agent and unattended work. hx is three answers.

**An agent stops at the end of a turn.** So every worker runs under a `/goal`: a session-scoped
evaluator that decides, after each turn, whether the task is met, not yet met, or impossible.
The goal is never prose in a command line — it is a fixed pointer to a work item on disk, and
the order it points at can be as long as it needs to be.

**An agent forgets when its context is cut.** So hx never lets Claude's compaction summarizer
decide what survives. Each agent is paired one-to-one with a small **Companion** — another
Claude Code session in the next tmux window, with two tools and nothing else — that reads the
agent's tool-call stream and keeps a bounded, structured *step state*: open steps with their
next action, closed steps with their commit shas, decisions with reasons, dead ends, and the
facts the agent had to read a file to learn. At a boundary, hx composes that plus the agent's
memory, its order and its own task list into **one file**, and a hook hands over the path. The
agent reads one file and continues. It does not search, and it does not re-read what it
already knew.

The cut itself is a **seam**: `/clear` plus rehydration, taken at a quiet turn boundary either
when a step closes or when context crosses a threshold. It is planned, not survived.

**An agent that says it is done may not be.** So `hx complete done` is machine-checked. The
order carries a `### Checks` bash block; it runs in the agent's `workdir`, the directory must
be clean if it is a git repository, and no subagent stream may be open. Any failure prints
`HX-CHECK-FAILED` with the output, changes nothing, and leaves the goal active — the agent
fixes it and retries. Only success prints `HX-COMPLETE <id> done`, and that line, in the
transcript, is what the goal evaluator reads. Not a claim; a result.

## How work flows

You talk to the **Partner**, in `tmux attach -t partner`. It is a Claude Code session with its
own Companion and its own memory file, and it is the only one you talk to.

1. You tell it what you want, in chat. That is its goal — there is no order file for the
   Partner, no dispatch, and no `/goal`. It asks back what is unclear and starts.
2. It decomposes the work into one order file per item, each with a definition of done and
   checks that can actually run, sized to finish inside one context window.
3. It creates the workers it needs — a config directory, a persona, a `workdir` it picks —
   and `hx dispatch be-001 <order-file>` starts one. Several id/file pairs in one call start
   several at once. **There is no dependency field and no queue:** if one item must wait for
   another, the Partner waits and then dispatches, the way you would.
4. Workers work, spawn subagents, commit as they go, take seams, and finish with
   `hx complete <outcome>`.
5. Completion wakes the Partner over cross-session messaging. It reads the Companion-written
   digest, updates `PARTNER.md`, and acts: dispatch what that unblocks, resume a `blocked` or
   `decision` with an addendum that keeps the agent's step state intact, split an `exhausted`.
6. When the ask is met, the Partner tells you in chat. Nothing completes the Partner; you do.

You run no hx command at any point.

## What it will not do

hx never writes to your `~/.claude` — not your credentials file, not the macOS Keychain, on any
platform. It authenticates with one long-lived token of its own that you generate in one
command.

It also does not manage your source control. A worker's `workdir` is a directory somebody
chose; hx creates no repository, no branch and no worktree, and pushes nothing anywhere. The
only git it ever runs is `git status --porcelain` inside `hx complete done`, and only when the
directory is a git repository.

This is enforced, not promised: a guard test records your Claude configuration surface before
the suite runs and fails on any difference, hx refuses a `HARNESS_ROOT` that resolves inside
`~/.claude`, and `packaging/e2e-deploy.sh` installs into a throwaway `HOME` containing a
planted `~/.claude` whose every string must be absent afterwards.
[docs/two-worlds.md](docs/two-worlds.md) has the line-by-line account.

## Shape of an instance

```
$HARNESS_ROOT/
  PARTNER.md                  the Partner's memory
  config/CLAUDE.md            the one CLAUDE.md that loads
  config/<id>/AGENTS.md       persona above the header → system prompt
                              the agent's own memory below it → context file
  config/<id>/harness.json    model, effort, role, and the workdir it works in
  personas/<role>/AGENTS.md   the personas the Partner copies to make a worker
  templates/                  work-item, order, addendum, worker
  tasks.json                  the control-plane record, written only by hx
  pods/<pod>/<id>-<state>.md  the work item; the state is the filename suffix
  logs/<id>/<id>-main.jsonl   raw stream, one line per tool call, read by the Companion
  logs/<id>/<id>-sNNN-*.jsonl one stream per subagent, open until it stops
  state/<id>/…                step state, written only by the Companion
  run/<id>/…                  context files, per-agent Claude home, turn and goal markers
  seed/token                  the one credential, mode 0600
```

`config/` is the part worth committing to your own git. Everything else is runtime state.

## Status

Pre-release, built against the spec in [`spec/`](spec/) —
[`spec/HARNESS_SPEC.md`](spec/HARNESS_SPEC.md) is the compiled read; the numbered section files
beside it are authoritative. Milestones and their acceptance tests are in
[`spec/13-build-order.md`](spec/13-build-order.md).

- Python 3.14, standard library only. No runtime dependencies.
- Requires `tmux`, `git`, and a `claude` binary at a version in
  [`src/hx/packaging/tested-claude-versions.json`](src/hx/packaging/tested-claude-versions.json).
- A Claude subscription. No API key: the Companion runs on the same token as the agents.
- Tested on macOS and Linux.

The install path works end to end — `packaging/e2e-deploy.sh` builds the wheel, installs it as
a uv tool into a `HOME` that did not exist a moment ago, runs the full `hx install` including
its stop for the seed token, launches a worker, and proves no Claude home was touched. What is
still unproven is everything the agents do once running: the Companion, seams, and the metrics
that judge them. That needs a live Claude Code, not a packaging script.

## Docs

- [docs/getting-started.md](docs/getting-started.md) — a fresh machine to a working Partner
- [docs/operating.md](docs/operating.md) — what you see and do day to day
- [docs/two-worlds.md](docs/two-worlds.md) — how your own Claude stays untouched
- [docs/companion-eval.md](docs/companion-eval.md) — how the Companion gets measured
- [docs/github-plan.md](docs/github-plan.md) — how this repository gets published
- [CONTRACTS.md](CONTRACTS.md) — the JSON shapes shared between the CLI and the UI
- [CHANGELOG.md](CHANGELOG.md)

## License

MIT. See [LICENSE](LICENSE).
