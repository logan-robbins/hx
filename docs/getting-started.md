# Getting started: a fresh machine to a working Partner

Everything here is done once by you. After it, you only ever talk to the Partner. If a coding
agent is doing this for you, hand it `src/hx/skills/hx-setup/SKILL.md`: it is this page as a
procedure, and it leaves the token step (§2) to you.

## What you need

- macOS or Linux, `tmux`, `git`, Python 3.14, and `uv`.
- Claude Code installed and on your `PATH`. hx records where the binary is and launches
  every agent with it.
- A Claude subscription (Max or Pro), or an Anthropic API key supplied below.

Alternatively, supply an Anthropic API key in a dotenv file. `hx` uses an existing
`seed/token` OAuth credential first and falls back to the file when that token is absent.
`OPENAI_API_KEY` and `XAI_API_KEY` in the same file can seed Codex and
Grok workers. Other variables in the file are available to agents at startup.

## 1. Install hx

From the repository:

```bash
cd ~/workspace/hx && uv build && uv tool install --python 3.14 dist/hx_harness-1.0.0-py3-none-any.whl
```

Check it:

```bash
hx doctor
```

## 2. Create your instance

Pick a directory for the instance. It must not be inside `~/.claude` (hx refuses that).

```bash
hx install --root ~/hx
```

To use a local dotenv fallback, pass `--env-file /absolute/path/to/.env`. The env file
path is saved in the instance, so later `hx up` runs use it automatically. Existing
credentials under `seed/` always take precedence over the env file.

The first run stops with exit 4 and prints two steps. Do them:

```bash
claude setup-token
```

This opens the browser once for consent on your subscription and prints a token starting with
`sk-ant-oat01-`. Paste it into the file hx named:

```bash
printf '%s\n' 'PASTE_THE_TOKEN' > ~/hx/seed/token && chmod 600 ~/hx/seed/token
```

Then run the install again:

```bash
hx install --root ~/hx
```

It records your Claude binary, lays out the instance, launches the Partner in tmux
session `partner`, and starts the UI in tmux session `ui` at `http://127.0.0.1:8765/`. Nothing under your own `~/.claude` is read or written; every agent runs
in its own home under `~/hx/run/<id>/home` with the token in its environment.

## 3. Talk to the Partner

```bash
tmux attach -t partner
```

Tell it what you want, in plain language, and where the code is. For example:

> The repo is at /Users/me/src/shop. Add a `--json` flag to the `report` command and make the
> frontend's Reports page call it. Backend first, then frontend.

The Partner explores current graph dependencies and searches for existing capabilities before
building. It defines observable dependency packets, maximizes useful parallel work, prepares
separate worker worktrees, reserves ready runs, and explicitly launches their native controllers.
Every directive uses this flow; apparent request size does not determine packet count.
Workers bind exact checks before edits and obtain independent approval. Typed blockers bring
unresolved decisions back to the Partner; changed scope requires stopped/revised work.

The runtime verifies exact source/dependencies, approved checks, managed-operation drain,
output publication, and ownership release. Packet coverage is separate from integration,
intent review, landing, fixed release validation, and external deployment. The Partner reports
what the requested outcome and evidence establish.

## 4. Watching

The UI address reported by setup projects ledger dependency packets, execution/leases,
progress, approved bindings, check/checkpoint evidence, and delivery candidates. Details can
name task, run, worker, or plan. Recorded state does not itself prove current readiness.
The Partner chat sends the human's explicit message; it does not create a task transition.

`PARTNER.md` retains current decisions, pending questions, constraints, and next scheduling
consequences. The ledger owns fleet state. Ask the Partner for status or changes in chat.

## 5. Personas

Every worker is handed, at every boundary, its role's
`~/hx/personas/<role>.md` (`backend-engineer`, `frontend-engineer`, `release-engineer`,
`qa-engineer`) plus an identity line carrying the one paragraph in its `harness.json` `purpose`
that says what that id is for; nothing is copied per worker. What a worker learns becomes a learnings rule for its role
(§6), not a note in a file of its own. Edit a persona only by telling the Partner what to
change; it takes effect at each worker's next boundary (`/clear`, compaction, restart). The
Partner's own persona is `~/hx/personas/partner.md`; the Companions' rules are
`~/hx/companion/BASE.md` and `~/hx/companion/roles/<role>.md`.

## 6. Learnings

Each agent has a Learning Companion alongside its Context Companion. It watches captured
evidence with its own cursor and budget and proposes reusable rules. Agents can also use
`hx learn propose --category <c> --signal … --check … --evidence …`. A different authorized
session reviews with `hx learn pending`, then accept, merge or reject; assessment alone
cannot publish a rule. Reviewed global and matching role rules are delivered completely to
all applicable agents, subagents and both Companions. Active workers adopt changed rules at
their next controlled boundary through a drained restart. [Learnings](learnings.md) describes
the evidence and review contracts.

## 7. Stopping and starting

`hx up` recovers the Partner/UI/root service. `hx heartbeat` advances existing durable native
intents, without starting unlaunched reservations. Worker drain/shutdown name the original
admitted run and launch request; uncertain process ownership retains leases until reconciled.
The Partner uses `hx plan stop` when a task must be stopped or revised.
