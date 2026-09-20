# Two worlds, one binary

*How your own Claude Code stays exactly as it was.*

hx runs the same `claude` binary you already have. It does not wrap it, patch it, or install a
second copy. Everything that separates a harness session from your own session is
configuration passed at launch — a different config directory, a different working directory,
a pinned version — so there is no shared state to leak through.

The short answer: **hx reads your `~/.claude` at most once, at install, and only if you ask it
to, to copy one file. It never writes there. It never touches your checkouts. It never pushes
to your remote unless you tell the Partner to.**

The long answer follows. Every claim in it was checked against
`adapters/claude/install.sh` and `adapters/claude/start.sh` — the two scripts that actually
write a harness home and launch a session — rather than against the spec they implement.

## Where each side keeps its settings

Your normal `claude` reads `~/.claude`. A harness session is launched with
`CLAUDE_CONFIG_DIR=$HARNESS_ROOT/run/<id>/home`, so for the whole of its life that directory
*is* its `~/.claude`. Settings, hooks, skills, credentials, memory, transcripts — all of it
resolves there, and none of it resolves to yours. There is one such directory per agent, so
agents do not leak into each other either.

## Settings and hooks

Yours are whatever you have configured. A harness home's `settings.json` is written from
scratch by `adapters/claude/install.sh` — nothing of yours is copied into it, merged with it,
or read while writing it. Key by key, that file holds:

| Key | Value | Why |
|---|---|---|
| `hooks` | the hx hook events of spec 09.1, each command carrying `--id <agent>` and the absolute `hook_bin` from `config/hx.json` | six events for the Partner; a worker also gets `SubagentStart`, `SubagentStop` and a second `PostToolUse` matching `Agent`, because only workers spawn subagents hx tracks as streams |
| `skipDangerousModePermissionPrompt` | `true` | the bypass acceptance, so no launch is ever interactive |
| `pluginConfigs["agents-md@builtin"].options.instructionFiles` | `"claude-md"` | no `AGENTS.md` anywhere is ever discovered |
| `claudeMdExcludes` | seven globs under `<root>/wt/**` and `<root>/repos/**` | the product repo's `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md` and the `.claude/` copies of each |
| `crossSessionInbound` | `"accept"` | **Partner only.** A worker's settings do not contain the key at all |

Your hooks never run in a harness session and hx's hooks never run in yours. They are
different files in different directories, and the harness file is regenerated from the package
at every `hx launch` and every `hx upgrade`, so nothing accumulates in it either.

## Skills

Yours live in `~/.claude/skills` and are untouched. A harness home gets exactly one skill
installed from the package: `hx-partner` for the Partner, `hx-worker` for everyone else. It is
copied, not symlinked — `install.sh` removes any previous copy and copies the directory in —
so nothing in a harness home points back at the package or at you, and no skill of yours is
visible to an agent.

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

Your credentials are read once, at `hx install --from-user-config <path>`, and copied into
`$HARNESS_ROOT/seed/home/.credentials.json` at mode 0600. **That is the only file taken.** Not
your `settings.json`, not your `CLAUDE.md`, not your skills or agents or hooks — only the
credentials, plus the bypass-permissions acceptance merged into the harness's own settings.
Otherwise you log `seed/home` in yourself, once, and your own `~/.claude` is not read at all.

Every agent home is seeded from `seed/home`, and `install.sh` refuses to write a home when
`seed/home/.credentials.json` is missing rather than producing one that would stop at a login
prompt on first launch. `start.sh` refuses again at launch for the same reason. Those copies
refresh independently of yours, and nothing is ever written back.

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

`start.sh` refuses to launch a worker whose worktree does not exist, rather than falling back
to some other directory.

## Your remote

Agent branches exist only in the bare mirror. Nothing reaches your remote until you tell the
Partner to push a specific id, and `hx push <id>` — `git push upstream agent/<id>`, from the
mirror — is the only command in hx that touches it. There is no automatic push, no automatic
PR, and no background sync.

## System prompt

Yours is the default. A harness session gets the default plus one file:
`--append-system-prompt-file $HARNESS_ROOT/run/<id>/persona.md`, regenerated by `start.sh`
immediately before `exec` from the part of `config/<id>/AGENTS.md` above
`## UPDATES BELOW ONLY`. That is why an agent always knows who it is, at zero cost, across
compaction and `/clear` — and why the agent's own memory, which lives *below* that line, never
leaks into the system prompt. `start.sh` refuses to launch an `AGENTS.md` with no header line
at all, because it could not tell the two halves apart.

The launch is exactly spec 17.4 and nothing more:

```
env HARNESS_ID=<id> HARNESS_ROOT=<root> CLAUDE_CONFIG_DIR=<root>/run/<id>/home DISABLE_AUTOUPDATER=1 \
  <config/claude.json bin> --dangerously-skip-permissions --effort <level> --model <full id> \
  --append-system-prompt-file <root>/run/<id>/persona.md
```

No prompt argument, no `--resume`, no compaction environment variables. Everything the agent is
ever told arrives as a `/goal` pointer pasted into its pane or as a file path from a hook.

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

`start.sh` also pipes each pane to `$HARNESS_ROOT/logs/<id>/<id>-pane.log`, which is what the
UI falls back to when a session has died. It is instance state like everything else under
`logs/`, archived by the next `hx dispatch`, and it never leaves the instance.

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

## What is built today

This page describes the finished system. Pre-release, the parts that create an instance are
still landing: the full `hx install` (the seed login, `--from-user-config`, the repo mirror,
the sparse worktree and the boot units) is build-4, and until it lands `hx install` requires
`--skeleton-only` and says so. Everything about the *per-agent home* above — the settings file
key by key, the credential seeding and its refusals, the persona derivation, the launch argv,
the skills copy — is what `install.sh` and `start.sh` do today, and
`packaging/e2e-deploy.sh` asserts the rest the moment build-4 lands.

## How this is verified, not just asserted

A guard test records a manifest of the user's Claude configuration surface before any test
runs and fails on any difference afterwards. It runs in every milestone check, not only at the
end. A second guard test asserts that hx refuses a `HARNESS_ROOT` that is, or resolves through
symlinks into, `~/.claude`.

Beyond that, `packaging/e2e-deploy.sh` runs the whole install into a `HOME` that did not exist
a moment ago, pointed at a **fake** user Claude home it builds for the purpose — one carrying a
deny-everything hook, a banner `CLAUDE.md` and a skill, all of which must *not* be copied. It
then asserts that only the credentials were taken, that the fake home is byte-identical before
and after, that the agent home carries none of those planted strings, that the worktree has no
`.claude/`, that the rendered units carry no unsubstituted token, and that the real `~/.claude`
manifest is unchanged. Planting strings that must not appear is the only way to tell "isolated"
from "we did not look".
