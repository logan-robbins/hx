# Deploying hx

This is everything a human does. It happens once. After it, you talk to the Partner and
nothing else.

## What you are setting up

`hx` is a control plane for a fleet of Claude Code sessions. Each one — including the
supervising **Partner** — is a full Claude Code instance in its own tmux session, running under
a `/goal`, with its own config directory, its own worktree, and a small **Companion** model
that keeps its continuity across context boundaries.

Two things exist and never mix:

- The **package** `hx`: Python 3.14, zero runtime dependencies, published as `hx-harness`.
- The **instance** `$HARNESS_ROOT`: your data. Created by `hx install`. `/srv/hx` on a server,
  `~/hx` on a workstation.

Upgrading the package never writes into your instance except through `hx upgrade`.

## Before you start

- **A non-root user.** hx refuses to run as root, and Claude Code refuses
  `--dangerously-skip-permissions` under root or sudo. Every agent here runs with that flag.
- **`tmux`** and **`git`** on `PATH`.
- **Python 3.14 or newer.**
- **The `claude` binary**, at a version in the package's tested list, which ships inside the
  wheel as `hx/packaging/tested-claude-versions.json`. `hx install` checks this and stops with
  the version to install if yours is not listed.
- **A Claude account.** The harness authenticates with a long-lived token of its own that you
  generate in one command — see step 3. Your own login is never read.

## 1. Install the package

```bash
uv tool install hx-harness
```

The distribution is named `hx-harness`; the command, the Python package, and the repository are
all `hx`. `pipx install hx-harness` works too. It installs two entry points, `hx` and
`hx-hook`, into your uv tool bin directory, and nothing at all into your `~/.claude`.

## 2. Create the instance

```bash
export HARNESS_ROOT=~/hx        # or /srv/hx on a server
hx install --repo git@github.com:you/your-project.git
```

That is the whole of it. `hx install` runs six numbered steps and prints each one; it is
idempotent, so the run below is what happens the first time and re-running it picks up where it
stopped. Everything that follows on this page is output from a real run of
`packaging/e2e-deploy.sh`, not an illustration.

It refuses to run as root, checks `tmux`, `git` and Python, and pins the `claude` binary:

```
1. claude 2.1.278 at /opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
2. instance at ~/hx
   created  PARTNER.md
   created  adapters/claude/install.sh
   created  adapters/claude/start.sh
   created  companion/BASE.md
   created  companion/roles/engineer.md
   created  companion/roles/partner.md
   created  companion/roles/reviewer.md
   created  config/CLAUDE.md
   created  config/models.json
   created  config/partner/AGENTS.md
   created  config/partner/SUBAGENTS.md
   created  config/partner/harness.json
   created  templates/addendum.md
   created  templates/order.md
   created  templates/work-item.md
   created  templates/worker/AGENTS.md
   created  templates/worker/SUBAGENTS.md
   created  templates/worker/harness.json
   created  .gitignore
   created  config/hx.json
   created  config/claude.json
```

If your `claude` is not a version this package has been tested against, it stops here and says
which one to install. `partner` is the only agent a fresh instance has; `templates/worker/` is
what the Partner copies to create another, and you never do that yourself.

## 3. Seed the token

Then it stops, with **exit 4**, and asks for the one thing it cannot do for you:

```
hx needs one long-lived token for this instance. Two steps, both yours:

  1. claude setup-token
  2. paste the token it prints into ~/hx/seed/token

Then run this command again. hx reads nothing from your own ~/.claude, on any
platform: this token is the whole of an agent's auth (spec 11 Auth).
```

Do exactly that — `claude setup-token` in your own Claude, paste the line it prints into
`$HARNESS_ROOT/seed/token` — and run the same `hx install` command again. It sets the file to
mode 0600 and carries on. `start.sh` then reads that file in its own process and exports it as
`CLAUDE_CODE_OAUTH_TOKEN` for each session it launches: never as an argument to anything, so it
cannot appear in `ps`, and never written anywhere under `run/`.

Agent homes hold no credentials file at all. Revoking the harness's access later is one token,
in one place, with nothing of yours entangled in it.

## 4. The rest of the install

The second run picks up at step 3 and finishes:

```
3. seed token at ~/hx/seed/token, mode 0600
4. mirrored product from git@github.com:you/your-project.git (main)
5. units written to ~/Library/LaunchAgents
   created  com.hx.up.plist
   created  com.hx.heartbeat.plist
   hx does not enable them. To start them at login, run:
     launchctl bootstrap gui/$(id -u) "~/Library/LaunchAgents/com.hx.up.plist"
     launchctl bootstrap gui/$(id -u) "~/Library/LaunchAgents/com.hx.heartbeat.plist"
6. partner started

tmux attach -t partner
```

