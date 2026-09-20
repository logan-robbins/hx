# gtm-2 done: packaging that installs, and the CI that proves it

Lane `gtm`, goal 2 (spec 17, M10 part 1). `packaging/e2e-install.sh` passes end to end on this
machine, `tests/guard` (5) and `tests/packaging` (74) pass, everything is committed with
explicit paths, and the handoffs are written.

`tools/milestone-check.sh` exits **0**: 530 passed, 1 skipped. It was red on `tests/ui/`
while I worked and I had written this file up as such; the ui lane landed their cookie-token
change before I closed, so the final state is green. See "Other lanes" at the bottom.

## What was built

### The unit templates moved into the wheel — `src/hx/packaging/`

`hx install` renders the boot and heartbeat units on a machine that has the installed tool and
**no checkout**, so the templates cannot live in a repo-root directory. Moved with `git mv`:

| Was | Now |
|---|---|
| `packaging/launchd/*.plist` | `src/hx/packaging/launchd/*.plist` |
| `packaging/systemd/*` | `src/hx/packaging/systemd/*` |
| `packaging/tested-claude-versions.json` | `src/hx/packaging/tested-claude-versions.json` |

`packaging/` at the repo root now holds exactly one thing, `e2e-install.sh`, which is a release
script and has no business in the wheel. `test_units.py` asserts that split stays true.

**Substitution keys are now `{HARNESS_ROOT}` and `{HX_BIN}`**, per the goal, replacing the
`@…@` form from gtm-1. Substitution is **literal `str.replace`, not `str.format`**: these are
plists, INI files and shell-bearing comments, and a brace in a future comment would make
`str.format` raise — a `KeyError` on a typo'd token is a worse failure than a visible
unsubstituted one. The contract is written up for the build lane.

One fix along the way: the systemd units carried
`Documentation=file://{HARNESS_ROOT}/docs/deploy.md`, which pointed at a path an instance never
has (`docs/` is repo-level and is not in the wheel). Dropped rather than left wrong.

### 1. `packaging/e2e-install.sh`

Takes a scratch directory. Creates a fresh `HOME` and its own `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`
and `UV_CACHE_DIR` beneath it, unsets `HARNESS_ROOT`/`HARNESS_ID`/`CLAUDE_CONFIG_DIR`, and
refuses outright if the scratch path resolves inside the real Claude home. Then, eleven numbered
steps, exiting non-zero at the first failure with the step named:

1. record the real `~/.claude` manifest (`tools/claude-home-hash.sh`)
2. fresh `HOME` and uv directories
3. `uv build`
4. **the wheel carries all 23 package-data files** `hx install` needs — skeleton, both skills,
   the five unit templates, the tested-versions list
5. `uv tool install --python 3.14 <wheel>`, both entry points on `PATH`
6. `hx doctor` from the installed tool, exit 0
7. `hx install --root <scratch>/hx --skeleton-only`
8. **every skeleton file byte-identical** (`cmp` per file against `src/hx/skeleton/`)
9. a fresh instance holds only `partner`
10. **the fresh `HOME/.claude` does not exist** — nothing in the sequence may create it
11. **the real `~/.claude` manifest is unchanged** — re-hashed and `diff`ed

Step 4 has a special case: when a missing file is under `hx/packaging/`, it names the
`pyproject.toml` line and the handoff entry, because that was the one thing gating this goal
and it should not cost the next person an hour.

### 2. `tests/packaging/test_e2e_install.py`

Runs the script under `tmp_path`, prints the whole transcript either way (a packaging failure
is never diagnosable from an assertion message alone), and is **skipped only when `uv` is
absent, with the reason and the install command printed**. Two cheap tests beside it: the
script is executable and `bash -n` clean, and it exits 2 with a usage line when given no
scratch directory.

### 3. `tests/packaging/test_units.py`

**Renders first, then lints.** Checking the templates alone cannot catch a token that never
gets substituted or a substitution that produces malformed XML or INI. Every test here renders
with `SAMPLE_ROOT = /srv/hx` and `SAMPLE_BIN = /opt/uv/tools/hx-harness/bin/hx` — deliberately
not this machine's paths — using the same literal replacement `hx install` will use. Then:

