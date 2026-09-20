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
