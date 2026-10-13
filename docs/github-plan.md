# Publishing hx: the productization plan

This is a plan, not a procedure that has been run. **No remote and no GitHub repository have
been created.** Nothing in this document has been executed, and nothing in hx creates a remote
or pushes to one. Executing it is a deliberate, later decision by the spec author.

## 1. The repository

**Name:** `hx`, under the `autodev-team` organization — `github.com/autodev-team/hx`.

**Distribution name on PyPI: `hx-harness`** (settled by the orchestrator, gtm-1 question 2).
The import package, both entry points, and the repository all stay `hx`; only
`pyproject.toml`'s `[project] name` carries the distribution name, and it does today — the
wheel `uv build` produces is `hx_harness-0.1.0-py3-none-any.whl`. Nothing has been checked or
reserved on PyPI, and nothing external happens until the human says so.

**Public**, MIT-licensed, from the first commit that is published. There is no private-then-open
transition planned; see §6 for what does not go in it.

**Default branch:** `main`. Direct pushes disabled; changes land by pull request with CI green.
Tags are the release mechanism.

### Layout as published

```
hx/
  README.md                  what it is, the three problems, how work flows
  LICENSE                    MIT, autodev-team
  CHANGELOG.md               Keep a Changelog; the public surface is defined at the top
  CONTRACTS.md               JSON shapes shared between the CLI and the UI
  pyproject.toml             name, version, entry points, package data
  src/hx/                    the package: CLI, hooks, companion, adapters, ui, skeleton, skills
  src/hx/packaging/          tested-claude-versions.json
  tests/                     core, guard, ui, packaging, scenario
  tests/scenario/m8/         the M8 scenario pack: chat, goals, expected boards, fixture repo
  tools/                     claude-home-hash.sh, milestone-check.sh
  docs/                      deploy.md, two-worlds.md, companion-eval.md, github-plan.md
  packaging/                 e2e-install.sh
  .github/workflows/ci.yml   this plan's CI
```

**`spec/` is gone** (removed 2026-10-13). The design record is now the published
documentation: `docs/system-design.md` is the end-to-end account, `README.md` and
`CONTRACTS.md` are the other two top-level contracts, and the per-subsystem pages under
`docs/` carry the why. Spec numbers still quoted in docstrings (`spec 08`, `spec 17.4`) are
historical pointers into that former record, kept so the reasoning they cite stays traceable.

**`notes/` does not ship.** `AUTODEV-COMPARISON.md` lives there now (§6).

**`tools/` ships whole.** It is not a build artifact: `tools/claude-home-hash.sh` is what the
`~/.claude` guard test compares against, `tools/milestone-check.sh` is the suite entry point,
and `ci.yml` calls both. It stays at `tools/` and `ci.yml` keeps pointing there.

**`tests/scenario/` ships.** It is the data the end-to-end milestones run on, including the
M8 pack's fixture repo and its two deliberate tripwires. A reader who wants to know what hx
claims to do end to end should be able to read the scenario rather than take the README's word
for it.

**Two `packaging` locations, deliberately.** `src/hx/packaging/` holds the unit templates and
the tested-versions list, *inside the wheel*, because `hx install` renders them on a machine
that has the installed tool and no checkout. The repo-root `packaging/` holds only
`e2e-install.sh`, which is a release script and has no business in the wheel.

`ORCHESTRATION.md`, `goals/`, and `handoff/` are build-process artifacts of how hx was written
by a fleet of agents in parallel, not part of the product. They are excluded from the published
repository (§6). `.baseline/` is gitignored and never published: CI records its own (§3).

## 2. Releasing

hx is a pure-Python, stdlib-only package. The wheel is universal: no compiled extensions, no
platform wheels, no matrix.

```bash
uv build                                   # dist/hx-<version>-py3-none-any.whl + .tar.gz
uv publish                                 # to PyPI, from a trusted-publisher CI job
```

Users install with:

```bash
uv tool install hx-harness
# or
pipx install hx-harness
```

`uv tool install` is the documented path because hx is an application, not a library: it should
live in its own isolated environment with `hx` and `hx-hook` on `PATH`, and those two absolute
paths are what get baked into every agent home's `settings.json`.

### Release steps

1. Land the work. CI green on both runners, including `packaging/e2e-install.sh`.
2. Move the `## [Unreleased]` entries in `CHANGELOG.md` into a new `## [x.y.z] - YYYY-MM-DD`
   section. Any change to the `hx` command set, the instance layout, or `CONTRACTS.md` gets a
   **Changed** entry saying what an existing instance must do.
3. Bump `[project] version` in `pyproject.toml`.
4. Confirm `packaging/tested-claude-versions.json` lists every Claude Code version the live
   suite has passed on, newest first (§4); it is a record, not a gate.
5. Tag `vX.Y.Z` and push the tag. A `release.yml` workflow — to be added alongside the tag
   protection, it does not exist yet — runs `uv build`, re-runs the wheel content check from
   `ci.yml`, publishes to PyPI via a PyPI Trusted Publisher (OIDC, no long-lived token in the
   repo), and attaches the artifacts to a GitHub Release whose body is the changelog section.