- `plutil -lint` on the rendered plists where it exists (ran here on macOS), `plistlib`
  everywhere;
- an INI parse of the rendered systemd units, with only `[Unit] [Service] [Timer] [Install]`
  allowed;
- **the heartbeat is 900 s on both platforms**, and the two numbers are asserted equal to each
  other and to `HEARTBEAT_SECONDS`;
- between them the five units exec exactly `hx up` and `hx heartbeat` and nothing else;
- exactly the two documented tokens exist across all five templates, and **none survives
  rendering**;
- no host path (`/Users/`, `/home/`, `/opt/homebrew`, `/srv/hx`) is baked into any template;
- `hx-heartbeat.service` still carries no `[Install]` (the timer is what gets enabled), and
  `hx-up.service` keeps `KillMode=none` so the tmux sessions outlive the oneshot unit.

### 4. `.github/workflows/ci.yml`, finished

Job **`test`**, `ubuntu-latest` + `macos-latest`, `fail-fast: false`: install `tmux` (apt/brew)
and `uv` (`astral-sh/setup-uv`), Python 3.14, build `.venv` the way `ORCHESTRATION.md`
describes, **record `.baseline/` from the runner's own empty `HOME/.claude` before any test
runs**, run `tests/guard` alone, run `tools/milestone-check.sh`, then re-hash that Claude home
and `diff` it against the baseline — so the guarantee is asserted by the workflow itself and
not only by a test inside the suite it is checking.

Job **`package`**, `ubuntu-latest`: runs `packaging/e2e-install.sh "$RUNNER_TEMP/e2e"` and
uploads the wheel and sdist as an artifact.

**Which validator: `actionlint` if present, otherwise a minimal structural YAML parser.**
`actionlint` is not installed here, and there is no PyYAML in the virtualenv and never will be
— hx is stdlib-only and `ORCHESTRATION.md` says not to add dependencies. So
`tests/packaging/test_ci_workflow.py` carries a parser for the subset of YAML a workflow uses
(block mappings, sequences of mappings, scalars, flow sequences, block scalars) that rejects
anything outside it. **It has its own negative tests** — tabs, bad indentation, a bare sentence
— because a validator that accepts everything proves nothing about the file it validated. On
top of parsing, that file asserts what CI must actually do: both operating systems in the
matrix, tmux and uv installed, `.venv` built, the baseline recorded *before* the first test
(by step index, not by grep), `milestone-check.sh` run, the e2e script run, the wheel uploaded,
and `permissions: contents: read` with no `secrets.` anywhere.

### 5. Docs, with real output

`docs/deploy.md`, `docs/github-plan.md`, `README.md`, `CHANGELOG.md`. The orchestrator's
answers are applied: **`spec/` ships** (and the README calls it the spec the code is built to,
not a feature list), **`tools/` ships whole** with `ci.yml` still pointing at it, and the
**distribution name is `hx-harness`** with the import package, both entry points and the
repository all staying `hx`.

Every command and every block of output is copied from the runs in this goal. `deploy.md` step
2 carries the real `hx install --skeleton-only` file list; "If something is wrong" carries the
real `hx doctor` output with its four `warn` lines, each naming the setup step that clears it;
`README.md` carries the e2e transcript including the real wheel name
`hx_harness-0.1.0-py3-none-any.whl`. Where a scratch path is elided for width the text says so.
Nothing is invented.

`github-plan.md` §3 is rewritten to the workflow as it now exists, §1 gains the two `packaging`
locations and the reason for each, §6 removes `tools/` from "what stays private", and §7 is
rewritten to the four questions that are actually still open.

### 6. `handoff/gtm-to-build.md`, gtm-2 entry

What `hx install` steps 1, 2, 4, 5, 6 need from the package. Sections: the one `pyproject.toml`
line (`packaging/**/*` in package-data) plus the `hx-harness` distribution name; the package
data paths and how to resolve them (`Path(__file__).parent / "packaging"`, never a
repo-relative `packaging/`); the two substitution keys, why replacement is literal, and where
the rendered units go on each platform; the tested-versions file and the bare-version
comparison for step 1; and the `--from-user-config` seeding contract.

