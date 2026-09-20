# gtm-1 done: skills, prompt and template texts, packaging units, docs, GitHub plan

Lane `gtm`, goal 1. Everything `goals/gtm-1.md` asks for exists, is checked against the spec
section that constrains it, is covered by `tests/packaging/test_skeleton_texts.py`, and is
committed with explicit paths. `tools/milestone-check.sh` exits 0.

## What was built

All paths below are gtm-owned per the ORCHESTRATION table.

### 1. Companion prompts — `src/hx/skeleton/companion/`

| File | Checked against |
|---|---|
| `BASE.md` | spec 10 (whole section), 07.1 raw-record shape, **07.2 step-state schema field by field**, 07.3, 05 (`companion.*` knobs: `batch_records`, `state_budget_tokens`, `seam_min_context_tokens`, `seam_min_interval_s`, `cache_ttl`), 04 (the Companion's one exception: `## Digest`) |
| `roles/partner.md` | spec 10 (partner retention: dispatch outcomes, digests consumed, cross-pod blockers, open decisions awaiting the human), 12 |
| `roles/engineer.md` | spec 10 (engineer retention: paths, failing tests), 06 standing instructions, 09.4 for the subagent paragraph |
| `roles/reviewer.md` | spec 10 (the QA row: boundaries, correlation ids, per-case results) |

`BASE.md` carries, as gtm-1 item 1 requires: the full step-state schema with a rule per field;
the keep / collapse / discard / keep-file-facts / evict-in-order rules; the evidence rule
(`verified: true` only with citable seqs; a `compact` summary is unverified); "agent signals
lead"; boundary records are not work; **the seam marker rule** (main stream only, all four
conditions, idempotent, never for a subagent, and the `log`-hook threshold as the other,
harder trigger); the closed-stream digest; **the Digest rule with the blocker or the question
first** for `blocked`/`decision`; and **addendum absorption on resume** (fold into `goal` and
`constraints`, keep every closed step, decision, dead end and working-set entry; only
`hx dispatch` archives).

### 2. Templates — `src/hx/skeleton/templates/`

