---
description: Install hx and bring up a working Partner, step by step.
---

# /hx:setup — install hx

You are setting hx up on this machine. Do the steps in order; each one says how to check
it worked. After setup the human only ever talks to the Partner — you do the one-time
machine setup below and then stop. You do not create workers and you do not dispatch work.

## Two rules before you type anything

**Never touch the human's own Claude.** hx reads nothing from `~/.claude` and writes
nothing there — every agent has its own home under `<root>/run/<id>/home`. Hold the same
line: do not edit `~/.claude/settings.json`, credentials, or skills, and never put the
instance root inside `~/.claude` (`hx install` refuses it).

**The token step is the human's.** `hx install` stops once and asks for a long-lived token
from `claude setup-token`. You never run it for them, never read, print, or paste the
token, and never write `seed/token` yourself. Relay the two lines `hx install` prints,
verbatim, and wait.

## 1. Prerequisites

Check each one and report what is missing before installing anything:

```bash
tmux -V                    # any recent tmux
git --version
uv --version               # uv installs the package and provides Python 3.14
uv python find 3.14        # or: python3.14 --version
claude --version           # must be a version hx has been tested with
```

The tested Claude Code versions are listed in `src/hx/packaging/tested-claude-versions.json`
in the repository. If the installed `claude` is another version, say so; `hx install` will
refuse to pin it, and you do not work around that. The human needs a Claude subscription
(Max or Pro). No API key is used anywhere.

## 2. Install the hx CLI

```bash
uv tool install git+https://github.com/logan-robbins/hx
```

Check it:

```bash
hx doctor
```

## 3. Create the instance

Pick a directory for the instance. It must not be inside `~/.claude`.

```bash
hx install --root ~/hx
```

The first run stops with exit 4 and prints two steps. Relay them verbatim and wait: the
human runs `claude setup-token` (a browser consent on their subscription) and pastes the
token into the file hx named. Then run the install again:

```bash
hx install --root ~/hx
```

It records the Claude binary and version, lays out the instance, launches the Partner in
tmux session `partner`, and starts the UI at `http://127.0.0.1:8765/`.

## 4. Verify

```bash
hx board          # the Partner, idle, in its tmux session
tmux ls           # sessions: partner, ui
```

Done. Tell the human to attach with `tmux attach -t partner` and talk to the Partner.
Point them at `docs/operating.md` in the repository for day-to-day use.