That last one is the part I most wanted on record before it is implemented: **read-only,
always**, and exactly two things copied out of `~/.claude` — `.credentials.json` (hard error if
absent) and the bypass-permissions acceptance entry merged into `seed/home/settings.json` (warn
and continue). Not `CLAUDE.md`, not `skills/`, not `settings.json` wholesale. `docs/two-worlds.md`
promises the user in print that a harness home gets "a copy from `seed/home`" and none of their
configuration; copying their settings file wholesale would drag their hooks and permission
rules into every agent and quietly break it.

**The build lane had already applied it by the time I finished.** The wheel this goal builds is
`hx_harness-0.1.0-py3-none-any.whl` and carries `hx/packaging/**`, so both requests landed.

## `packaging/e2e-install.sh <scratch>` — last 15 lines

```
== 8. every skeleton file landed byte-identical
   ok  18 files identical to src/hx/skeleton/

== 9. a fresh instance installs the Partner and no worker
   ok  config/ holds: partner

== 10. the fresh HOME has no .claude
   ok  /private/tmp/…/scratchpad/e2e-final/home/.claude does not exist

== 11. the real ~/.claude is unchanged
   ok  /Users/loganrobbins/.claude manifest identical before and after

== PASS  wheel built, installed, instance created, no Claude home touched
   wheel    /private/tmp/…/scratchpad/e2e-final/dist/hx_harness-0.1.0-py3-none-any.whl
   instance /private/tmp/…/scratchpad/e2e-final/hx
```

`E2E_EXIT=0`. (The scratch prefix
`/private/tmp/claude-501/-Users-loganrobbins-workspace-hx/0bd6d45e-b525-4cb5-8154-62e4ac2dc927/`
is elided as `…` for width; nothing else is changed.)

Earlier steps from the same run, for what they assert:

```
== 3. uv build
   ok  hx_harness-0.1.0-py3-none-any.whl

== 4. the wheel carries the package data hx install needs
   ok  59 entries, all 23 required files present

== 7. hx install --skeleton-only
   ok  19 files created under …/e2e-final/hx
```

## How it was verified

```
$ .venv/bin/python -m pytest tests/guard
5 passed in 0.70s

$ .venv/bin/python -m pytest tests/packaging
74 passed in 2.79s

$ .venv/bin/python -m pytest tests/packaging/test_units.py tests/packaging/test_ci_workflow.py tests/packaging/test_e2e_install.py
42 passed in 2.11s

$ packaging/e2e-install.sh <scratch>
E2E_EXIT=0

$ tools/milestone-check.sh
530 passed, 1 skipped in 19.96s
MC_EXIT=0
```

An earlier run during the goal was `9 failed, 505 passed, 1 skipped`, all nine in `tests/ui/`.
That was the ui lane mid-migration; it cleared before I closed.

`plutil -lint` ran here (macOS) on both rendered plists. `claude --version` is still `2.1.278`,
matching `src/hx/packaging/tested-claude-versions.json`.

## Live vs. parser-verified

**Verified for real on this machine:** the wheel builds; it carries all 23 required package-data
files; `uv tool install` puts `hx` and `hx-hook` on `PATH`; the installed `hx doctor` exits 0;
the installed `hx install --skeleton-only` creates 19 files; all 18 skeleton files are
byte-identical after the round trip; a fresh instance holds only `partner`; the fresh
`HOME/.claude` is never created; and the real `~/.claude` manifest is identical before and
after. `plutil -lint` on the rendered plists.

**Still parser-verified only:**

- The systemd units parse as INI after rendering, but no `systemd` has loaded them —
  `systemd-analyze verify` is Linux-only and there is no Linux runner here. CI's Linux job runs
  pytest and the e2e script; it does not `systemctl enable` anything either. **First real proof
  is M10 on a clean Linux user.**
- The launchd plists lint but were never `launchctl bootstrap`ed. Same: M10.
- `ci.yml` has never run — there is no remote and no GitHub repository, by design. It is
  structurally validated and asserted against, which is not the same as green.
