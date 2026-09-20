# build-1 done: Milestone M0 (spec 13) plus the two standing guard rules

## What was built

Everything in `goals/build-1.md`, in the build lane's paths only.

### 1. `hx` CLI and the standing HARNESS_ROOT refusal

- `src/hx/__main__.py`, `src/hx/cli.py`. `main()` dispatches every command in spec 08 plus
  `install`, `up`, `doctor`, `ui`, `show`, `repo`, `push`, `upgrade`, and the `orders` and
  `archive` the orchestrator added mid-goal. Unimplemented ones exit 2 with
  `hx: <cmd>: not implemented (build-N)`, N being the build-lane goal that delivers it
  (spec 13 milestone + 1, confirmed in `handoff/orchestrator-to-build.md` answer 1). The map
  is one dict, `hx.cli.NOT_IMPLEMENTED`.
- `src/hx/root.py` is the one resolver: `--root`, else `HARNESS_ROOT`, else `~/hx`. It refuses
  with the word `refuse` and exit 1 any root that is, or resolves inside, the user's
  `~/.claude`, comparing **both** the literal path and the fully symlink-resolved path, so
  neither a symlink into `~/.claude` nor a `~/.claude` that is itself a symlink gets past.
  Every command calls it before anything else — including the unimplemented ones, which is
  asserted directly (`test_every_command_refuses_before_doing_anything`).
- `src/hx/hooks.py` gives `hx-hook` its entry point so the `pyproject` console script works;
  it validates `--id <id> <event>` against the spec 09.1 vocabulary and names the build goal
  that delivers each event.
- `hx doctor` prints the resolved root, the Python version, the tmux version, git, and the
  pinned binary from `config/claude.json` when present, then the skeleton, models, seed, home
  and repo checks, and exits 0.

### 2. Layout creation

`src/hx/install.py`. `hx install --skeleton-only --root <path>` creates the spec 03 directory
layout, copies `src/hx/skeleton/**` into it preserving modes (so the adapters stay executable),
and writes the instance `.gitignore` (`pods/ logs/ state/ run/ orders/ tasks.json`). It never
overwrites an existing file, so re-running is safe. `EXPECTED_SKELETON_FILES` is exactly what
spec 17.2 step 2 lists plus the two adapters — no worker, per `CONTRACTS.md` "Fresh instance
contents" — and `hx doctor` reports each missing one as a `warn` while still exiting 0.
Plain `hx install` exits 2 naming `--skeleton-only`; steps 1 and 3-6 land in build-11.

### 3. Validators and parsers, one module each

Every rejection message names the file and the rule that rejected it.

| Module | Validates | Malformed fixtures rejected |
|---|---|---|
| `config_models.py` | `config/models.json` (spec 05), 1M windows capped at 500000 | 16 |
| `config_harness.py` | `config/<id>/harness.json` (spec 05) incl. its four cross-file rules | 17 + 6 |
| `orders.py` | order files (spec 06): `## Order`, `## Definition of done`, non-empty `### Checks` bash block, frontmatter `after` | 14 |
| `workitems.py` | the spec 06 filename regex, and work-item frontmatter | 11 names + 10 bodies |
| `frontmatter.py` | the shared YAML subset both order files and work items use | — |

Supporting readers, all read-only in M0: `tasks.py` (`tasks.json`), `streams.py`
(`logs/<id>/**`), `tmux.py`, `timestamps.py`, `ids.py`.

### 4. `hx board`

`src/hx/board.py`. The text form is `<work-item-file>  <after>  <outcome>  <open subagents>
<goal ts>` per id then the invariant errors; `--json` is the object in `CONTRACTS.md`, field
for field, `partner` first then by id. Exit 0 when `errors` is empty, else 1, in both forms.
`--require-done <id>…` exits 0 iff every listed item is `complete` with outcome `done`, which
is what the Partner's own `### Checks` run.

All nine invariants of spec 08 are computed and each has its own test: one work item per id;
names match the regex; work item ⇄ `config/<id>/`; every `tasks.json` key has a config dir;
`working` needs a live session and a goal marker; `queued` needs an unmet `after` and no goal
marker; `complete` needs zero `-open` streams; every `run/<id>/home/` needs its settings and
credentials. A malformed work item or `tasks.json` is reported as an error, never raised.

`context_tokens`, `seams` and `session_alive` are computed when the data exists and are
`null`/`null`/`false` otherwise. `seams` counts only records at or after `dispatched`, so a
previous dispatch's seams do not leak into the current one.

