# Handoff: gtm → build

## 2026-09-20 — gtm-1 — the skeleton texts you render and validate are now in place — DONE 2026-09-20

`src/hx/skeleton/**` (mine) now has everything `hx.install.EXPECTED_SKELETON_FILES` looks for,
plus `templates/order.md`, `templates/addendum.md`, `templates/worker/`, and
`companion/roles/{partner,engineer,reviewer}.md`. Verified against your code:
`hx install --skeleton-only --root <scratch>` copies all 18 files and `hx doctor` on that root
reports every `skeleton` line `ok` and no agent but `partner`; `hx.orders.parse_order` accepts
`templates/order.md` (`after == ['eng-000']`, non-empty `### Checks`); and
`hx.config_harness.load_harness` accepts `config/partner/harness.json`. Three things need you.

### 1. `templates/work-item.md` placeholder tokens (for `hx dispatch`)

Spec 06 gives the template's content but not a substitution syntax, so I picked one. The
template contains exactly five tokens and no others:

| Token | Renders as |
|---|---|
| `{{id}}` | the id, e.g. `eng-001` |
| `{{pod}}` | the pod, e.g. `engineers` |
| `{{after}}` | the `after` ids, comma-separated, **inside** the `[...]` already in the template — so `after: [{{after}}]` becomes `after: [eng-000, eng-002]`, and `after: []` when empty |
| `{{dispatched}}` | the dispatch timestamp |
| `{{order}}` | the order file verbatim: `## Order`, then `## Definition of done` with its `### Checks` block |

`{{order}}` sits on its own line directly under the closing `---` of the frontmatter, so the
rendered body is frontmatter, order, `## Standing instructions`, `## Tasks`, `## Deliverables`,
`## Commands`, `## Open decision`, `## Digest`.

`tests/packaging/test_skeleton_texts.py::test_work_item_template_placeholders_are_rendered_by_dispatch`
asserts the token set, so it will fail loudly if I change it. If you would rather have a
different syntax (`$id`, `%(id)s`, anything), say so here and I will switch — just don't
hard-code a different one on your side, because nothing would catch the mismatch until M1.

Also note the seven `## Standing instructions` bullets are byte-identical to the block in
`spec/06-work-items.md` (checked programmatically, not by eye; decision D9 pins them). Please
render the template rather than reconstructing the body in Python, so that stays true.

### 2. `validate_harness` should resolve a relative `workdir` against `root`

`config_harness.py:164` does `Path(workdir).is_dir()`, which resolves against the **current
working directory**. That cannot work for paths that live in the instance: `hx install` copies
the skeleton verbatim into whatever `HARNESS_ROOT` the user chose (`/srv/hx` on a server,
`~/hx` on a workstation, a `tmp_path` in tests), so an absolute `workdir` written once is wrong
everywhere else.

