# Two worlds, one binary

*How your own Claude Code stays exactly as it was.*

hx runs the same `claude` binary you already have. It does not wrap it, patch it, or install a
second copy. Everything that separates a harness session from your own session is
configuration passed at launch — a different config directory, a different working directory,
a pinned version — so there is no shared state to leak through.

The short answer: **hx reads your `~/.claude` once, at install, to copy your credentials. It
never writes there. It never touches your checkouts. It never pushes to your remote unless you
tell the Partner to.**

The long answer follows.

## Where each side keeps its settings

Your normal `claude` reads `~/.claude`. A harness session is launched with
`CLAUDE_CONFIG_DIR=$HARNESS_ROOT/run/<id>/home`, so for the whole of its life that directory
*is* its `~/.claude`. Settings, hooks, skills, credentials, memory, transcripts — all of it
resolves there, and none of it resolves to yours. There is one such directory per agent, so
agents do not leak into each other either.

## Settings and hooks

Yours are whatever you have configured. A harness home's `settings.json` is written by
`adapters/claude/install.sh` and holds exactly what hx needs: its own hooks with the agent's id
baked into each command, the bypass-permissions acceptance, `claudeMdExcludes` for the product
repo's instruction files, instruction-files mode `claude-md`, and — for the Partner only —
`crossSessionInbound: accept` so completions can wake it.

Your hooks never run in a harness session and hx's hooks never run in yours. They are
different files in different directories.

## Skills

Yours live in `~/.claude/skills` and are untouched. A harness home gets exactly one skill
installed from the package: `hx-partner` for the Partner, `hx-worker` for everyone else.
Nothing is symlinked into your skill directory, and no skill of yours is visible to an agent.

## CLAUDE.md and AGENTS.md

Yours load normally. In a harness session there is exactly one CLAUDE.md —
`$HARNESS_ROOT/config/CLAUDE.md`, copied into each home — and the product repo's own
`CLAUDE.md` and `AGENTS.md` are excluded by `claudeMdExcludes` plus instruction-files mode
`claude-md`, so nothing inside the worktree is discovered. The per-agent identity files
(`config/<id>/AGENTS.md`) live outside any worktree for the same reason: Claude Code's own
`AGENTS.md` discovery cannot see them, and they reach the agent only by the paths hx chooses.

## Memory and transcripts

Yours accumulate in `~/.claude`, as always. Each harness home has its own, and `hx dispatch`
wipes exactly `home/projects/`, `home/file-history/`, and `home/history.jsonl` when it starts a
new task on that id. `settings.json`, `.credentials.json`, `agents/`, `skills/`, `plugins/`,
and `agent-memory/` are siblings of those and survive.

Claude Code's auto memory is keyed by git repo. Without a config directory per agent, every
worktree of the same product repo would share one memory pool — including yours. The per-agent
home is what prevents that.

## Credentials

Your credentials are read once, at `hx install --from-user-config`, and copied into
`$HARNESS_ROOT/seed/home`. Otherwise you log `seed/home` in yourself, once, and your own
`~/.claude` is not read at all. Every agent home is seeded from `seed/home`, and those copies
refresh independently of yours. Nothing is written back.

## Permissions

Yours are whatever you chose. Every harness session — Partner, worker, and every subagent —
launches with `--dangerously-skip-permissions`. That is deliberate and not configurable: these
sessions are unattended, and a prompt nobody is there to answer is a hung agent.

What replaces the permission prompt is a `PreToolUse` guard hook that denies writes outside
each agent's own lane: another agent's identity file, the `config/` and `companion/`
directories, `orders/` for anyone but the Partner, anything under `logs/`, `state/`, `run/`,
`archive/`, `tasks.json`, and another agent's work item. Bypass mode does not disable hooks, so
those rules hold regardless.

Your own sessions are unaffected by any of it.

## Working directory

Yours is your checkout. A harness agent works in `$HARNESS_ROOT/wt/<id>`, a worktree cut from
a **bare mirror** at `$HARNESS_ROOT/repos/<name>.git`, on branch `agent/<id>`. The mirror is
fetched from your upstream; your own clone is never opened, never written, and never has a
worktree added to it.

The worktree is sparse: `git sparse-checkout set --no-cone '/*' '!/.claude/'`, so the repo's
own `.claude/` is not even present on disk in a harness worktree. Those project settings belong
to your interactive work, and they could add hooks or permission rules that fight hx. Set
`keep_claude_dir: true` in `config/repo.json` to opt a project back in when its `.claude/`
carries skills the agents actually need.

## Your remote

Agent branches exist only in the bare mirror. Nothing reaches your remote until you tell the
Partner to push a specific id, and `hx push <id>` — `git push upstream agent/<id>`, from the
mirror — is the only command in hx that touches it. There is no automatic push, no automatic
PR, and no background sync.

## System prompt

Yours is the default. A harness session gets the default plus one file:
`--append-system-prompt-file $HARNESS_ROOT/run/<id>/persona.md`, regenerated at every launch
from the part of `config/<id>/AGENTS.md` above `## UPDATES BELOW ONLY`. That is why an agent
always knows who it is, at zero cost, across compaction and `/clear`.

## Version

Yours auto-updates. Harness sessions run with `DISABLE_AUTOUPDATER=1` and the exact binary path
recorded in `config/claude.json` at install. The pin changes only through `hx upgrade`, which
refuses a version the live test suite has not passed on. So an upstream release cannot change
the behaviour of a running fleet underneath you — and it cannot hold your own Claude back
either, because the pin is a path and an env var in one process, not a system-wide setting.

## Visibility

Your sessions are in your terminal. Harness sessions are in tmux, reachable with
`tmux attach -t <id>` or through `hx ui` on `127.0.0.1`. They do not appear in your session
list, your history, or your `/resume` picker, because those live in `~/.claude`.

## The whole table

| | Your normal `claude` | A harness session |
|---|---|---|
| Config dir | `~/.claude` | `$HARNESS_ROOT/run/<id>/home` |
| Settings and hooks | Yours | Written by hx per agent |
| Skills | Your `~/.claude/skills` | `hx-partner` or `hx-worker` only |
| CLAUDE.md | Yours and the repo's | `config/CLAUDE.md` only; the repo's excluded |
| Memory and transcripts | Yours, accumulating | Per home, wiped at each dispatch |
| Credentials | Yours | A copy from `seed/home`, refreshed independently |
| Permissions | Whatever you chose | Bypass, always; guard hook instead |
| Working dir | Your checkout | `wt/<id>`, sparse, without the repo's `.claude/` |
| System prompt | Default | Default + `run/<id>/persona.md` |
| Version | Auto-updating | Pinned; `DISABLE_AUTOUPDATER=1` |
| Goal | None | The `/goal` pointer to a work item |
| Visibility | Your terminal | `tmux attach` or `hx ui` |

## How this is verified, not just asserted

A guard test records a manifest of the user's Claude configuration surface before any test
runs and fails on any difference afterwards. It runs in every milestone check, not only at the
end. A second guard test asserts that hx refuses a `HARNESS_ROOT` that is, or resolves through
symlinks into, `~/.claude`. The packaging milestone additionally requires that `~/.claude`, the
user's checkout, and the remote are byte-identical before and after a full end-to-end run.