### 5. `adapters/claude/install.sh`

Writes `run/<id>/home/settings.json`:

- the nine spec 09.1 hooks, each carrying `--id <id>`, at `<hook_bin> --id <id> <event>` with
  `hook_bin` read from `config/hx.json` (`CONTRACTS.md`) and falling back to
  `$HARNESS_ROOT/bin/hx-hook`. Mapping asserted event by event: `SessionStart`
  `startup|resume|clear|compact` → `context`; `PreToolUse *` → `guard`; `PostToolUse *` →
  `log`; `PostToolUse Agent` → `subagent-result`; `SubagentStart`/`SubagentStop`; `Stop` (no
  matcher); `PreCompact`; `PostCompact`. The three subagent events are omitted for `partner`,
  which spec 09.1 marks "Non-Partner".
- instruction-files mode `claude-md`, `claudeMdExcludes` for the product repo under `wt/` and
  `repos/` (never under `run/`, so the home's own `CLAUDE.md` still loads), the bypass
  acceptance, and `crossSessionInbound: accept` for the Partner alone.
- Copies `seed/home/.credentials.json` into the home at mode 0600, copies `config/CLAUDE.md`
  to `home/CLAUDE.md`, and copies `hx-partner` or `hx-worker` from `$HX_SKILLS_DIR` into
  `home/skills/` by copy, never a symlink. **Refuses** when `seed/home/` has no credentials.

### 6. `adapters/claude/start.sh`

`start.sh <id>` ensures tmux session `<id>` with `HARNESS_ID`, `HARNESS_ROOT`,
`CLAUDE_CONFIG_DIR` and `DISABLE_AUTOUPDATER=1` on the session and runs `start.sh --exec <id>`
in window `main`; `--exec` derives `run/<id>/persona.md` from `config/<id>/AGENTS.md` above
`## UPDATES BELOW ONLY` immediately before exec, then execs exactly the spec 17.4 argv, cwd
`wt/<id>` (`HARNESS_ROOT` for `partner`). No prompt argument, ever. The two modes are explicit
flags rather than a `$TMUX` heuristic, because the Partner runs `hx restart` from inside its
own pane and a heuristic would read the wrong session. Relaunch respawns window `main` rather
than stacking sessions, which is the shape `hx restart` needs in build-7.

It refuses: no `harness.json`, no `AGENTS.md`, an `AGENTS.md` with no `## UPDATES BELOW ONLY`
line, no home settings, no home credentials, no worktree, no or non-executable claude binary,
and a non-id.

### 7. `tests/fakeclaude/claude`

Records argv, env and cwd to `$HARNESS_ROOT/run/<id>/fake-argv.json`; stays alive reading
stdin on a real tmux pane appending every pasted line to `fake-input.log`; emits scripted hook
payloads from `HX_FAKE_SCRIPT` (`on: start` or an exact pasted line) through `$HX_HOOK_BIN`,
logging each to `fake-hooks.log`. Python 3, stdlib only.

### 8. Tests

`tests/core/` — 238 tests. Unit tests use pytest `tmp_path` roots; the launch tests use a real
tmux server on a private socket (`tmux -L hx-test-<pid>`, killed at teardown) and the fake
`claude`. Nothing points `HOME`, `CLAUDE_CONFIG_DIR` or `HARNESS_ROOT` at the user's `~/.claude`,
and the child environment is stripped of inherited `HARNESS_*`/`CLAUDE_*`/`HX_*`.

## How it was verified

```
$ ./tools/milestone-check.sh          # guard + full suite, before the gtm lane's packaging/ move
.....                                                                    [100%]
........................................................................ [ 97%]
............                                                             [100%]
(exit 0)

$ .venv/bin/python -m pytest tests/guard
5 passed in 0.47s

$ .venv/bin/python -m pytest tests/core
238 passed in 12.02s

$ .venv/bin/python -m pytest tests/packaging   # the gtm lane's, at close of goal
74 passed in 2.76s

$ .venv/bin/python -m pytest tests/ui          # the ui lane's, at close of goal
7 failed, 154 passed, 1 skipped in 3.93s
```

**State of the full suite at close.** `tools/milestone-check.sh` passed end to end — exit 0,
`tests/guard` plus all 444 tests of all four lanes — immediately before the gtm and ui lanes
began their own in-flight changes. Two other-lane breakages appeared during this goal; per
ORCHESTRATION.md "Finishing a goal" step 2 I reported each in a handoff entry rather than
fixing it, and neither blocks this goal:

1. **gtm, resolved.** Moving `packaging/**` to `src/hx/packaging/**` left
   `tests/packaging/test_skeleton_texts.py` resolving `PACKAGING` to the now-empty top-level
   `packaging/`, which raised at **import** time and interrupted collection for the whole
   suite, not just that file. Reported in `handoff/build-to-gtm.md`; the gtm lane has since
   fixed it and `tests/packaging` is 74 passed.
2. **ui, open at close.** `tests/ui` is 7 failed / 154 passed, every failure a 401 where the
   test expects 200, while `src/hx/ui/server.py` and `index.html` are modified and
   `src/hx/ui/pane.py` is untracked — the ui lane's bearer-token work in flight. Reported in
   `handoff/build-to-ui.md`. The build lane wrote no file under `src/hx/ui/**` or `tests/ui/**`
   in this goal, and `hx ui` is still `not implemented (build-10)`, so nothing in the CLI
   reaches that server.

The build lane's own gates are green: `tests/guard` 5 passed, `tests/core` 238 passed,
and `tests/packaging` 74 passed alongside them.

### Done-when commands, on a scratch root outside the repo and outside `$HOME`

```
$ .venv/bin/python -m hx install --skeleton-only --root <scratchpad>/acceptance
root /…/scratchpad/acceptance
created  PARTNER.md
created  adapters/claude/install.sh
…19 files…
created  .gitignore

$ HARNESS_ROOT=<scratchpad>/acceptance .venv/bin/python -m hx doctor
ok    python        3.14.7 (…/.venv/bin/python)
ok    tmux          tmux 3.7c
ok    git           git version 2.50.1 (Apple Git-155)
ok    root          /…/scratchpad/acceptance
warn  claude        config/claude.json absent; `hx install` records {bin, version} (spec 17.1)
ok    skeleton      …11 lines, all ok…
ok    models        2 model(s): claude-opus-5, claude-sonnet-5
warn  seed          seed/home/.credentials.json absent; `hx install` runs the seed login (spec 17.2 step 3)
warn  home:partner  run/<id>/home absent; `hx launch` runs adapters/claude/install.sh
warn  repo          config/repo.json absent; `hx repo add <url|path>` mirrors the product repo (spec 17.2 step 4)
doctor exit: 0

$ HARNESS_ROOT=<scratchpad>/acceptance .venv/bin/python -m hx board --json
… items: [ {"id": "partner", "pod": "partner", "role": "partner", "state": null, … } ] …
  "errors": ["config/partner/: no work item (spec 08 board invariants)"]
board --json exit: 1
```

Exit 1 on a fresh root is correct, not a defect: a fresh instance has `config/partner/` and no
work item until `hx launch partner` (17.2 step 6, build-2) creates the `-idle` item. With the
partner work item rendered from `templates/work-item.md` and `install.sh` run, the same command
is clean:

```
$ HARNESS_ROOT=<scratchpad>/acceptance .venv/bin/python -m hx board
pods/partner/partner-idle.md  -  -  0  -
board exit: 0
```

### Live launch on a real tmux server, against the fake `claude`

```
$ HARNESS_ROOT=… HX_TMUX="tmux -L hx-acceptance-…" bash adapters/claude/start.sh partner
start.sh: …/tests/fakeclaude/claude running in tmux session partner window main (cwd /…/acceptance)

recorded argv: --dangerously-skip-permissions --effort xhigh --model claude-opus-5 \
               --append-system-prompt-file /…/acceptance/run/partner/persona.md
cwd:  /…/acceptance
env:  HARNESS_ID=partner, HARNESS_ROOT=/…/acceptance,
      CLAUDE_CONFIG_DIR=/…/acceptance/run/partner/home, DISABLE_AUTOUPDATER=1
persona.md: the whole of config/partner/AGENTS.md above `## UPDATES BELOW ONLY`, nothing below it
```

## Verified live against Claude Code vs. against the fake

**Live, against the real Claude Code and its documentation (nothing here was assumed):**

- `claude --version` in this session: `2.1.278 (Claude Code)`. That is the pinned reference.
- Settings keys, checked 2026-09-20:
  - `https://code.claude.com/docs/en/settings-reference` — `claudeMdExcludes` (string array of
    globs matched against absolute paths), `crossSessionInbound`, `hooks`,
    `skipDangerousModePermissionPrompt` (the bypass-permissions acceptance; confirmed present
    in a real logged-in home).
  - `https://code.claude.com/docs/en/memory` — **the spec's "instruction-files mode `claude-md`"
    is not a top-level key.** The real setting is
    `pluginConfigs["agents-md@builtin"].options.instructionFiles`, whose values are
    `claude-md-or-agents-md` (default), `claude-md-and-agents-md`, `claude-md`, `managed-only`.
    `install.sh` writes `"claude-md"` there. Claude Code ignores it in project and local
    settings, but honours it in the settings file at the root of `CLAUDE_CONFIG_DIR`, which is
    exactly where hx writes it.
  - `https://code.claude.com/docs/en/cross-session-messaging` — `crossSessionInbound` values are
    `accept` | `hold` | `refuse`. The spec's `accept` is right. (The settings-reference page's
    summary of this key lists `deliver`/`notice`/`refuse`; the messaging page's table is the
    authoritative one and matches the spec.) The same page confirms the
    `CLAUDE_CODE_MESSAGING_SOCKET` / `CLAUDE_CODE_MESSAGING_TOKEN` pair and the
    `{"type":"auth","token":…}`-then-message wire format that spec 08 `hx wake partner` uses.
  - `https://code.claude.com/docs/en/hooks` — every event name spec 09.1 relies on exists:
    `SessionStart` (matcher `startup|resume|clear|compact|fork`), `PreToolUse`, `PostToolUse`,
    `SubagentStart`, `SubagentStop`, `Stop`, `PreCompact`, `PostCompact`. `Stop` takes **no**
    matcher, which is why its entry is written without one. `"*"` is a valid matcher. Deny is
    exit 2 with the reason on stderr. Only `SessionStart`, `UserPromptSubmit`,
    `UserPromptExpansion` and `PostModelSwitch` inject plain stdout as context — exactly the
    split spec 09.1 depends on for `context` (stdout) versus `subagent-start` (JSON
    `additionalContext`).

