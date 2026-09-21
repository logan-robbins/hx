# Getting started: a fresh machine to a working Partner

Everything here is done once by you. After it, you only ever talk to the Partner.

## What you need

- macOS or Linux, `tmux`, `git`, Python 3.14, and `uv`.
- Claude Code installed and on your `PATH` at a version hx has been tested with (`claude
  --version`; the list is `src/hx/packaging/tested-claude-versions.json`, currently `2.1.278`).
- A Claude subscription (Max or Pro). No API key.

## 1. Install hx

From the repository:

```bash
cd ~/workspace/hx && uv build && uv tool install --python 3.14 dist/hx_harness-0.1.0-py3-none-any.whl
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

It records your Claude binary and version, lays out the instance, launches the Partner in tmux
session `partner`, and starts the UI in tmux session `ui` at `http://127.0.0.1:8765/`. Nothing under your own `~/.claude` is read or written; every agent runs
in its own home under `~/hx/run/<id>/home` with the token in its environment.

## 3. Talk to the Partner

```bash
tmux attach -t partner
```

Tell it what you want, in plain language, and where the code is. For example:

> The repo is at /Users/me/src/shop. Add a `--json` flag to the `report` command and make the
> frontend's Reports page call it. Backend first, then frontend.

The Partner decomposes that into orders, creates a backend and a frontend worker if none exist
(`be-001`, `fe-001`), points each at the directory you named, launches them, dispatches, and
waits. Each worker is its own Claude Code session (`tmux attach -t be-001` to watch one) with a
Companion session beside it keeping its state. When a worker finishes, the Partner is woken,
reads the digest, dispatches what comes next, and reports to you in chat when the whole ask is
done. If a worker hits a real decision, the Partner asks you in chat and resumes the worker
with your answer.

You never run `hx` yourself after this page. If you catch yourself wanting to, tell the Partner
instead.

## 4. Watching

- `tmux attach -t partner` is the only control surface. Detach with `Ctrl-b d`.
- The UI is already running: `hx install` started it in tmux session `ui`. Open
  `http://127.0.0.1:8765/`. It is read-only: the Board (every worker, its state and outcome,
  whether its session is alive, its context size and seams), an Agent drawer per worker (its
  work item file: the goal, checks, live `## Tasks`, deliverables, open decision, digest; plus
  whether its Companion is running a pass right now, and an **Open last compaction** link that
  opens the Companion's latest compaction in another window), an **Open session** link that opens the
  pane, step state, context file and stream tails in another window, Goals and Archive, and a
  Partner page with `PARTNER.md`, the board, and a chat box that sends a message to the Partner
  exactly as `hx wake partner` does.
  Full control (slash commands, interrupts) stays in `tmux attach -t partner`.
- `~/hx/PARTNER.md` is the Partner's memory: the fleet, open questions, decisions.

## 5. Personas

The Partner installs a persona when it creates a worker, from `~/hx/personas/<role>/AGENTS.md`:
`backend-engineer`, `frontend-engineer`, `release-engineer`. Each persona is the part above
`## UPDATES BELOW ONLY`; the worker's own memory accumulates below it. Edit a persona only by
telling the Partner what to change; it takes effect at that worker's next `hx restart`. The
Partner's own persona is `~/hx/config/partner/AGENTS.md`; the Companions' rules are
`~/hx/companion/BASE.md` and `~/hx/companion/roles/<role>.md`.

## 6. Stopping and starting

`hx up` relaunches the Partner and every working agent after a reboot; `hx heartbeat` restarts
dead sessions and tells the Partner. Both are plain commands; put them in your own cron or
login items if you want them automatic. To stop everything: `tmux kill-server` on the hx
server's sockets, or kill the sessions by name (`partner`, `be-001`, …).
