---
name: hx-setup
description: How a coding agent or another harness installs hx on a machine and brings up an instance — prerequisites, the wheel install, `hx install` and its stop for the seed token (the human's own step), verification, the UI, handing over to the Partner, keeping it running, and what never to do. Use when asked to set up, install, deploy, verify or explain how to set up hx.
metadata:
  hx-audience: operator
  hx-roles: none
  hx-loads-when: installing hx on a machine and bringing up the Partner; never installed into a home
---

# hx-setup

You are a coding agent (Claude Code, another assistant, or a script in some other harness)
asked to set hx up on this machine. This skill is the whole procedure. Read it once, then do
the steps in order; each one says how to check it worked.

## What you are setting up, in one paragraph

hx runs a small fleet of Claude Code sessions in tmux: one **Partner** that the human talks
to, N **workers** the Partner configures and assigns, and a **Companion** beside every one of
them that keeps its state so it survives context cuts. Everything lives under one directory,
the instance root (`HARNESS_ROOT`, default `~/hx`), with its Claude credential in `seed/token`
and optional provider keys under `seed/`. After
setup **the human only ever talks to the Partner** (`tmux attach -t partner`) and watches the
UI. You do the one-time machine setup below and then stop. You are not the Partner, you do
not create workers, and you do not assign work.

## Two rules before you type anything

**Never touch the human's own Claude.** hx reads nothing from `~/.claude` and writes nothing
there, on any platform — every agent has its own home under `<root>/run/<id>/home`. You hold
to the same line: do not edit `~/.claude/settings.json`, `~/.claude/skills`, credentials or
`.claude.json`, and never put the instance root inside `~/.claude` (`hx install` refuses it).
An explicit request to install the Claude plugin may use `claude plugin`; that operation is
separate from hx's runtime and does not authorize changing personal credentials or settings.

**The OAuth token step is the human's.** Without an existing token or a supplied env file,
`hx install` stops and asks for a long-lived token from `claude setup-token`. You never run it for them,
never read or paste the OAuth token, and never write it yourself.
An explicitly supplied env file is the authorized fallback; hx imports missing provider
keys from it without printing their values.

## 1. Prerequisites

Check each one and report what is missing before installing anything:

```bash
tmux -V                       # any recent tmux
git --version
uv --version                  # uv installs the package and provides Python 3.14
uv python find 3.14           # or: python3.14 --version
command -v claude             # Claude Code on PATH
```

hx records the path of the `claude` it finds (`--claude <path>` names another) and launches
every Claude agent with it.
The default auth path is the instance's OAuth token. If the human supplies `--env-file`,
missing credentials fall back to that file: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `XAI_API_KEY`. Existing
credentials under `seed/` take precedence.

Optional, for the code graph: `universal-ctags` (the `ctags` that ships with macOS is BSD
ctags and is refused), the `tree-sitter` CLI with grammar checkouts named in
`HX_TREE_SITTER_GRAMMARS`, and SCIP indexers with the `scip` CLI. None is required: hx's own
extractors cover Python, C#, TypeScript/JavaScript, Go, Rust and Software AG Natural, and
`hx code stats` reports which optional tools were detected. Nothing is vendored.

## 2. Install the package

From a checkout of the repository:

```bash
cd <checkout> && uv build --wheel && uv tool install --force --python 3.14 dist/hx_harness-1.0.0-py3-none-any.whl
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
If the requested project already has an hx instance, reuse it. Run `hx up` with
`HARNESS_ROOT` set to that instance, then verify the Partner and UI. Do not create a second
instance merely because the default path differs.

```bash
hx install --root ~/hx
```

For a supplied dotenv fallback:

```bash
hx install --root ~/hx --env-file /absolute/path/to/.env
```

The env path is stored in the instance; later launches read it again.

Without an existing OAuth token or a usable `--env-file`, the first run stops with exit
code 4 and prints:

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

This time it records the path of the `claude` binary it found (`config/claude.json`), lays
out the instance (`config/`, `templates/`, `personas/`, `companion/`, `pods/`, `run/`,
`state/`, `logs/`, `seed/`), launches the Partner in tmux session `partner`, and starts the UI
in tmux session `ui`. It is
idempotent: running it again adds missing skeleton files and keeps existing files, including
adapters, `config/<id>/`, `PARTNER.md`, and credentials.

Non-interactive callers (another harness) get the same contract: exit code 4 means "seed token
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
- A `skills` row: every packaged skill is well-formed, and which optional ones (`config/hx.json`
  `"skills"`) are on. A `skills-isolation` warning names a checkout or a `~/.agents/skills`
  whose skills could load into a worker; report it, delete nothing.

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
defaults are `claude-opus-5-5` at `xhigh` for the Partner and for every worker the Partner
creates from `templates/worker/harness.json`. Models are always full ids, never aliases.

For a trial or test instance, set both files to `claude-sonnet-5-5` and `medium` **before the
second `hx install` run** before starting the Partner:

```bash
sed -i.bak 's/"claude-opus-5-5"/"claude-sonnet-5-5"/; s/"xhigh"/"medium"/' \
  ~/hx/config/partner/harness.json ~/hx/templates/worker/harness.json && rm -f ~/hx/config/partner/harness.json.bak ~/hx/templates/worker/harness.json.bak
```

The Companion's model is `companion.model` in the same file when set, else the agent's. The
harness is what a trial exercises, not the model's judgement; Sonnet at medium is enough.

## 6. Hand over

Give the human the Partner attachment command and the live UI URL:

```bash
tmux attach -t partner
```

and the UI at the address reported by `hx up` (`config/ui.json` chooses the port).
From here the Partner explores graph dependencies and searches/reuses existing capabilities
for every directive, drafts dependency packets, schedules maximum useful parallel work, reserves
ready runs, explicitly launches them, and reports verified outcomes. The human describes
intent and unresolved choices; the setup operator does not create worker assignments.
The Partner uses `hx-partner` and `hx-fleet`; workers use `hx-worker`.

The UI projects dependency packets, native execution/leases, progress, approved bindings,
checks/checkpoints, and delivery candidates from the ledger. Its Partner chat sends the
explicitly entered message; it does not create a task or override a proof gate.

## 7. Keeping it running

`HARNESS_ROOT=~/hx hx up` recovers the Partner, UI, and resident root service. `hx heartbeat`
advances durable existing native launch/recovery intents; it does not launch unassigned workers
or start reservations without explicit launch. Schedule recovery only if requested.

The code graph is derived state under `<root>/state/code-index/`, never in a checkout. Each
worker completion refreshes the paths it changed; the one full rebuild is the installed hook,
which the human wires into their repository's post-merge hook or job:

```bash
HARNESS_ROOT=~/hx HX_BIN="$(command -v hx)" HX_CODE_REPO=/path/to/repo ~/hx/bin/code-index-post-merge
```

`HX_CODE_REPO` defaults to the repository the hook runs in; `HX_CODE_INDEXER_CHECKOUT` names a
separate clean checkout to opt into the resolved (SCIP) lane when an indexer is installed.
Offer the hook and the line above; do not edit their repository's hooks yourself.

## 8. tmux, precisely

Target sessions by exact name: `tmux kill-session -t '=ui'`. A bare `-t ui` prefix-matches
any session starting with `ui`, and the `=` must be quoted in zsh, which otherwise expands
`=word` as a command lookup. Never send keys into an agent's pane: the Partner is spoken to
through `tmux attach` by the human or `hx wake partner` by the UI, and workers only through
`hx plan assign`, explicit `hx launch ID --run RUN --request REQUEST`, and accepted plan revisions run by the Partner.

## 9. Several instances on one host

A programme split by ownership runs one instance per area, each with its own root, its own
tmux server (`HX_TMUX="tmux -L hx-<area>"`), its own UI port and its own `seed/token`:

```bash
hx install --root /path/fleet/instances/<area> --claude <binary> --skeleton-only   # layout only, idempotent
hxc() { local a=$1; shift; HARNESS_ROOT=/path/fleet/instances/$a HX_TMUX="tmux -L hx-$a" hx "$@"; }
```

Install and verify all of them before approval, and give every Partner persona a go-gate (no
`GO <date>` line in its standing instructions → `READY`/`BLOCKED` row, then wait). Prefer an API
key per instance: several Partners on a few OAuth seats hit usage limits within the hour, and a
usage-limited native process requires owned-process reconciliation. The procedure, the recovery and the product-repository
rules are in `docs/multi-instance.md`.

## 10. What only the operator runs

`hx help` from your shell (no `HARNESS_ID`) prints the operator column; `hx help --all` prints
every audience's. Besides install, up, doctor, ui and heartbeat:

- `hx models [--provider P] [--expect current]`: each provider's live model list, diffed against
  `config/models.json` and `docs/models.md`; `unverified` for a provider with no key (Anthropic,
  on an OAuth seed); exit 3 with `--expect current` when a configured id is stale or its window
  is too large. It writes nothing; the human edits the registry.
- `hx metrics`: recorded packet, run, ownership, check, checkpoint, and delivery counts, and each agent's
  captured chat bytes, output tokens and turns. `hx cache report [ID]`: each agent's
  invariant prompt prefix against its model's budget.
- `hx learn check|path|categories|invariant`: lint the `LEARNINGS/` files and say where they
  live. The Partner reviews proposals; the operator only checks the files.
- `config/capabilities.json`: the MCP servers (`"servers": {NAME: {"command": [...]}}`) and
  operator skills (`"skills": {NAME: "/abs/SKILL.md"}`) workers may reach through `hx tools`;
  the Partner's `hx tools index` records each server's protocol version. `config/hx.json`
  `"skills": ["hx-conversion"]` turns an optional packaged skill on for the instance; it reaches
  each home at that agent's next `hx launch`.

## Never

- Run `claude setup-token`, or read, print, paste or write the token. Relay the two steps.
- Put the instance root inside `~/.claude`, or edit anything in `~/.claude`.
- Bypass configured native controller, Companion, or process-ownership boundaries.
- Add timeouts, kill a session mid-turn, or answer a trust or onboarding prompt by hand — hx
  pre-seeds each home so no such prompt appears; one that does is a bug to report.
- Create workers, write goals, dispatch, or edit a worker's `workdir`. That is the Partner's.
- Tell the human to run an hx command after hand-over. They talk to the Partner.