### Versioning

Semantic versioning. Below 1.0.0 the public surface is the `hx` command set (spec 08), the
instance layout (spec 03), and `CONTRACTS.md`; a breaking change to any of them is a minor
bump with a migration note. 1.0.0 waits on M10 passing on a clean macOS and a clean Linux user.

Upgrading the package never writes into a user's instance (spec 14 D20). `uv tool install`
again replaces the package and nothing else; the instance at `$HARNESS_ROOT` is untouched, and
hook commands keep working because they reference the absolute path recorded in
`config/hx.json`. There is no `hx upgrade` command: it was cut with its tested-list gate
(D25).

## 3. What CI does

`.github/workflows/ci.yml`, on every push to `main`, every pull request, and on demand.

**Job `test`** — matrix `ubuntu-latest` and `macos-latest`, `fail-fast: false`:

1. Install `tmux` (apt on Linux, brew on macOS). hx drives real tmux sessions; a runner without
   tmux would pass a suite that proves nothing.
2. Install `uv` (`astral-sh/setup-uv`) and Python 3.14 (`actions/setup-python`), then build
   `.venv` the way `ORCHESTRATION.md` describes — the system interpreter, `pytest`, and `hx`
   installed editable — so CI runs through the same `.venv/bin/python` the lanes use locally
   and `tools/milestone-check.sh` needs.
3. **Record `.baseline/` from the runner's own Claude home, before any test runs.**
   `tests/guard/test_user_home_untouched.py` compares `~/.claude` against a manifest recorded
   before the build began. A runner has no such history, so the job creates an empty
   `$HOME/.claude` and hashes it with `tools/claude-home-hash.sh` into
   `.baseline/claude-home.manifest`. The assertion is then the real one: nothing in the suite
   wrote to the Claude home it was given. `.baseline/` is gitignored precisely so a developer's
   local manifest is never what CI checks against.
4. Run `tests/guard` on its own first, so a guard failure is never buried in suite output.
5. Run `tools/milestone-check.sh` — the same script every lane runs before finishing a goal.
6. Re-hash `$HOME/.claude` and `diff` it against the baseline, so the guarantee is asserted by
   the workflow itself and not only by a test inside the suite it is checking.

**Job `package`** — `ubuntu-latest`, with `tmux`, `uv`, and Python 3.14:

1. Run `packaging/e2e-install.sh "$RUNNER_TEMP/e2e"` — the same script a human runs before a
   release. It builds the wheel, asserts the wheel carries every package-data file `hx install`
   needs (the skeleton, both skills, the unit templates, the tested-versions list), installs it
   with `uv tool install` into a `HOME` that did not exist a moment ago, runs the installed
   `hx doctor` and `hx install --skeleton-only`, asserts every skeleton file landed
   byte-identical and that a fresh instance holds only `partner`, and finally asserts the fresh
   `HOME/.claude` does not exist and the real `~/.claude` manifest is unchanged.
2. Copy the wheel and sdist out of the scratch directory and upload `dist/` as an artifact.

The package-data assertion is the one that earns its keep: `pyproject.toml` declares
`skeleton/**/*`, `skills/**/*`, `ui/static/**/*`, and `packaging/**/*`, and if one of those
globs silently stops matching, `hx install` ships with nothing to copy and no unit test
notices.

**Validating `ci.yml` itself.** `tests/packaging/test_ci_workflow.py` uses `actionlint` when it
is on `PATH` and otherwise a minimal structural YAML parser written for the purpose — there is
no PyYAML in this repo's virtualenv and there never will be, because hx is stdlib-only. The
fallback parses the subset of YAML a workflow uses and rejects anything outside it; its own
negative tests prove it rejects tabs, bad indentation and non-`key: value` lines, so it is not
a validator that accepts everything. On top of parsing, that file asserts what CI must actually
do: both operating systems in the matrix, tmux and uv installed, the baseline recorded before
the first test, `milestone-check.sh` run, the e2e script run, the wheel uploaded, and
`permissions: contents: read` with no secrets anywhere.

### Not in CI, and why

The M6+ milestones run a live Claude Code against a real account. That needs credentials, costs
money per run, and is non-deterministic in wall-clock terms. It stays a local, pre-release gate
run by a human before a version is added to the tested list, and never becomes a pull-request
check. CI covers everything that runs against the fake `claude` and the fixtures.

Branch protection on `main`: both `test` jobs and `package` required, linear history, no force
push. CI needs no secrets — it only reads, builds, and uploads an artifact; publishing is a
separate workflow that does not exist yet (§2).

## 4. Recording Claude Code, not pinning it

hx runs the same `claude` binary the user already has, at whatever version it is. The version
is recorded as evidence; it is never a gate. (Until 2026-10-13 `hx install` refused a version
missing from a hand-maintained tested list; that gate made every fleet stale by default and
is gone.)

- `hx install` records `{bin, version}` in `config/claude.json` for the `claude` it finds (or
  `--claude <path>`), and prints the version with `(tested)` or `(untested; the live suite
  last passed on X)` beside it. `--ignore-claude-version` is accepted and does nothing.