- `hx install` steps 1 and 3–6 do not exist yet (build-11), so nothing has actually *rendered*
  a unit template into a real `~/Library/LaunchAgents` or `~/.config/systemd/user`. The
  rendering contract is tested against the substitution the handoff specifies, not against
  build's implementation of it.

## Open questions

1. **The organization and repository name.** `autodev-team/hx` is still a placeholder and the
   LICENSE copyright says `autodev-team`. Flagged to the human by the orchestrator; nothing
   created.
2. **When to publish.** Everything is reversible until the first push. No remote exists and hx
   creates none.
3. **`CONTRIBUTING.md` and the issue templates** are described in `github-plan.md` §5 but not
   written. They should land in the change that publishes, not before.
4. **Does `spec/AUTODEV-COMPARISON.md` ship** with the rest of `spec/`? Recommendation is yes;
   it is the one file in `spec/` that discusses a product the reader cannot see.
5. **The ui lane's static files are not in the wheel's required-files list.** `pyproject.toml`
   declares `ui/static/**/*` as package data, but `e2e-install.sh` step 4 does not pin any of
   those filenames, because the ui lane is actively renaming them. Offered in
   `handoff/gtm-to-ui.md`; once they settle, adding them closes the last gap in that check.

## Handoff entries

**Read before starting and before finishing.** `handoff/orchestrator-to-gtm.md` — the answers
to gtm-1's five open questions, all applied in this goal:

| Answer | Applied |
|---|---|
| `spec/` ships | `github-plan.md` §1; README calls it the spec the code is built to |
| Distribution name `hx-harness` | `deploy.md`, `README.md`, `github-plan.md` §1–2; build set `[project] name` |
| `tools/` ships whole | `github-plan.md` §1 and §6; `ci.yml` keeps pointing at `tools/` |
| `autodev-team/hx` stays a placeholder | `github-plan.md` §7 question 1 |
| Companion prompts are M7's to re-tune | noted; untouched this goal |

`handoff/gtm-to-build.md` was already marked `DONE 2026-09-20` by the build lane for the gtm-1
entry — all three items applied, including the relative-`workdir` fix
(`hx.config_harness.resolve_workdir`). Nothing further needed from that entry.

**Written by me:**

- `handoff/gtm-to-build.md`, gtm-2 entry (described above). Both requests in its section 0 were
  applied by the build lane before I finished.
- `handoff/gtm-to-ui.md` — `tests/ui/` is red at the close of this goal. Reported with the
  failing assertion and the likely cause, explicitly not fixed, with a note that it is very
  likely work in progress and an offer to pin their static files in the wheel check.

## Other lanes

For most of this goal `tools/milestone-check.sh` exited 1, entirely on the ui lane: 4 failures
in `tests/ui/test_pane.py` and 5 in `tests/ui/test_views_js.py`, with the visible cause being
the cookie-token change in `56f12a1` landing in `static/app.js` ahead of the test that still
asserted `request["headers"]["Authorization"]`. I reported it in `handoff/gtm-to-ui.md` rather
than touching `tests/ui/**` or `src/hx/ui/**`, and said in that entry that it looked like work
in flight.

It was. The ui lane landed the rest before I finished, and the final state is **530 passed,
1 skipped, `MC_EXIT=0`**. The handoff entry is marked resolved in place; no action is owed by
that lane.

`tests/guard` (5) and `tests/packaging` (74) — the two suites gtm-2's "Done when" names — pass
on their own as well.

Every commit in this goal used `git add <paths> && git commit -- <the same paths>` per the new
ORCHESTRATION Git rule. One earlier attempt failed midway and left files staged; the shared
index then dropped them when another lane committed, which is exactly the hazard the rule
describes, so the work was re-staged and committed in a single command. Nothing outside the gtm
paths was committed.

## Commits

```
daa082d gtm: handoff to ui — tests/ui red at the close of gtm-2
2aeeaba gtm: CI finished, and the docs carry real commands and real output
04c424f gtm: unit templates ship inside the wheel; end-to-end install check
e0f6c5e gtm: handoff to build — package data path, substitution keys, --from-user-config contract
```