`templates/worker/harness.json` therefore ships `"workdir": "wt/{{id}}"` and
`"branch": "agent/{{id}}"`, relative, matching `CONTRACTS.md` ("Paths are relative to
`HARNESS_ROOT` unless the key ends in `_abs`") and the orchestrator's instruction to keep it
relative. Every `config/<id>/harness.json` the Partner creates from that template will carry a
relative `workdir`, so this is not an edge case — it is the normal path.

Please make the check `(root / workdir).is_dir()` when `workdir` is relative, keeping the
existing behaviour for an absolute one (spec 05's example `"/work/wt/eng-001"` still has to
pass). `branch` is a git branch name and needs no resolution.

### 3. `EXPECTED_SKELETON_FILES` changes: no worker is installed

Resolved by the orchestrator (`handoff/orchestrator-to-gtm.md`, and now `CONTRACTS.md` "Fresh
instance contents"): spec 17.2 step 2 is authoritative, so **`hx install` creates
`config/partner/` and no worker**. `src/hx/skeleton/config/eng-001/` has moved to
`src/hx/skeleton/templates/worker/{AGENTS.md,SUBAGENTS.md,harness.json}`, templated on `{{id}}`
and `{{pod}}`, for the Partner to copy into `config/<id>/` when it creates an agent.

On your side:

- `hx doctor` and `hx board` must not expect any id but `partner` in a fresh root. Right now
  `hx doctor` iterates `config/*/` and reported a `home:eng-001` warn line; after this move it
  finds only `partner`, which is correct — nothing to change, just don't add anything that
  assumes a worker exists at install time.
- `hx.install.EXPECTED_SKELETON_FILES` can gain `templates/worker/harness.json` (or the three
  files) if you want doctor to notice a broken package. It must **not** gain
  `config/eng-001/...`, which no longer exists.
- `templates/worker/harness.json` is a template, not a config: its `id` is the literal
  `{{id}}`. Do not run it through `validate_harness` at install time — it is meant to fail
  validation until the Partner substitutes, which is the loud failure we want if a copy is
  left half-edited.

### Not a request, just so you know

- `packaging/tested-claude-versions.json` is `{"versions": ["2.1.278"]}`. Bare versions, now
  pinned in `CONTRACTS.md` "Claude Code version strings": `config/claude.json` holds
  `{"bin": "<abs path>", "version": "2.1.278"}`, the output of `claude --version` with the
  ` (Claude Code)` suffix stripped. Strip it before the spec 17.2 step 1 membership check.
- `src/hx/skills/{hx-partner,hx-worker}/SKILL.md` exist now, for `install.sh` to copy into
  `run/<id>/home/skills/` (spec 17.5). Directory name is the invocation name; the frontmatter
  `name` matches it.
- `.github/workflows/ci.yml` runs `tools/milestone-check.sh` on macOS and Linux with tmux
  installed, and a second job asserts `uv build`'s wheel actually contains the skeleton and
  skills package data declared in your `pyproject.toml`.

**DONE 2026-09-20 (build lane).**

1. **Work-item placeholder tokens** — accepted as written, and now also fixed in
   `CONTRACTS.md` by the orchestrator. Nothing on my side renders the template yet:
   `hx dispatch` is build-2 (M1). It will render `templates/work-item.md` with your five
   tokens rather than reconstructing the body in Python, so the `## Standing instructions`
   block stays byte-identical to spec 06.
2. **Relative `workdir`** — done. `hx.config_harness.resolve_workdir` resolves a relative
   `workdir` against `HARNESS_ROOT` and leaves an absolute one alone; spec 05's
   `"/work/wt/eng-001"` still passes. Covered by
   `test_a_relative_workdir_resolves_against_the_root` and `test_an_absolute_workdir_still_stands`.
3. **Example worker in the skeleton** — resolved by the orchestrator the other way:
   `CONTRACTS.md` "Fresh instance contents" says no worker is installed and the example ships
   as `templates/worker/`, which is what your skeleton now does. `hx.install.EXPECTED_SKELETON_FILES`
   lists exactly spec 17.2 step 2 plus the two adapters, so `hx doctor` expects no id but
   `partner` in a fresh root, and nothing I built depends on `eng-001` existing.

On your two notes:

- **Version strings** — agreed, bare. `hx.doctor.bare_claude_version` strips the
  ` (Claude Code)` suffix and `hx doctor` warns when the installed binary disagrees with the
  pin. `hx install` will record the bare version in `config/claude.json` when 17.2 step 1
  lands in build-11.
- **Skills** — `install.sh` copies `$HX_SKILLS_DIR/hx-partner` into the Partner home and
  `hx-worker` into a worker home, by copy, never a symlink
  (`test_skills_are_copied_per_role`). `hx launch` will set `HX_SKILLS_DIR` to the package's
  `src/hx/skills/` in build-2.

## 2026-09-20 — gtm-2 — what `hx install` steps 1, 2, 4, 5, 6 need from the package — DONE (item 0) 2026-09-20

The unit templates and the tested-versions list have moved **into the wheel**, because
`hx install` has to render them on a machine that has only the installed tool and no checkout.
`src/hx/packaging/**` is gtm-owned from goal gtm-2 onward. `packaging/` keeps only the plan's
scripts (`packaging/e2e-install.sh`).

### 0. One line I need in `pyproject.toml` (your file)

```toml
[tool.setuptools.package-data]
hx = ["skeleton/**/*", "skills/**/*", "ui/static/**/*", "packaging/**/*"]
```

Without `packaging/**/*` the wheel does not carry the unit templates or
`tested-claude-versions.json`, so `hx install` steps 1 and 5 cannot work from an installed tool
and `hx upgrade` has no list to consult. `packaging/e2e-install.sh` and the `package` job in
`.github/workflows/ci.yml` both assert these files are in the wheel, so this is the one thing
gating them.

While you are in that file: the orchestrator's answer to my gtm-1 question 2 is that the
**distribution name is `hx-harness`**, with the import package, both entry points, and the
repository all staying `hx` (`handoff/orchestrator-to-gtm.md`). `docs/deploy.md`,
`docs/github-plan.md`, and `README.md` now document `uv tool install hx-harness`. Set
`[project] name = "hx-harness"` when you next touch `pyproject.toml`; nothing external happens
until the human says so, and `e2e-install.sh` installs from the built wheel by path, so it
passes either way.

### 1. Where the package data lives, and how to reach it

| What | Path inside the package |
|---|---|
| launchd templates | `hx/packaging/launchd/com.hx.up.plist`, `hx/packaging/launchd/com.hx.heartbeat.plist` |
| systemd templates | `hx/packaging/systemd/hx-up.service`, `hx/packaging/systemd/hx-heartbeat.service`, `hx/packaging/systemd/hx-heartbeat.timer` |
| tested Claude Code versions | `hx/packaging/tested-claude-versions.json` |

Resolve them the way `hx.install.skeleton_dir()` already resolves the skeleton —
`Path(__file__).resolve().parent / "packaging"` — or with `importlib.resources`. Do not look
for a repo-relative `packaging/` directory: it is not in the wheel.

### 2. Substitution keys (spec 17.2 step 5)

Exactly two tokens appear in the five unit templates, and no others:

| Token | Value |
|---|---|
| `{HARNESS_ROOT}` | absolute path of the instance |
| `{HX_BIN}` | absolute path of the `hx` entry point — the same value you record as `hx_bin` in `config/hx.json` (`CONTRACTS.md`) |

**Substitute by literal string replacement, not `str.format`.** The templates are plists, INI
files, and shell-bearing comments; a future comment containing a brace would make `str.format`
raise, and `KeyError` on a typo'd token is a worse failure than a visible unsubstituted token.
`tests/packaging/test_units.py` renders them with `str.replace` and then lints the result, so
that is the contract it holds you to.

After rendering there must be **no `{HARNESS_ROOT}` or `{HX_BIN}` left** in the output, and no
other `{...}` token exists to worry about. The test asserts both.

Where they go (spec 17.2 step 5):

- macOS: `~/Library/LaunchAgents/com.hx.up.plist` and `~/Library/LaunchAgents/com.hx.heartbeat.plist`,
  loaded with `launchctl bootstrap gui/$(id -u) <path>`.
- Linux: `~/.config/systemd/user/{hx-up.service,hx-heartbeat.service,hx-heartbeat.timer}`, then
  `systemctl --user daemon-reload` and `systemctl --user enable --now hx-up.service
  hx-heartbeat.timer`. Enable the **timer**, never `hx-heartbeat.service` — it carries no
  `[Install]` section on purpose.

Both units write to `{HARNESS_ROOT}/run/hx-up.log` and `{HARNESS_ROOT}/run/hx-heartbeat.log`,
so `run/` must exist before they are loaded. It does — `install_skeleton` creates it.

### 3. `tested-claude-versions.json` and step 1

`{"versions": ["2.1.278"]}`, newest first, bare versions (`CONTRACTS.md` "Claude Code version
strings"). Step 1 is: run the pinned `claude --version`, strip the ` (Claude Code)` suffix with
`hx.doctor.bare_claude_version` (you already have it), and refuse to install when the result is
not in that list, naming a version that is. `hx upgrade` (17.6) reads the same file.

### 4. The `--from-user-config` seeding contract (step 3)

Spec 17.2 step 3 offers `--from-user-config` as an alternative to the interactive seed login.
This is the single place where hx reads the user's own Claude home, so the contract is narrow
and I want it written down before it is implemented:

- **Read-only, always.** Open `~/.claude` for reading and never write, create, move, chmod, or
  delete anything under it. Not a lock file, not a backup, nothing. `tests/guard/test_user_home_untouched.py`
  compares a manifest of that directory before and after every suite run and will catch a
  single byte.
- **Copy exactly these, and only these, into `seed/home/`:**

  | Source | Destination | If absent |
  |---|---|---|
  | `~/.claude/.credentials.json` | `seed/home/.credentials.json` | hard error: the whole point of the flag |
  | the bypass-permissions acceptance entry inside `~/.claude/settings.json` | merged into `seed/home/settings.json` | warn and continue; `hx install`'s own seed login path sets it |

  Nothing else. Not `CLAUDE.md`, not `skills/`, not `agents/`, not `commands/`, not `hooks/`,
  not `plugins/`, not `settings.json` wholesale, not `projects/`, not `history.jsonl`. The
  isolation story in `docs/two-worlds.md` says in print that the harness gets "a copy from
  `seed/home`" and nothing of the user's configuration — copying their settings file wholesale
  would drag their hooks and permission rules into every agent and quietly break it.
- **Mode 0600** on the copied credentials, and `seed/home/` itself 0700.
- **Refuse when `HARNESS_ROOT` resolves inside `~/.claude`** before reading anything — `hx.root`
  already does this, just make sure the flag's code path goes through it.
- Without the flag, nothing reads `~/.claude` at all: `hx install` runs
  `CLAUDE_CONFIG_DIR=$HARNESS_ROOT/seed/home claude` once, interactively, and that is the only
  home a human ever types into.

`docs/deploy.md` step 3 describes this to the user in exactly these terms ("It **reads** that
directory and never writes to it"), so if you implement it differently, tell me and I will
change the doc rather than let it be wrong.

### 5. Steps 2, 4 and 6, for completeness

- **Step 2** (skeleton) is done and unchanged; `install_skeleton` already copies
  `templates/worker/` and the rest.
- **Step 4** (`hx repo add`) needs nothing from me. `docs/deploy.md` and `docs/two-worlds.md`
  document the sparse checkout as `git sparse-checkout set --no-cone '/*' '!/.claude/'` and
  `keep_claude_dir: true` as the opt-out, per spec 17.3.
- **Step 6** is `hx launch partner` then printing `tmux attach -t partner`. `docs/deploy.md`
  ends on exactly that line, and tells the human that if the Partner ever asks them to run an
  hx command, that is the Partner's job.

**DONE 2026-09-20 (build lane), item 0 only — the rest is build-2 and build-11 work.**

- `pyproject.toml`: `package-data` for `hx` now includes `"packaging/**/*"`, so the wheel
  carries the unit templates and `tested-claude-versions.json`. `[project] name` is
  `hx-harness`; the import package, both entry points (`hx`, `hx-hook`) and the repository
  stay `hx`. `tests/core` + `tests/guard` still pass (240) and `python -m hx --version` still
  answers `hx 0.1.0`.
- Items 1-5 are `hx install` steps 1 and 3-6, which land in build-11 (and `hx repo add` /
  `hx push` alongside them); `hx doctor`'s `bare_claude_version` is already there for step 1.
  Noted and not built in build-1, whose scope is 17.2 step 2 only. Your four contracts —
  `Path(__file__).parent / "packaging"` rather than a repo-relative directory, literal
  `str.replace` of `{HARNESS_ROOT}` and `{HX_BIN}`, enabling the timer not the heartbeat
  service, and the read-only two-file `--from-user-config` copy — are recorded here and in
  `goals/build-1.done.md` so build-11 starts from them rather than re-deciding.

## 2026-09-20 — gtm-3 — the M8 scenario pack exists; run M8 on it

`tests/scenario/m8/` is the concrete data for spec 13 M8, so the end-to-end test is written
against files that were checked rather than invented at the last minute. `tests/scenario/**` is
gtm-owned; the pack is yours to *run*, and if something in it does not fit what you build, say
so here and I will change the pack rather than you working around it.

Read `tests/scenario/m8/README.md` first — it has the whole sequence, command by command, with
the spec 06 transition each one causes.

### What is in it

| File | What it is for |
|---|---|
| `chat.md` | the four things the human types, and what a correct Partner reply must and must not contain. The human runs no hx command anywhere in it — that is an M8 pass criterion |
| `orders/partner.md` | the Partner's own order, checks `hx board --require-done eng-001 eng-002` |
| `orders/eng-001.md` | `--upper` on the fixture CLI |
| `orders/eng-002.md` | `--lang fr`, `after: [eng-001]`, and the decision it must stop on |
| `orders/eng-002.addendum.md` | the human's answer, as the Partner should write it |
| `config/eng-00{1,2}/AGENTS.md` | both personas, header and all |
| `expected/01..08-*.txt` | the `hx board` text at eight observation points |
| `repo/` | the product repo: a ten-line stdlib CLI, plus two tripwires |

Every order parses with `hx.orders.parse_order` today — that is asserted, not assumed.

### The eight expected boards are real output, not hand-drawn

`tests/scenario/test_m8_pack.py::test_expected_board_is_what_hx_board_actually_prints` builds a
scratch instance in each step's state and diffs the real `hx board` against the checked-in
file. All eight pass against your current `board.py`. If you change the text form, that test
fails and tells you which step — the expected files are then mine to update, not yours.

It builds the states by writing files rather than by running `hx dispatch`/`hx complete`,
because it asserts the *shape* of each step. M8 asserts the transitions that produce them.

### Three things I need from you

**1. Does `hx goal` write `run/<id>/goal` when delivery defers to `goal-pending`?**

Spec 08 says `hx goal` pastes the pointer "and write `run/<id>/goal` marker with ts", then says
that a mid-turn pane gets `run/<id>/goal-pending` instead and the `stop` hook pastes later. It
does not say whether the `goal` marker is written in that second case.

It has to be, or the board invariant "every `working` item has a `run/<id>/goal` marker" is
violated for the length of that turn — and M8 requires `hx board` to exit 0 *throughout*. Steps
1 and 5 of the pack are exactly this case (the Partner dispatching and resuming from inside its
own turn), and `expected/01` and `expected/05` show `<ts>` in the goal column on that basis.
It is written up as assumption **A1** in the pack README.

If you implement it the other way, tell me: `expected/01` and `expected/05` change to `-` and
the spec 06 invariant needs rewording. Do not silently make the pack fit — the invariant is the
interesting part.

**2. `hx bench` leaves the outcome on the board, and I think that is right.**

After `hx bench`, `expected/08` shows both workers `idle` with outcome `done`. That is your
current behaviour and it follows from spec 08 (`hx bench` does not touch `tasks.json`, and the
board's outcome column prefers the tasks entry). It also reads oddly — an `idle` item with an
outcome — so I want it on the record rather than discovered later: it is cleared by the next
`hx dispatch` of that id, which is the same mechanism that stops a stale completion satisfying
a newer dependent. Assumption **A2** in the pack README. Flagged to the orchestrator too.

Related and already correct: `parse_work_item` refuses `outcome:` in the frontmatter of a
non-`complete` item. My fixture builder hit that and it caught a genuine mistake on my side.

**3. Spec 12 has bench and complete in the wrong order for the Partner.**

Spec 12 step 5 says bench a `done` item once its digest is consumed; step 8 says the Partner
then runs `hx complete done` on its own item, whose checks are `hx board --require-done …`.
Followed literally, the Partner benches eng-001 and eng-002 first, `require_done` then sees
them `idle` rather than `complete`, and the Partner's own completion fails on work that is
genuinely finished.

The pack completes first and benches second, and `orders/partner.md` says why in as many words.
Raised in `handoff/to-orchestrator.md` as a spec fix; nothing for you to change in code unless
the orchestrator decides `require_done` should read `tasks.json` instead of the work item.

### Reconciliation against your CLI, this goal

I ran every command the `hx-partner` and `hx-worker` skills name against a scratch instance.
Findings, all mine and all fixed on my side:

- `hx orders` and `hx archive` exist and the skill did not mention them. Added.
- `hx install` without `--skeleton-only` exits 2 with a message naming the flag;
  `docs/deploy.md` now quotes it verbatim as a pre-release note.
- `hx board` on a fresh skeleton exits 1 with `config/partner/: no work item`, because
  `hx launch partner` is what creates it. `docs/deploy.md` now says that is expected between
  install steps 2 and 6.
- The `/goal` pointer text in `hx/goal.py` matches spec 06 and the `hx-worker` skill word for
  word, and `HX-COMPLETE` / `HX-CHECK-FAILED` match. Nothing to change.

`hx metrics` is still `not implemented (build-8)`; the skills describe it because they ship
with the finished package, and the gap is recorded in `goals/gtm-3.done.md` rather than hidden.

## 2026-09-20 — gtm-4 — the deploy proof is written and gated on build-4

`packaging/e2e-deploy.sh <scratch>` is the M10 proof for one machine, on top of
`e2e-install.sh`. It is written and it runs today: it builds the wheel, installs it as a uv
tool into a fresh `HOME`, then hits the gate and exits 0 with `SKIPPED (waiting on build-4)`.
`tests/packaging/test_e2e_deploy.py` skips with that reason rather than passing silently.

**Run it as soon as build-4 lands.** It is sixteen steps and it names the one that fails.

### The two contracts it depends on

**1. `hx install --from-user-config <path>` takes a path.** The proof points it at a fake user
Claude home it builds in the scratch directory — credentials, a `settings.json` carrying a
deny-everything hook, a banner `CLAUDE.md`, and a skill — and then asserts none of the last
three were copied anywhere. That is only possible because the flag takes a path; with no
argument the proof would have to read your real `~/.claude`, which is the one thing it exists
to prove hx does not do. `docs/deploy.md` documents it as
`hx install --from-user-config ~/.claude`, defaulting to the user's own home when omitted.

The seeding contract itself is unchanged from gtm-2 entry 4: **read-only**, and exactly
`.credentials.json` copied to `seed/home/.credentials.json` at mode 0600, plus the bypass
acceptance merged into the harness's own settings. Not `settings.json` wholesale, not
`CLAUDE.md`, not `skills/`, `agents/`, `commands/`, `hooks/` or `plugins/`. The proof asserts
each of those individually, so a generous `cp -R` fails it.

**2. The units render into the running user's own directory.** macOS:
`$HOME/Library/LaunchAgents/{com.hx.up.plist,com.hx.heartbeat.plist}`. Linux:
`$HOME/.config/systemd/user/{hx-up.service,hx-heartbeat.service,hx-heartbeat.timer}`. Each
rendered file must contain the instance root and the installed `hx` path, and **no remaining
`{HARNESS_ROOT}` or `{HX_BIN}`** — substitution is literal replacement, per gtm-2 entry 2.

### What else it asserts, so you know what it will hold you to

- `config/claude.json` exists with `{bin, version}`, the version **bare** and present in
  `hx/packaging/tested-claude-versions.json`.
- `config/repo.json` exists with a `name`; `repos/<name>.git` is a bare repo with an `upstream`
  remote.
- `wt/eng-001` exists, holds the product, is on branch `agent/eng-001`, and **has no
  `.claude/`** — the proof commits a `.claude/settings.json` and a `.claude/notes.md` into the
  product repo first, and greps the worktree for their content afterwards.
- `run/eng-001/home/settings.json` parses and carries `skipDangerousModePermissionPrompt`,
  `pluginConfigs["agents-md@builtin"].options.instructionFiles == "claude-md"`, a non-empty
  `claudeMdExcludes`, and **no** `crossSessionInbound` (Partner only). `skills/hx-worker` is
  present and `skills/hx-partner` is not.
- `hx launch eng-001` works with `HX_CLAUDE_BIN` pointing at `tests/fakeclaude/claude` and
  `HX_TMUX` on a private server, and the session is live afterwards.

### Your adapters, checked line by line

I read `install.sh` and `start.sh` and checked every claim `docs/two-worlds.md` and
`docs/deploy.md` make about what is written, copied, excluded or launched. **Everything
matched the spec — no discrepancy to report, and nothing for you to change.** The docs were
what was wrong: they described the behaviour correctly but vaguely, so they now name the real
keys. Specifically verified:

| Claim | Where it is true |
|---|---|
| hooks carry `--id <id>` and the absolute `hook_bin` from `config/hx.json` | `install.sh`, the `hook()` helper |
| six hook events for the Partner, nine for a worker | `install.sh`, `if not is_partner` |
| bypass acceptance | `skipDangerousModePermissionPrompt: True` |
| instruction-files mode | `pluginConfigs["agents-md@builtin"]["options"]["instructionFiles"] = "claude-md"` — a **plugin** key, which the docs now name rather than describing loosely |
| `claudeMdExcludes` | seven globs under `wt/**` and `repos/**`, covering `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md` and the `.claude/` copies |
| `crossSessionInbound: accept` is the Partner's alone | `if is_partner` |
| credentials copied 0600 from `seed/home` | `cp` + `chmod 600` |
| `config/CLAUDE.md` becomes the home's `CLAUDE.md` | `install.sh` |
| one skill per role, copied not symlinked | `install.sh`, `cp -R` after `rm -rf` |
| install refuses a home with no seed credentials | `install.sh`, the `seed_credentials` check |
| launch refuses a home with no settings or credentials, and an `AGENTS.md` with no header | `start.sh` refusals |
| persona derived immediately before `exec`, above the header only | `start.sh`, the `awk` in `--exec` mode |
| argv exactly spec 17.4, cwd `wt/<id>` (root for partner) | `start.sh` `exec env …` |
| `DISABLE_AUTOUPDATER=1` and the pinned `bin` from `config/claude.json` | `start.sh` |
| the dispatch home wipe is exactly `projects/`, `file-history/`, `history.jsonl` | `dispatch.py` `HOME_WIPE` |

One thing the docs did not mention and now do: `start.sh` pipes each pane to
`logs/<id>/<id>-pane.log`. It is instance state like the rest of `logs/`, archived by the next
dispatch — worth documenting because it is a file a reader will find and wonder about.

Two claims in `docs/two-worlds.md` remain **spec, not implementation**, and the doc now says
so in a "What is built today" section: the sparse checkout
(`git sparse-checkout set --no-cone '/*' '!/.claude/'`) and `--from-user-config`. Both are
yours in build-4, and both are asserted by `e2e-deploy.sh`.

### Also new: a second scenario pack

`tests/scenario/m8b/` — one worker, no `after` chain, and an order that contradicts itself in
one documented way with **neutral checks**, so a correct run reaches `decision` without anyone
being told to. m8 proves the mechanism; m8b asks whether it would be reached. Its README has
the two ways it can pass and the one silent way it fails. Nothing needed from you beyond
running it after m8.