| File | Checked against |
|---|---|
| `work-item.md` | spec 06 (the ```markdown block), decision D9 in spec 14 |
| `order.md` | spec 06 "The order is a file" + "Definition of done"; `CONTRACTS.md` "Orders" |
| `addendum.md` | spec 06 resume row, spec 08 `hx resume` |

`work-item.md` is the spec 06 block with five substitution tokens — `{{id}}`, `{{pod}}`,
`{{after}}`, `{{dispatched}}`, `{{order}}` — now pinned in `CONTRACTS.md`. **The seven
`## Standing instructions` bullets are byte-identical to the spec block**, verified
programmatically (command below), not by eye, because D9 pins them.

`order.md` is a worked example, not a stub: frontmatter `after: [eng-000]`, `## Order` with the
context an agent would otherwise hunt for, a numbered `## Definition of done`, and a
`### Checks` block with three real commands. It parses with the build lane's
`hx.orders.parse_order`. `addendum.md` is prose with no `##` heading, because `hx resume`
appends it verbatim beneath `## Order`.

### 3. Identity and state documents

| File | Checked against |
|---|---|
| `skeleton/config/CLAUDE.md` | spec 17.5 (the four things it must say), 03, 02 Single-file context |
| `skeleton/PARTNER.md` | spec 03, 04, 12 |
| `skeleton/config/partner/AGENTS.md` | spec 01 Persona/Memory, 02 Identity, 03, 04, 12 |
| `skeleton/config/partner/SUBAGENTS.md` | spec 02 Identity, 09.1 `subagent-start`, 09.4 |
| `skeleton/config/partner/harness.json` | spec 05 (partner: `pod`/`role` `partner`, no `workdir`, no `branch`) |
| `skeleton/templates/worker/{AGENTS.md,SUBAGENTS.md,harness.json}` | spec 05, 02 Identity, 03, 11 Persona, 09.4 |

Each `AGENTS.md` has exactly one `## UPDATES BELOW ONLY`, real persona content above it (or
`run/<id>/persona.md` would be empty) and an explicit hand-off to the agent below it.

**The example worker moved** from `skeleton/config/eng-001/` to `skeleton/templates/worker/`
after the orchestrator's answer — see "Handoff entries" below. A fresh instance now installs
`partner` and nothing else, per spec 17.2 step 2 and `CONTRACTS.md` "Fresh instance contents".

### 4. Skills — `src/hx/skills/`

`hx-partner/SKILL.md` and `hx-worker/SKILL.md`, checked against spec 17.5, 12, 06, 08, 09, 07.

Skill format followed: **`code.claude.com/docs/en/skills`, read 2026-09-20.** What was
verified there and applied: the opening `---` must be the file's first line or the whole file
is treated as content; `name` is the display label and defaults to the directory name (the
invocation name always comes from the directory, so both frontmatter names match their
directories); `description` is what Claude uses to decide when to invoke, and `description` +
`when_to_use` are capped at **1536 characters combined**; `SKILL.md` should stay under 500
lines with reference material in sibling files. All four are asserted by the test.

`hx-partner` covers the order-file format and what makes a definition of done and a
`### Checks` block that hold; creating a worker from `templates/worker/`; `hx launch`,
`dispatch` with `after`, self-dispatch through `goal-pending`, `board`, `show`, `metrics`,
`read`, `resume`, `bench`, `restart`, `push`; the outcome table with the action for each;
resume-vs-bench; that the human never runs hx and that decisions go to chat.

`hx-worker` covers the lifecycle from the `/goal` pointer to `HX-COMPLETE`, in ten steps: the
boundary and its one Read, the goal pointer, `## Tasks` discipline, commit-as-you-go, one Read
per file, subagents and `SUBAGENTS.md`, seams, finishing, the `HX-CHECK-FAILED` loop as the
agent's own problem, and when `done` is not available.

### 5. Packaging units — `packaging/`

`launchd/com.hx.up.plist`, `launchd/com.hx.heartbeat.plist`, `systemd/hx-up.service`,
`systemd/hx-heartbeat.service`, `systemd/hx-heartbeat.timer`. Checked against spec 17.2 step 5,
17.4, 08 (`hx up`, `hx heartbeat`), 12 step 4.

Both platforms templated on `@HARNESS_ROOT@` and `@HX_BIN@` (`config/hx.json` `hx_bin`, per
`CONTRACTS.md`); no host path appears in any of them, which the test asserts. Heartbeat is 900 s
on both (`StartInterval` / `OnUnitActiveSec`). User agent and user unit deliberately, since hx
refuses root and Claude Code refuses bypass permissions under root. `hx-heartbeat.service`
carries no `[Install]` — the timer is what gets enabled. `hx-up.service` sets `KillMode=none`
so the tmux sessions outlive the oneshot unit.

### 6. `packaging/tested-claude-versions.json`

`{"versions": ["2.1.278"]}` — `claude --version` in this session is `2.1.278 (Claude Code)`,
normalised to the bare version per the orchestrator's answer and `CONTRACTS.md` "Claude Code
version strings".

### 7. Docs — `docs/`, `README.md`, `LICENSE`, `CHANGELOG.md`

| File | Checked against |
|---|---|
| `docs/deploy.md` | spec 17.2 step by step, 17.4, 17.6, 03 (the human's total command list) |
| `docs/two-worlds.md` | spec 17.3 table in prose, 11, 09.2 (the guard hook replacing the prompt), 08 (the dispatch wipe) |
| `README.md` | spec 01, 02, 06, 12, 17 |
| `LICENSE` | MIT, copyright `autodev-team` |
| `CHANGELOG.md` | Keep a Changelog; the pre-1.0 public surface stated at the top |

### 8. `docs/github-plan.md` and `.github/workflows/ci.yml`

The plan covers repository name and layout, the release process (`uv build`, `uv tool install`,
a `release.yml` that does **not** exist yet, PyPI Trusted Publisher), versioning policy, what
CI does and why the live suite stays out of it, the Claude Code pinning policy, contribution
posture, and what stays private. It ends with four open questions (below).

**No remote and no GitHub repository were created.** Nothing was pushed. The plan says so in
its first paragraph.

`ci.yml` job `test` runs on `ubuntu-latest` and `macos-latest` with tmux installed and Python
3.14, records a guard baseline from an empty `$RUNNER_TEMP/guard-home/.claude` and points the
test at it through `HX_USER_CLAUDE_HOME` / `HX_CLAUDE_HOME_BASELINE` (both already honoured by
`tests/guard/test_user_home_untouched.py`), runs `tests/guard` alone, then
`tools/milestone-check.sh`. Job `package` builds the wheel with `uv`, asserts it actually
contains the skeleton and skills package data `pyproject.toml` declares, and installs it to
confirm both entry points land on `PATH`.

### 9. `tests/packaging/test_skeleton_texts.py`

52 tests, everything gtm-1's "Done when" list names plus the spec-05 `harness.json` rules, the
fresh-instance rule, the worker-template rule, the `BASE.md` content rules, and the
no-host-paths rule on every unit file.

## How it was verified — exact commands and their last lines

```
$ tools/milestone-check.sh                              # guard, then the whole suite
......                                                                   [100%]
MC_EXIT=0

$ .venv/bin/python -m pytest tests/guard
5 passed in 0.44s

$ .venv/bin/python -m pytest tests/packaging
52 passed in 0.03s

$ .venv/bin/python -m pytest
437 passed, 1 skipped in 13.85s
```

Against the build lane's real code, not fixtures (ORCHESTRATION: "then switch to the real
commands"):

```
$ .venv/bin/python -m hx install --skeleton-only --root <scratch>/inst2
created  templates/worker/harness.json
created  .gitignore

$ HARNESS_ROOT=<scratch>/inst2 .venv/bin/python -m hx doctor
warn  repo          config/repo.json absent; `hx repo add <url|path>` mirrors the product repo (spec 17.2 step 4)
        # every `skeleton` line ok; `partner` is the only agent; the four warns are
        # config/claude.json, seed credentials, run/partner/home, config/repo.json — all
        # later-milestone work, none of them mine

$ hx.orders.parse_order(templates/order.md)
order OK: Order after= ['eng-000']
checks commands: '.venv/bin/python -m pytest tests/test_doctor.py -q\n…'

$ hx.config_harness.load_harness(config/partner/harness.json, root=…, models=…)
harness OK: partner claude-opus-5 partner None None
```

Standing instructions checked against the spec block programmatically rather than by eye:

```
$ .venv/bin/python  # bullets(spec/06-work-items.md ```markdown block) vs bullets(work-item.md)
7 7
SAME × 7
standing instructions: byte-identical to spec 06
```

The `~/.claude` guard passed at every step, including immediately before this file was written.

## Live vs. fake

Nothing in this goal runs a Claude Code agent, so none of these texts has been exercised by a
live agent yet — that is M2 (`config/CLAUDE.md`, the context file), M7 (the Companion prompts,
against the `hx metrics` seam metric), M8 (personas, `PARTNER.md`, the skills in a real Partner
loop), and M10 (the units, the wheel, `hx install`). They are prompts; they are tuned against
the M7 metric, not proven by a unit test.

**Verified live in this session:**

- `claude --version` → `2.1.278 (Claude Code)`. This is the entry in
  `packaging/tested-claude-versions.json`.
- The skill format, fetched live from `code.claude.com/docs/en/skills` on 2026-09-20. The
  findings applied are listed in §4 above.
- `plutil -lint` ran on both plists on this macOS (the test skips it elsewhere and falls back
  to `plistlib`, which runs everywhere).
- `hx install --skeleton-only`, `hx doctor`, `hx.orders.parse_order`, and
  `hx.config_harness.load_harness`, all against the build lane's committed code.

**Verified only against a parser, not a real runtime:**

- The systemd units parse as INI with the sections and keys systemd requires. No `systemd`
  was available to load them (`systemd-analyze verify` is Linux-only and there is no Linux
  runner here yet). CI's Linux job does not load them either — it runs pytest. **First real
  proof is M10 on a clean Linux user.**
- The launchd plists parse and lint but were never `launchctl bootstrap`ed. Same: M10.
- `.github/workflows/ci.yml` has never run. There is no remote and no GitHub repository, by
  design. Its YAML block scalars and heredoc indentation were checked structurally; the
  workflow itself is unproven until the repository is published.

## Open questions

1. **Does `spec/` ship?** `docs/github-plan.md` assumes yes and the README links it. It is a
   large, opinionated design document that will be read as a promise about what hx does.
2. **PyPI name.** Is `hx` available? The plan's fallback is `hx-harness` as the distribution
   name with the import package, CLI and repository all staying `hx`.
3. **`tools/` at publication.** `ci.yml` references `tools/claude-home-hash.sh` and
   `tools/milestone-check.sh`, but `tools/` is a build-process artifact excluded from the
   published repository. Move those two under `.github/scripts/`, or ship `tools/` whole?
4. **`autodev-team/hx`** assumes the org exists and that hx lives beside autodev rather than
   replacing it. The LICENSE copyright says `autodev-team`.
5. **The companion prompts are unmeasured.** `BASE.md` and the three role files are written to
   the spec's rules, but "does this context file actually let a fresh agent continue" is the
   M7 question. Expect to rewrite them against the metric; nothing here should be treated as
   settled prose.

## Handoff entries

**Read and applied before finishing** — `handoff/orchestrator-to-gtm.md`, marked `DONE
2026-09-20` in place. All three answers applied in commit `5605eeb`:

1. The example worker is **not installed**: `skeleton/config/eng-001/` → `skeleton/templates/
   worker/`, templated on `{{id}}`/`{{pod}}`, `workdir` relative. `hx-partner/SKILL.md` gained
   a "Creating a worker" step; `test_skeleton_texts.py` now asserts a fresh `config/` holds
   only `partner`.
2. Version strings are bare (`2.1.278`), already what I had shipped.
3. Placeholders unchanged and now pinned in `CONTRACTS.md`.

There were no other `handoff/*-to-gtm.md` entries at the start or the end of this goal.

**Written by me:**

- `handoff/gtm-to-build.md` — three items: (1) the `templates/work-item.md` placeholder tokens
  and their rendering rules, with a request to render the template rather than reconstruct the
  body in Python so the D9-pinned standing instructions stay byte-identical; (2) **a real bug
  request**: `config_harness.py:164` validates `workdir` with `Path(workdir).is_dir()`, which
  resolves against the *current working directory* — that cannot work for a skeleton copied
  into an arbitrary `HARNESS_ROOT`, and every `config/<id>/harness.json` the Partner creates
  from `templates/worker/` will carry a relative `workdir`, so it is the normal path, not an
  edge case. Asked for `(root / workdir).is_dir()` when relative, absolute behaviour unchanged;
  (3) what changes for `EXPECTED_SKELETON_FILES` now that no worker is installed, plus a note
  not to run `templates/worker/harness.json` through `validate_harness` (its `id` is the
  literal `{{id}}` on purpose). Also noted the bare-version rule for the spec 17.2 step 1
  membership check.
- `handoff/to-orchestrator.md` — the three gtm-1 questions, now answered and applied.

## Other lanes

`tools/milestone-check.sh` exits 0 as of this file. One transient failure was observed
mid-goal — `tests/core/test_adapter_start_sh.py::test_refuses_without_a_pinned_binary`, a
build-lane path — during one run and was green on the next two; the build lane was committing
at the time. Not reported as a defect and not fixed by me; flagged here only so the
orchestrator knows it was seen.

`tests/core/`, `tests/fakeclaude/`, `tests/ui/`, `src/hx/*.py`, `src/hx/ui/`,
`src/hx/skeleton/adapters/`, and `src/hx/skeleton/config/models.json` are other lanes' paths
and were not touched. My commits use explicit paths only; `git add -A` was never run.

## Commits

```
fa9126d gtm: handoffs for goal gtm-1
362a1d3 gtm: tests for the shipped texts, and CI on macOS and Linux
2141556 gtm: README, LICENSE, changelog, and the three docs
5605eeb gtm: the example worker becomes templates/worker/, not an installed agent
ba63848 gtm: boot and heartbeat units, and the tested Claude Code version list
949ae0d gtm: hx-partner and hx-worker skills
68fc00f gtm: instance skeleton texts — companion prompts, templates, identities
```
