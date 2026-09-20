# Deploying hx

This is everything a human does. It happens once. After it, you talk to the Partner and
nothing else.

## What you are setting up

`hx` is a control plane for a fleet of Claude Code sessions. Each one — including the
supervising **Partner** — is a full Claude Code instance in its own tmux session, running under
a `/goal`, with its own config directory, its own worktree, and a small **Companion** model
that keeps its continuity across context boundaries.

Two things exist and never mix:

- The **package** `hx`: Python 3.14, zero runtime dependencies. Installed with `uv tool install`.
- The **instance** `$HARNESS_ROOT`: your data. Created by `hx install`. `/srv/hx` on a server,
  `~/hx` on a workstation.

Upgrading the package never writes into your instance except through `hx upgrade`.

## Before you start

- **A non-root user.** hx refuses to run as root, and Claude Code refuses
  `--dangerously-skip-permissions` under root or sudo. Every agent here runs with that flag.
- **`tmux`** and **`git`** on `PATH`.
- **Python 3.14 or newer.**
- **The `claude` binary**, at a version in the package's tested list
  (`packaging/tested-claude-versions.json`). `hx install` checks this and stops with the
  version to install if yours is not listed.
- **A Claude account you are willing to log a second config directory into.** The harness uses
  its own credentials, seeded from a home you log into once. You can reuse your existing
  credentials instead — see step 3.

## 1. Install the package

```bash
uv tool install hx
hx --version
```

`pipx install hx` works too. Nothing is installed into your `~/.claude`.

## 2. Create the instance

```bash
export HARNESS_ROOT=~/hx        # or /srv/hx on a server
hx install
```

This refuses root; checks `tmux`, `git`, Python, and the `claude` binary, recording
`{bin, version}` in `config/claude.json`; and creates `$HARNESS_ROOT` from the package
skeleton: `config/CLAUDE.md`, `config/models.json`, `config/partner/{AGENTS.md,SUBAGENTS.md,
harness.json}`, `companion/`, `templates/`, an empty `orders/`, and `pods/partner/`.

## 3. Seed the login

`hx install` runs, once, interactively:

```bash
CLAUDE_CONFIG_DIR=$HARNESS_ROOT/seed/home claude
```

Log in and accept bypass permissions. `seed/home` is the only Claude home a human ever types
into; every agent home is copied from it. This is the step that makes every later launch
non-interactive.

If you would rather not log in a second time:

```bash
hx install --from-user-config
```

which copies the credentials out of your `~/.claude` instead. It **reads** that directory and
never writes to it.

## 4. Point it at your repo

```bash
hx repo add git@github.com:you/your-project.git     # or a local path
```

This creates a **bare mirror** at `repos/<name>.git` and records it in `config/repo.json`.
Agent worktrees are cut from the mirror at `wt/<id>`, on branches `agent/<id>`, with a sparse
checkout that excludes the repo's own `.claude/` so its hooks and settings never fight hx.

Your checkout is not touched. Your remote sees nothing until you tell the Partner to push, and
`hx push <id>` is the only command in the whole system that reaches it.

If your repo's `.claude/` carries skills the agents genuinely need, set `keep_claude_dir: true`
in `config/repo.json`.

## 5. Enable the boot and heartbeat units

`hx install` writes these for you, from the templates in `packaging/`, with your instance path
and the recorded hx binary substituted in.

**macOS** (`~/Library/LaunchAgents/`):

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hx.up.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hx.heartbeat.plist
```

**Linux** (`~/.config/systemd/user/`):

```bash
systemctl --user daemon-reload
systemctl --user enable --now hx-up.service
systemctl --user enable --now hx-heartbeat.timer
loginctl enable-linger "$USER"      # so the fleet starts without an interactive login
```

`hx up` launches every configured agent at boot. `hx heartbeat` runs every 900 seconds: it
reads the board, restarts any `working` item whose tmux session has died, and wakes the Partner
when something changed. That clock lives outside Claude Code on purpose — a timer inside a
session would be cleared by `/clear`, and every seam is a `/clear`.

## 6. Talk to the Partner

`hx install` finishes with `hx launch partner` and prints:

```bash
tmux attach -t partner
```

That is the interface. Tell it what you want built, in your own words. It writes its own order
file from the conversation, dispatches itself, decomposes the work into order files for
workers, launches and dispatches them, reads their digests as they finish, and reports back to
you in chat.

**You do not run hx commands.** If the Partner ever tells you to run one, tell it that is its
job — that instruction is in its persona and its skill.

Detach with `Ctrl-b d`. The fleet keeps working.

## Watching without attaching

```bash
hx ui
```

serves an observing UI on `127.0.0.1` (port from `config/ui.json`, default 8765), with a
bearer token in `run/ui-token`. It shows the board, each agent's work item and step state, the
Partner's memory and a chat box, the orders and their dependency graph, and the archive. It
observes; it does not operate. Full control — slash commands, interrupts — is
`tmux attach -t partner`.

## Upgrading

```bash
uv tool upgrade hx
hx upgrade
```

`hx upgrade` re-renders every agent home's settings and skills from the new package. If the
`claude` version changed, it runs the live suite against it in a scratch instance first and
refuses to pin a version that suite has not passed on; your existing pin stays in place and it
tells you why. Sessions restart one id at a time, at a boundary.

## What hx never touches

Your `~/.claude`, your checkouts, and any remote. See [two-worlds.md](two-worlds.md) for the
line-by-line version.

## If something is wrong

```bash
hx doctor        # tmux, git, the pinned binary and its version, seed credentials,
                 # every agent home's settings, mirror reachability
hx board         # one line per id, then invariant violations; exits 1 on any error
```

Both are safe to run yourself and neither changes anything. A board error is normally the
Partner's to fix (`hx restart <id>`), and the heartbeat usually gets there first.