**Against the fake `claude` only:** everything about the launch itself — the spec 17.4 argv, the
session environment, the cwd, persona derivation, respawn on relaunch. No real Claude Code
process was started by this goal; M6 is where the real binary takes over, per spec 13. The
flags themselves (`--dangerously-skip-permissions`, `--effort`, `--model`,
`--append-system-prompt-file`) are the ones spec 11 records as verified against the CLI
reference on 2026-09-20; I did not re-verify them against the binary.

**Not verified at all, and deliberately so:** that Claude Code actually loads
`run/<id>/home/settings.json` and fires the hx hooks. That is M2-M4's job and needs the real
binary. What M0 asserts is that the file hx writes has the right keys, events, matchers and
ids — which is exactly what the M0 pass criterion asks for.

## Open questions

1. **`hx doctor` exit code tightens later.** Spec 08 wants exit 1 with the list for a missing
   seed login, a home without settings, or an unreachable mirror. In M0 those are `warn` and
   doctor exits 0 (confirmed as the right call in `handoff/orchestrator-to-build.md` answer 5).
   Each becomes a `fail` when its milestone lands; nothing tracks that but this note.
2. **`hx board` on a fresh instance exits 1** for `config/partner/: no work item`, because the
   spec 08 invariant "every `config/<id>/` has a work item" is true only after `hx launch`.
   Correct per the spec and harmless, but any `### Checks` block that runs a bare `hx board`
   between `hx install` and `hx launch partner` will fail. Flagging in case 17.2 step 6 should
   be considered part of "installed".