- Every launch boundary — `hx launch`, `hx restart`, `hx up` — re-records the version the
  binary reports now (`hx.claude_bin.refresh_pin`). A fleet therefore runs the latest release
  from its next launch, automatically, and `hx doctor` always has the current record.
- Sessions still launch with `DISABLE_AUTOUPDATER=1` and the recorded absolute binary path,
  so an upstream release cannot change a *running* agent underneath its operator: "latest"
  arrives at a launch boundary, never mid-turn. Grok (`auto_update = false`) and Pi
  (`PI_SKIP_VERSION_CHECK=1`) hold the same line.
- The package still ships `hx/packaging/tested-claude-versions.json`
  (`{"versions": ["2.1.296", …]}`, bare versions as `claude --version` reports them before
  the ` (Claude Code)` suffix, newest first). It is the record of what the live suite
  (`HX_LIVE=1 pytest tests/live`) last passed on, which `hx doctor` and `hx install` report
  as `tested` / `untested`. Nothing refuses on it.
- `hx doctor` warns, and never fails, when the binary now reports a different version than
  `config/claude.json` recorded; the warning says the next launch re-records it.

**Adding a version to the list is still a human act with evidence behind it.** The pull
request that adds an entry states which live suite run passed on it, on which OS. A version is
never added because it exists — but a fleet never waits for the entry either.

## 5. Issues, contribution, and support posture

`CONTRIBUTING.md` — to be written before the repository is published — says:

- The docs are the design. A pull request that changes behaviour changes the relevant
  `docs/` page and `CONTRACTS.md` in the same commit.
- Milestone acceptance tests (spec 13) are the bar for a behavioural change.
- No runtime dependencies are accepted. hx is stdlib-only, on purpose: it is installed as a
  tool on machines that also run an agent fleet, and its dependency surface is part of its
  isolation story.
- Issue templates: a bug template that asks for `hx doctor` and `hx board` output, the OS, the
  `claude --version`, and the relevant part of `hx show <id>` — never a raw transcript, which
  carries the user's own code.

Support posture at launch is best-effort, stated plainly in the README rather than implied by
an issue template promising triage.

## 6. What stays private

Nothing sensitive is in the repository today, and the rules that keep it that way:

- **The build process.** `ORCHESTRATION.md`, `goals/`, `handoff/`, and `notes/` are how hx was
  built by a fleet of agents in parallel. They name local paths, tmux session names, and lane
  assignments, and they would read as instructions to anyone who cloned the repo. They are
  excluded. `tools/` is **not** in that category and ships whole (§1): the guard tests, the
  suite entry point, and CI all depend on it. `.baseline/` is gitignored and never published —
  it is one developer's machine fingerprint, and CI records its own.
- **Anything derived from the prior product.** The prior product is material that was mined,
  not vendored. No file of its is copied into hx, and its `website/` was never read.
  `AUTODEV-COMPARISON.md` — the functional reassessment that drove several of the spec's
  decisions — is **not published**: it lives in `notes/`, which is excluded along with the rest
  of the build-process material. It compares hx against a product the reader cannot see and
  would read as a claim about someone else's software. The decisions it produced are already in
  `docs/`, stated on their own terms, which is where a reader should meet them.
- **Credentials, always.** hx never stores a token in the repository. The one instance
  credential is `$HARNESS_ROOT/seed/token`, mode 0600, outside the package; agent homes under
  `run/<id>/home/` hold none at all. The UI's bearer
  token is `run/ui-token`, mode 0600, generated per instance. PyPI publishing uses a Trusted
  Publisher, so no token exists in repository secrets either.
- **Instance data.** `config/`, `pods/`, `logs/`, `state/`, `run/`, `archive/` and `seed/`
  belong to a user's `$HARNESS_ROOT` and never to this repository. Only the
  *skeleton* under `src/hx/skeleton/` is shipped, and the only id in it is `partner`: the
  example worker ships as `templates/worker/`, templated on `{{id}}`, and is not installed.
- **Real repository contents of any user.** hx never copies, clones or pushes a user's code: a
  worker's `workdir` is an absolute directory its operator chose, and hx only ever runs
  `git status --porcelain` in it. Nothing about a user's product repo is ever collected, and
  the bug template asks for board and doctor output rather than transcripts for exactly this
  reason.

## 7. Open questions for the spec author

Everything asked in the first two drafts of this plan has been answered
(`handoff/orchestrator-to-gtm.md`) and written in above: **`spec/` shipped** until its removal on 2026-10-13, when the docs
became the design record; the distribution name is **`hx-harness`**; **`tools/` ships
whole**; **`AUTODEV-COMPARISON.md` does not ship** and has moved to `notes/`; `CONTRIBUTING.md`
and the issue templates land with the publishing change, not before.

What is left is not a question for me to resolve:

1. **The organization and repository name.** `autodev-team/hx` is a placeholder and the LICENSE
   copyright says `autodev-team`. The human's call; nothing has been created, and nothing in hx
   creates a remote or pushes to one.
2. **When to publish.** Everything here is reversible until the first push.
