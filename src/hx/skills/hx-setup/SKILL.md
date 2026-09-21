---
name: hx-setup
description: How a coding agent or another harness installs hx on a machine and brings up an instance — prerequisites, the wheel install, `hx install` and its stop for the seed token (the human's own step), verification, the UI, handing over to the Partner, keeping it running, upgrading, and what never to do. Use when asked to set up, install, deploy, verify, upgrade or explain how to set up hx.
---

# hx-setup

You are a coding agent (Claude Code, another assistant, or a script in some other harness)
asked to set hx up on this machine. This skill is the whole procedure. Read it once, then do
the steps in order; each one says how to check it worked.

## What you are setting up, in one paragraph

hx runs a small fleet of Claude Code sessions in tmux: one **Partner** that the human talks
to, N **workers** the Partner creates and dispatches, and a **Companion** beside every one of
them that keeps its state so it survives context cuts. Everything lives under one directory,
the instance root (`HARNESS_ROOT`, default `~/hx`), with one credential in `seed/token`. After
setup **the human only ever talks to the Partner** (`tmux attach -t partner`) and watches the
UI. You do the one-time machine setup below and then stop. You are not the Partner, you do
not create workers, and you do not dispatch work.

## Two rules before you type anything

**Never touch the human's own Claude.** hx reads nothing from `~/.claude` and writes nothing
there, on any platform — every agent has its own home under `<root>/run/<id>/home`. You hold
to the same line: do not edit `~/.claude/settings.json`, `~/.claude/skills`, credentials or
`.claude.json`, and never put the instance root inside `~/.claude` (`hx install` refuses it).

**The token step is the human's.** `hx install` stops once and asks for a long-lived token
from `claude setup-token`. That command opens a browser for consent on their subscription. You
never run it for them, never read, print or paste the token, and never write `seed/token`
yourself. You relay the two lines `hx install` prints, verbatim, and wait.

## 1. Prerequisites

Check each one and report what is missing before installing anything:

```bash
tmux -V                       # any recent tmux
git --version
uv --version                  # uv installs the package and provides Python 3.14
uv python find 3.14           # or: python3.14 --version
claude --version              # must be a version hx has been tested with
```

The tested Claude Code versions are in the repository at
`src/hx/packaging/tested-claude-versions.json` (also inside the installed package). If the
installed `claude` is another version, say so; `hx install` will refuse to pin it, and you do
not work around that. The human needs a Claude subscription (Max or Pro). **No API key is
used anywhere**; the Companion runs on the same subscription token as the agents.

## 2. Install the package

From a checkout of the repository:

```bash
cd <checkout> && uv build && uv tool install --python 3.14 dist/hx_harness-0.1.0-py3-none-any.whl
```

(Once published: `uv tool install --python 3.14 hx-harness`.) Check:

```bash
hx --help
hx doctor
```

`hx doctor` with no instance yet reports what is on the machine and what is missing. Its
lines are `ok`, `warn` or `fail`, each naming the step that clears it.

## 3. Create the instance

Choose the root with the human: `--root <dir>`, else `$HARNESS_ROOT`, else `~/hx`. Any
directory outside `~/.claude`; it need not exist.

```bash
hx install --root ~/hx
```

**The first run stops with exit code 4** and prints:

```
hx needs one long-lived token for this instance. Two steps, both yours:

  1. claude setup-token
  2. paste the token it prints into ~/hx/seed/token

Then run this command again.
```

Stop here. Show the human those two steps exactly. When they say the file is in place you may
check it without reading it:

```bash
test -s ~/hx/seed/token && chmod 600 ~/hx/seed/token && echo token-present
```

Then run the install again:

```bash
hx install --root ~/hx
```

This time it pins the `claude` binary and version it found, lays out the instance (`config/`,
`templates/`, `personas/`, `companion/`, `pods/`, `run/`, `state/`, `logs/`, `seed/`), launches
the Partner in tmux session `partner`, and starts the UI in tmux session `ui`. It is
idempotent: running it again re-copies the package skeleton without touching anything written
under `config/<id>/`, `PARTNER.md` or the token.

Non-interactive callers (another harness) get the same contract: exit 4 means "seed token
missing"; write the token file by your own means, then call again. Nothing else is asked.

## 4. Verify, do not assume

```bash
HARNESS_ROOT=~/hx hx doctor
```

Every line should be `ok`. The ones that matter most:

- `home:partner  settings.json` — the Partner's own Claude home was written by hx.
- `sandbox:partner  IS_SANDBOX=1 on the tmux session` and
  `sandbox:partner  --dangerously-skip-permissions in the pane's argv` — every agent runs
  bypass-permissions with `IS_SANDBOX=1`, always. A `fail` here a moment after launch can be
  the launcher not yet having exec'd `claude`; wait a few seconds and look again. One that
  persists is real.
- A `models` row saying the shipped `config/models.json` validates.

Then:

```bash
tmux ls                                   # sessions: partner, ui
HARNESS_ROOT=~/hx hx board                # an empty fleet prints only its header; that is right
HARNESS_ROOT=~/hx hx show partner         # PARTNER.md, the pane, its Companion
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/   # 200
```

If the human wants proof their own Claude was not touched, hash `~/.claude` before and after
with the repository's `tools/claude-home-hash.sh ~/.claude`; the two manifests are identical.

## 5. Models and cost

`config/<id>/harness.json` carries each agent's `model` and `effort`; `config/models.json`
carries each model's context `window`, `autocompact_window` and seam `threshold`. The shipped
defaults are `claude-opus-5` at `xhigh` for the Partner and for every worker the Partner
creates from `templates/worker/harness.json`. Models are always full ids, never aliases.

For a trial or test instance, set both files to `claude-sonnet-5` and `medium` **before the
second `hx install` run** (or restart the Partner afterwards):

```bash
sed -i.bak 's/"claude-opus-5"/"claude-sonnet-5"/; s/"xhigh"/"medium"/' \
  ~/hx/config/partner/harness.json ~/hx/templates/worker/harness.json && rm -f ~/hx/config/partner/harness.json.bak ~/hx/templates/worker/harness.json.bak
```

The Companion's model is `companion.model` in the same file when set, else the agent's. The
harness is what a trial exercises, not the model's judgement; Sonnet at medium is enough.

## 6. Hand over

Tell the human two things and nothing else:

```bash
tmux attach -t partner
```

and the UI at `http://127.0.0.1:8765/` (port from `config/ui.json`). From here on the Partner
creates workers, writes goals, dispatches, reads completion states and reports in chat; the human
describes what they want and where the code is. **The human never runs hx**, and neither do
you after this point. Do not create `config/<id>/` directories, do not write goal files, do
not run `hx launch` or `hx dispatch`: an agent that starts making agents is a bug, and the
Partner has the skills (`hx-partner`, `hx-fleet`, `hx-memory`) for all of it.

What the UI shows, so you can describe it: the fleet graph with the Partner at the root and
each worker with its Companion beside it (pulsing while a pass runs); a worker's drawer, which
is its work item file plus its Companion's status; a Session page and a Compaction page that
open in their own window; Task board, Harness Agents, Activity, Goals, Archive, and a chat box
that reaches the Partner exactly as `hx wake partner` does.

## 7. Keeping it running

hx ships no daemons, timers or launchd/systemd units. After a reboot:

```bash
HARNESS_ROOT=~/hx hx up        # hx launch for every config/<id>/, Partner first, then the UI
```

If the human wants unattended recovery, they put `hx heartbeat` in their own cron; it restarts
dead sessions and wakes the Partner when the board moved. Offer it; do not install it for them.

## 8. Upgrading

```bash
uv tool upgrade hx-harness          # or reinstall the new wheel
HARNESS_ROOT=~/hx hx install --root ~/hx
```

Running sessions keep the code and skills they launched with until restarted, deliberately:
a restart mid-turn throws that turn away. The Partner restarts its workers (`hx restart <id>`)
when it is ready; the UI is restarted by ending tmux session `ui` and running `hx up`.

## 9. tmux, precisely

Target sessions by exact name: `tmux kill-session -t '=ui'`. A bare `-t ui` prefix-matches
any session starting with `ui`, and the `=` must be quoted in zsh, which otherwise expands
`=word` as a command lookup. Never send keys into an agent's pane: the Partner is spoken to
through `tmux attach` by the human or `hx wake partner` by the UI, and workers only through
`hx dispatch`, `hx resume` and `hx restart` run by the Partner.

## Never

- Run `claude setup-token`, or read, print, paste or write the token. Relay the two steps.
- Put the instance root inside `~/.claude`, or edit anything in `~/.claude`.
- Use `claude -p`. Every hx participant is an interactive tmux session driven by hooks.
- Add timeouts, kill a session mid-turn, or answer a trust or onboarding prompt by hand — hx
  pre-seeds each home so no such prompt appears; one that does is a bug to report.
- Create workers, write goals, dispatch, or edit a worker's `workdir`. That is the Partner's.
- Tell the human to run an hx command after hand-over. They talk to the Partner.
