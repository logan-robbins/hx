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

**OAuth stays the default.** An existing `seed/token` credential wins. If the user supplies
an env file, `hx` fills missing provider credentials from it at startup. Never print a
credential. If neither source is present, relay the `claude setup-token` steps that
`hx install` prints and wait for the human to complete them.

## 1. Prerequisites

Check each one and report what is missing before installing anything:

```bash
tmux -V                    # any recent tmux
git --version
uv --version               # uv installs the package and provides Python 3.14
uv python find 3.14        # or: python3.14 --version
claude --version           # must be a version hx has been tested with
```

The tested Claude Code versions are listed in `src/hx/packaging/tested-claude-versions.json`.
Use `--ignore-claude-version` only when the human has asked to bypass this check. Auth may
come from an OAuth token or an Anthropic API key in a supplied env file.

## 2. Install the hx CLI

If the user supplied a local `hx/` checkout, build and install that checkout:

```bash
cd <local-hx-checkout> && uv build --wheel && uv tool install --force --python 3.14 dist/hx_harness-0.1.0-py3-none-any.whl
```

Otherwise use `uv tool install git+https://github.com/logan-robbins/hx`.

Check it:

```bash
hx doctor
```

## 3. Create the instance

Pick a directory for the instance. It must not be inside `~/.claude`. If the requested
project already has an hx instance, reuse it and run `hx up` with `HARNESS_ROOT` set to
that path.

```bash
hx install --root ~/hx
```

When the user supplies a dotenv file, add `--env-file /absolute/path/to/.env`. It is saved
in the instance. Existing OAuth and provider seed tokens take precedence. The file can
contain `ANTHROPIC_API_KEY` (or `ANTRHOPIC_API_KEY`), `OPENAI_API_KEY`, and `GROK_API_KEY`
(or `XAI_API_KEY`), plus other environment variables agents need. Use
`--ignore-claude-version` if the human authorized it.

Without an OAuth token or a usable env fallback, the first run stops with exit 4 and
prints two steps. Relay them verbatim and wait: the human runs `claude setup-token`
(a browser consent on their subscription) and pastes the token into the file hx named.
Then run the install again:

```bash
hx install --root ~/hx
```

It records the Claude binary and version, lays out the instance, launches the Partner in
tmux session `partner`, and starts the UI at `http://127.0.0.1:8765/`.

## 4. Verify

```bash
HARNESS_ROOT=<instance> hx doctor
tmux list-windows -t '=partner'   # main and companion
tmux ls                            # sessions: partner, ui
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/   # 200
```

Done. Tell the human to attach with `tmux attach -t partner` and talk to the Partner.
Point them at `docs/operating.md` in the repository for day-to-day use.
