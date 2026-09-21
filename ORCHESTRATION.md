# Building hx: orchestration rules

Three Claude Code sessions build this repo in parallel, one tmux session each, all with cwd
`/Users/loganrobbins/workspace/hx`. An orchestrator session (not one of the three) hands each
lane one milestone at a time as a `/goal` that points at `goals/<lane>-<n>.md`. The spec is
`spec/HARNESS_SPEC.md` (compiled from `spec/NN-*.md`; the section files are authoritative and
`spec/compile.sh` regenerates the compiled file). Read the spec sections your goal names before
writing code. Do not second-guess settled design; if the spec is wrong or silent, write the
question to `handoff/to-orchestrator.md` and build the closest thing the spec allows.

## Lanes and path ownership

| Lane | tmux | Owns (only these paths are edited and committed by this lane) |
|---|---|---|
| build | `build-0` | `src/hx/**` except `src/hx/ui/**`, `src/hx/skills/**`, `src/hx/packaging/**`, `src/hx/skeleton/templates/**`, `src/hx/skeleton/companion/**`, `src/hx/skeleton/PARTNER.md`, `src/hx/skeleton/config/CLAUDE.md`; `tests/**` except `tests/ui/**`, `tests/guard/**`, `tests/packaging/**`, `tests/scenario/**`; `pyproject.toml`; `src/hx/skeleton/adapters/**`, `src/hx/skeleton/config/models.json` |
| ui | `ui-1` | `src/hx/ui/**`, `tests/ui/**` |
| gtm | `gtm-2` | `src/hx/skills/**`, `src/hx/packaging/**`, `src/hx/skeleton/templates/**`, `src/hx/skeleton/companion/**`, `src/hx/skeleton/PARTNER.md`, `src/hx/skeleton/config/CLAUDE.md`, `packaging/**`, `tests/packaging/**`, `tests/scenario/**`, `docs/**`, `README.md`, `LICENSE`, `CHANGELOG.md`, `.github/**` |
| orchestrator | — | `ORCHESTRATION.md`, `CONTRACTS.md`, `goals/**`, `tools/**`, `tests/guard/**`, `.baseline/**`, `spec/**`, `notes/**` |

`src/hx/skeleton/` is what `hx install` copies into a fresh `$HARNESS_ROOT` (spec 03 layout,
17.2). `src/hx/skills/` holds `hx-partner/SKILL.md` and `hx-worker/SKILL.md`, installed into
agent homes only (spec 17.5).

A lane that needs a change in another lane's path appends a dated entry to
`handoff/<from>-to-<to>.md` (create it if missing) and continues with a stub or fixture. Each
lane reads every `handoff/*-to-<lane>.md` at the start of a goal and before finishing it, applies
what is asked, and marks the entry `DONE <date>` in place. Contract changes (`CONTRACTS.md`) go
to the orchestrator via `handoff/to-orchestrator.md`; do not edit `CONTRACTS.md`.

## Git

One shared working tree, one shared index, no branches, no remote. Commit often. The index is
shared, so a plain `git commit` takes whatever another lane has staged. Always commit
path-scoped, in one command:

    git add <explicit paths> && git commit -m "…" -- <the same explicit paths>

The `-- <paths>` form commits only the named paths and ignores the rest of the index. Never a
bare `git commit`. Always `git add <explicit paths>` in your own lane; never `git add -A`, `git add .`, `git stash`, `git reset`, `git checkout --`,
`git rebase`, or `git commit -a`. If `.git/index.lock` exists another lane is committing: wait a
few seconds and retry. Never touch files outside your lane, even to fix an obvious bug: use a
handoff entry. Never create a remote or push.

## Standing constraints (from the spec author; not negotiable)

- Every agent hx launches runs with `--dangerously-skip-permissions`. No timeouts anywhere. No
  character or token caps other than the context budget the spec defines.
- Hand-offs to agents are file paths, never injected content. No task text is ever a
  command-line argument (orders, addenda, personas, goals are files or file pointers).
- Keep native harness functionality under `/goal`. Assume the latest Claude Code
  (`claude --version` in your session is the pinned reference); consult
  `code.claude.com/docs` when behaviour matters, and record what you verified.