3. **`config/hx.json` is not written yet.** `install.sh` reads `hook_bin` from it and falls back
   to `$HARNESS_ROOT/bin/hx-hook`; `hx install` records it in build-11 (17.2 step 1). Until
   then every real instance uses the fallback, so `bin/hx-hook` has to exist or the hooks are
   dead paths. `hx doctor` does not check this yet — it will when build-11 writes the file.
4. **`seams` is `null` versus `0`.** A missing main stream gives `null`; a present one with no
   seam records gives `0`. `CONTRACTS.md` shows an integer and goal build-1 says these fields
   are `null` when the data does not exist, so I read "no stream" as "no data". If the ui lane
   would rather always render an integer, that is a one-line change.
5. **`start.sh` uses `python3` to read JSON.** Two `harness.json` fields and one `claude.json`
   field; `${HX_PYTHON:-python3}` overrides it. hx already requires Python ≥ 3.14, so this adds
   no dependency, but it does mean `start.sh` is not pure bash. Say so if that matters.

## Handoff entries written

- `handoff/to-orchestrator.md` — two entries. (a) The pre-existing
  `tests/guard/test_user_home_untouched.py` failure on
  `~/.claude/plugins/known_marketplaces.json`, which hx never touched; **resolved by the
  orchestrator** (manifest now prunes the cloud-synced trees and blanks `lastUpdated`), guard
  reruns clean. (b) The five build-1 ambiguities — `build-N` numbering, the 17.2 step number,
  `crossSessionInbound` scope, `config/hx.json` keys, and the doctor exit code — **all five
  answered** in `handoff/orchestrator-to-build.md` and applied; my readings were confirmed in
  every case. (c) A shared-index git hazard: the gtm lane's commit swept up the build lane's
  staged files, and the fix (`git add <paths> && git commit -m … -- <the same paths>`) is now
  ORCHESTRATION.md's rule for all lanes.