**Step 4, the mirror.** `repos/<name>.git` is a bare mirror fetched from your upstream. Agent
worktrees are cut from it at `wt/<id>`, on their own branches, with a sparse checkout that
excludes the repo's own `.claude/` so its hooks and settings never fight hx. Your checkout is
not touched — no worktree is added to it, no remote of yours is contacted. Your remote sees
nothing until you tell the Partner to push, and `hx push <id>` is the only command in the whole
system that reaches it.

If your repo's `.claude/` carries skills the agents genuinely need, set `keep_claude_dir: true`
in `config/repo.json`.

**Step 5, the units.** hx writes them and deliberately does not enable them — starting a fleet
at every login is your decision, not the installer's. Run the two commands it printed:

```bash
# macOS
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hx.up.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hx.heartbeat.plist
```

```bash
# Linux — the units land in ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hx-up.service
systemctl --user enable --now hx-heartbeat.timer
loginctl enable-linger "$USER"      # so the fleet starts without an interactive login
```

Enable the **timer**, not `hx-heartbeat.service`; the service carries no `[Install]` section on
purpose. `hx up` launches every configured agent at boot. `hx heartbeat` runs every 900 seconds:
it reads the board, restarts any `working` item whose tmux session has died, and wakes the
Partner when something changed. That clock lives outside Claude Code on purpose — a timer
inside a session would be cleared by `/clear`, and every seam is a `/clear`.

## 5. Talk to the Partner

Step 6 started the Partner and printed:

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

Two separate things, in this order.

**The hx package:**

```bash
uv tool upgrade hx-harness
```

**The pinned `claude` binary:**

```bash
hx upgrade                       # re-probe whatever `claude` is on PATH
hx upgrade --claude /path/to/claude   # or pin a specific one
```

`hx upgrade` moves the pin in `config/claude.json` **only** to a version this package has been
tested against, and it checks before it writes anything, so a refused upgrade leaves your
working pin exactly as it was. Real output of a refusal:

```
$ hx upgrade --claude ./claude-9.9.9
hx: claude 9.9.9 is not in this package's tested list (2.1.278). Install 2.1.278 and run this
again, or upgrade hx to a package that has been tested on 9.9.9 (spec 17.2 step 1, 17.6)
```

and of one that is accepted:

```
$ hx upgrade --claude ./claude-2.1.278
HX-UPGRADE unchanged 2.1.278
```

It prints `HX-UPGRADE <old> -> <new>` when the version actually moves.

**It does not restart anything.** Running sessions keep the binary they launched with until
they are restarted, which is deliberate: a restart mid-turn throws that turn away. Ask the
Partner to `hx restart <id>` one id at a time, at a boundary, once you are satisfied with the
new version.

## What hx never touches

Your `~/.claude`, your checkouts, and any remote. See [two-worlds.md](two-worlds.md) for the
line-by-line version.

## If something is wrong

```bash
hx doctor        # tmux, git, the pinned binary and its version, the seed token and its mode,
                 # every agent home's settings, mirror reachability
hx board         # one line per id, then invariant violations; exits 1 on any error
```

Both are safe to run yourself and neither changes anything. A board error is normally the
Partner's to fix (`hx restart <id>`), and the heartbeat usually gets there first.

Real `hx doctor` output from a finished install — every line `ok`, exit 0:

```
$ hx doctor
ok    python        3.14.7 (~/.local/share/uv/tools/hx-harness/bin/python)
ok    tmux          tmux 3.7c
ok    git           git version 2.50.1 (Apple Git-155)
ok    root          ~/hx
ok    claude        /opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe pinned at 2.1.278
ok    skeleton      PARTNER.md
ok    skeleton      adapters/claude/install.sh
ok    skeleton      adapters/claude/start.sh
ok    skeleton      companion/BASE.md
ok    skeleton      companion/roles/partner.md
ok    skeleton      config/CLAUDE.md
ok    skeleton      config/models.json
ok    skeleton      config/partner/AGENTS.md
ok    skeleton      config/partner/SUBAGENTS.md
ok    skeleton      config/partner/harness.json
ok    skeleton      templates/work-item.md
ok    models        2 model(s): claude-opus-5, claude-sonnet-5
ok    hx.json       hx_bin ~/.local/share/uv/tools/hx-harness/bin/hx
ok    hx.json       hook_bin ~/.local/share/uv/tools/hx-harness/bin/hx-hook
ok    hx.json       python_bin ~/.local/share/uv/tools/hx-harness/bin/python
ok    token         seed/token present, mode 0600
ok    home:partner  settings.json
ok    repo          product at aa1be46651e5
```

Before you have pasted the token, the `token` line is a `warn` naming
`claude setup-token`, and `hx install` itself stops with exit 4 rather than leaving you to
find that out from `doctor`. Every `warn` names the step that clears it, and `doctor` fails
(exit 1) only on things that are actually broken: a missing `tmux` or `git`, a Python below
3.14, or a pinned `claude` that is not executable.

(Absolute paths above are from a real run; yours will be under your own `$HARNESS_ROOT` and uv
tool directory.)