- Nothing hx does may touch the user's `~/.claude`, the user's checkouts, or any remote.
- Every model call is a Claude Code session in tmux operated by pasting file paths. No
  `claude -p`, no headless mode, no API client. Every agent and Companion runs with
  `--dangerously-skip-permissions` and `IS_SANDBOX=1`; no dialog is ever shown to an agent.
- The human never runs hx after setup and only talks to the Partner, and gives the Partner its
  goal in chat every time. The Partner has no work item and never dispatches itself.
- v1 cut (spec 14 D25, 2026-09-20): no dependency chains (`after`, `queued`, promotion); no
  guard hook; no locks or atomic-write ceremony on `tasks.json`; no state in filenames, no
  transition tables, no board invariants, no doctor policing of items (state is a frontmatter
  field hx sets); no mirror, sparse worktrees, `hx push`, `hx upgrade`, or unit files
  (`hx install` only); task text in exactly two places (`tasks.json`, the work item; order and
  addendum files are consumed inputs); bench archives the body only.

## Two rules enforced at every milestone

1. **`~/.claude` untouched.** `tools/claude-home-hash.sh` produces a manifest of the user's
   Claude configuration surface; `.baseline/` holds the manifest recorded before the build began.
   `tests/guard/test_user_home_untouched.py` fails on any difference. It runs in every goal's
   completion check, not only at M10. Consequence for your own session: do not run `/model`,
   `/effort`, `/login`, `/config`, `/plugin`, `/permissions`, or install skills or plugins into
   your own Claude, and never point `CLAUDE_CONFIG_DIR`, `HOME`, or `HARNESS_ROOT` at the real
   `~/.claude` in a test.
2. **`HARNESS_ROOT` refusal.** hx refuses a `HARNESS_ROOT` that is, or resolves (symlinks
   included) inside, `~/.claude`; `tests/guard/test_harness_root_refusal.py`. All tests use a
   scratch instance: pytest `tmp_path` for unit tests, and for live tmux/Claude tests a directory
   under your session's scratchpad (the path your system prompt names), never the repo and never
   the user's home.

## What counts as landed

The shared tree is the fact; `goals/*.done.md` is bookkeeping. A goal that says "if build-N has
landed" means: the commands or files it names work in the tree. Use them. What you may not do is
bind to another lane's Python symbols that have not been published to you in a
`handoff/<lane>-to-<you>.md` entry (module path, signature, return, what it raises); until then
call the CLI. Published names are stable; unpublished ones may be renamed without notice.

## Finishing a goal

1. Read and apply any `handoff/*-to-<lane>.md` entries addressed to you.
2. `tools/milestone-check.sh <lane>` passes: `tests/guard` and your lane's own test paths are
   required; every other lane's tests run only with `--all`, as advisory, and are never fatal. A red advisory suite
   is another lane mid-commit: report it in a handoff entry, do not fix it, do not wait for it,
   and say so in the done file. "Passes" in any goal text means exactly this.
3. Everything committed with explicit paths.
4. Write `goals/<lane>-<n>.done.md`, short: one paragraph of what was built; the last lines of
   the test commands; what was verified live against Claude Code versus the fake, in a sentence
   each; open questions as a numbered list; handoff entries you wrote, one line each. Aim for
   under 80 lines; the code and tests are the record, the done file is the pointer. The
   orchestrator reads this file, nothing else, to decide the next goal. Do not start the next
   milestone on your own.

## Fake `claude` for M0–M5

`tests/fakeclaude/claude` (build lane) is a Python script on PATH in tests that records its argv
and environment to a JSON file in the scratch root, accepts pasted input on a real tmux pane,
and emits scripted hook payloads. M6 onward runs the real pinned binary. The ui and gtm lanes
use JSON fixtures that conform to `CONTRACTS.md` until the build lane's commands exist, then
switch to the real commands.

## Python environment

The repo has a shared virtualenv at `.venv` (Python 3.14, the system interpreter, `pytest`, and `hx`
installed editable). Use `.venv/bin/python` for everything (`.venv/bin/python -m pytest`,
`.venv/bin/hx`, `.venv/bin/python -m hx`). Do not install into the system Python and do not
add dependencies: hx is stdlib only. If you need another dev tool, ask in
`handoff/to-orchestrator.md`.