- `handoff/build-to-gtm.md` — `tests/packaging/test_skeleton_texts.py` interrupts collection
  for the whole suite after the `packaging/` → `src/hx/packaging/` move, plus a request to
  resolve the directory inside the test rather than at import so one lane's move cannot take
  every lane's tests down.

## Handoff entries read and applied (marked `DONE` in place)

- `handoff/orchestrator-to-build.md` — all five answers applied, each with the test that holds
  it.
- `handoff/gtm-to-build.md`, gtm-1 entry — (1) the `{{id}}`/`{{pod}}`/`{{after}}`/
  `{{dispatched}}`/`{{order}}` token set accepted as-is, to be *rendered* rather than
  reconstructed when `hx dispatch` lands in build-2; (2) a relative `workdir` now resolves
  against `HARNESS_ROOT` (`hx.config_harness.resolve_workdir`), absolute still stands;
  (3) the example worker moved to `templates/worker/` and nothing I built expects `eng-001` in
  a fresh root. Plus the bare-version rule (`hx.doctor.bare_claude_version`) and per-role skill
  copying.
- `handoff/gtm-to-build.md`, gtm-2 entry — item 0 applied: `pyproject.toml` `package-data` now
  ships `packaging/**/*` in the wheel, and the distribution name is `hx-harness` with the
  import package, both entry points and the repository staying `hx`. Items 1-5 are `hx install`
  steps 1 and 3-6, which are build-11; their contracts are recorded there and above so build-11
  starts from them.

## Open questions — all five answered by the orchestrator before this goal closed

`handoff/orchestrator-to-build.md` answers the five questions listed above, and the two that
touch build-1's own code were applied here (marked `DONE` in that file):

1. Doctor tightening is tracked in each later goal; build-2 tightens the `home` checks.
2. `hx launch partner` is part of "installed", and `hx launch` creates a missing `-idle` work
   item, so the fresh-root `hx board` exit 1 is correct and nothing runs a bare board in
   between.
3. `config/hx.json` does not wait for build-11: build-2's `hx install --skeleton-only` writes
   it with the running package's `hx`, `hx-hook` and `python_bin` paths, and `hx doctor` fails
   when either binary is missing.
4. `seams` `null` for no stream and an integer otherwise is the right reading.
5. `python3` in the adapters is acceptable, preferring `config/hx.json`'s `python_bin`.
   **Applied in this goal**: both adapters now read `python_bin`, then `$HX_PYTHON`, then
   `python3`, and refuse by name when the chosen interpreter is absent (two new tests, 238 in
   `tests/core`).

The spec's "instruction-files mode `claude-md`" wording in 05, 11, 13 and 17 has been updated
to the real key this goal found; `install.sh` and its test already write and assert it.

## Notes for whoever writes build-2

- `hx dispatch` must render `templates/work-item.md` with the five `{{…}}` tokens, not rebuild
  the body in Python, so the `## Standing instructions` block stays byte-identical to spec 06.
- `hx launch` should set `HX_SKILLS_DIR` to the package's `src/hx/skills/` when it calls
  `install.sh`, and pass `HX_TMUX` through in tests.
- `logs/<id>/<id>-pane.log` (spec 03/11, build-2 item 12) is already ignored by
  `hx.streams._SUBAGENT_RE`; `test_the_pane_log_is_not_a_stream` pins that.
- `hx board`'s `goal_ts` reads the marker's contents when they parse as a timestamp and falls
  back to its mtime, so `hx goal` should write an ISO 8601 UTC `Z` timestamp into
  `run/<id>/goal`.
