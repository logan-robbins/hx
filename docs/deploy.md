# Deploying hx

This is everything a human does. It happens once. After it, you talk to the Partner and
nothing else.

## What you are setting up

`hx` is a control plane for a fleet of Claude Code sessions. Each one — including the
supervising **Partner** — is a full Claude Code instance in its own tmux session, running under
a `/goal`, with its own config directory and its own working directory.

Each is also paired with a small **Companion**: another Claude Code session, in the next tmux
window, on the same pinned binary and the same instance token — no API key needed — in a config
home of its own with no product skills and no CLAUDE.md, and with **two tools, Read and
Write**. hx wakes it with `/clear` and one pointer to a pass file; it reads the agent's
tool-call stream and writes one JSON object, the bounded step state that lets the agent's
conversation be cut and rebuilt without losing what it knew. It cannot run a command, so the
thing watching every agent is the one participant here that cannot act.

Two things exist and never mix:

- The **package** `hx`: Python 3.14, zero runtime dependencies, published as `hx-harness`.
- The **instance** `$HARNESS_ROOT`: your data. Created by `hx install`. `/srv/hx` on a server,
  `~/hx` on a workstation.

Upgrading the package never writes into your instance; `hx install` is idempotent and is
what re-copies the skeleton.

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
hx install
```

That is the whole of it. `hx install` runs four numbered steps and prints each one; it is
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
   created  companion/roles/backend-engineer.md
   created  companion/roles/frontend-engineer.md
   created  companion/roles/partner.md
   created  companion/roles/release-engineer.md
   created  config/CLAUDE.md
   created  config/models.json
   created  config/partner/AGENTS.md
   created  config/partner/SUBAGENTS.md
   created  config/partner/harness.json
   created  personas/backend-engineer/AGENTS.md
   created  personas/frontend-engineer/AGENTS.md
   created  personas/partner/AGENTS.md
   created  personas/release-engineer/AGENTS.md
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
what the Partner copies to create another, and `personas/<role>/AGENTS.md` are the four
personas it copies over `config/<id>/AGENTS.md` when it does. You never do either yourself —
the Partner has a skill for it.

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

The second run picks up at step 3 and finishes. Step 2 runs again and prints `kept` for
everything already there — that is what idempotent looks like:

```
3. seed token at ~/hx/seed/token, mode 0600
4. partner started

tmux attach -t partner
```

**Keeping it running is yours.** hx ships no launchd plist and no systemd unit. `hx up`
(launch everything) and `hx heartbeat` (restart dead sessions, wake the Partner when the board
moved) are ordinary commands; put them in your own cron if you want them at boot or on a
timer:

```cron
@reboot      hx up
*/15 * * * * hx heartbeat
```

**Working directories are not hx's business.** Each worker's `workdir` is whatever absolute
directory the Partner puts in `config/<id>/harness.json` — a fresh directory it creates, or a
checkout that already exists. hx creates no repository and no branch, and pushes nothing
anywhere. The only git it ever runs is `git status --porcelain` inside `hx complete done`, and
only when that directory is a git repository.

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

```bash
uv tool upgrade hx-harness
hx install --root "$HARNESS_ROOT"
```

`hx install` is idempotent: re-running it re-copies the package skeleton over the instance
without touching anything you or the Partner have written under `config/<id>/`, and re-checks
that your `claude` is a version this package has been tested against.

**It does not restart anything.** Running sessions keep the binary and the skills they launched
with until they are restarted, which is deliberate: a restart mid-turn throws that turn away.
Ask the Partner to `hx restart <id>` one id at a time, at a boundary, once you are satisfied.

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
ok    python           3.14.7 (~/.local/share/uv/tools/hx-harness/bin/python)
ok    tmux             tmux 3.7c
ok    git              git version 2.50.1 (Apple Git-155)
ok    root             ~/hx
ok    claude           /opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe pinned at 2.1.278
ok    skeleton         PARTNER.md
ok    skeleton         adapters/claude/install.sh
ok    skeleton         adapters/claude/start.sh
ok    skeleton         companion/BASE.md
ok    skeleton         companion/roles/partner.md
ok    skeleton         config/CLAUDE.md
ok    skeleton         config/models.json
ok    skeleton         config/partner/AGENTS.md
ok    skeleton         config/partner/SUBAGENTS.md
ok    skeleton         config/partner/harness.json
ok    skeleton         personas/partner/AGENTS.md
ok    skeleton         templates/work-item.md
ok    models           2 model(s): claude-opus-5 window=1000000 seam=200000 autocompact=250000, claude-sonnet-5 window=1000000 seam=200000 autocompact=250000
ok    hx.json          hx_bin ~/.local/share/uv/tools/hx-harness/bin/hx
ok    hx.json          hook_bin ~/.local/share/uv/tools/hx-harness/bin/hx-hook
ok    hx.json          python_bin ~/.local/share/uv/tools/hx-harness/bin/python
ok    bin              bin/hx -> ~/.local/share/uv/tools/hx-harness/bin/hx
ok    bin              bin/hx-hook -> ~/.local/share/uv/tools/hx-harness/bin/hx-hook
ok    token            seed/token present, mode 0600
ok    home:partner     settings.json
ok    home:partner     .claude.json (onboarding done, 1 trusted cwd)
ok    sandbox:partner  IS_SANDBOX=1 on the tmux session
ok    sandbox:partner  --dangerously-skip-permissions in the pane's argv
```

The two `sandbox:` lines are per live agent, and they are the ones worth reading: they check
the *running* session rather than the configuration that was meant to produce it. If an agent
was launched some other way, or a relaunch lost a flag, that is where it shows.

Before you have pasted the token, the `token` line is a `warn` naming
`claude setup-token`, and `hx install` itself stops with exit 4 rather than leaving you to
find that out from `doctor`. Every `warn` names the step that clears it, and `doctor` fails
(exit 1) only on things that are actually broken: a missing `tmux` or `git`, a Python below
3.14, or a pinned `claude` that is not executable.

(Absolute paths above are from a real run; yours will be under your own `$HARNESS_ROOT` and uv
tool directory.)
