# Handoff: orchestrator → gtm

## 2026-09-20 — answers to your gtm-1 questions (all three)

> gtm, DONE 2026-09-20: all three applied. Example worker moved to
> `src/hx/skeleton/templates/worker/` (templated on `{{id}}`/`{{pod}}`); versions are bare in
> `packaging/tested-claude-versions.json`; placeholders unchanged and now pinned in
> `CONTRACTS.md`. `hx-partner/SKILL.md` gained a "Creating a worker" step, and
> `tests/packaging/test_skeleton_texts.py` asserts a fresh `config/` holds only `partner`.
> Build told in `handoff/gtm-to-build.md`.

1. **Example worker: not installed.** Spec 17.2 is authoritative. Move
   `src/hx/skeleton/config/eng-001/` to `src/hx/skeleton/templates/worker/` (same three files),
   so a fresh instance has `templates/worker/` for the Partner to copy and no `config/eng-001/`.
   Keep `workdir` relative (`wt/{{id}}` or a note that the Partner fills it in). Update your
   skeleton test and the `hx-partner` skill's "create a worker" step to say: copy
   `templates/worker/` to `config/<id>/`, edit the persona, then `hx launch <id>`. Tell build in
   `handoff/gtm-to-build.md` that `EXPECTED_SKELETON_FILES` changes accordingly.
2. **Version strings: bare.** `2.1.278` in both files, now pinned in CONTRACTS.md.
3. **Placeholders: pinned in CONTRACTS.md** exactly as you defined them. Thank you for
   checking the standing instructions programmatically.

## 2026-09-20 — answers to the five open questions in `goals/gtm-1.done.md`

1. **`spec/` ships.** It is the design document and the reason the code looks the way it does.
   README should call it what it is: the spec the code is built to, not a feature list.
2. **PyPI name.** Plan for the distribution name `hx-harness` with import package, CLI, and
   repository all `hx`, exactly your fallback. Do not check or reserve anything on PyPI; nothing
   external happens until the human says so.
3. **`tools/` ships whole.** It is not a build artifact: the guard tests, `milestone-check.sh`,
   and CI depend on it. Update `docs/github-plan.md` accordingly; leave `ci.yml` pointing at
   `tools/`.
4. **Org and name.** `autodev-team/hx` stays as the placeholder. That is the human's decision;
   I am flagging it to them. LICENSE copyright stays `autodev-team`.
5. **Companion prompts unmeasured.** Agreed. M7 rewrites them against the metric.

## 2026-09-20 — answers to the open questions in `goals/gtm-2.done.md`

> gtm, DONE 2026-09-20: 4 applied — `docs/github-plan.md` §1 now says `spec/` holds only the
> sections and the compiled file and that `notes/` does not ship, §6 explains why
> `AUTODEV-COMPARISON.md` is excluded rather than left as a judgement call, and §7 is down to
> the two decisions that are the human's. 1–3 noted, nothing to do. 5 (pin the ui static files
> in the e2e check) is gtm-3's, per your note. Ownership of `src/hx/packaging/**` and
> `tests/scenario/**` and the no-container-runtime constraint are noted for gtm-3.

1–3. Human's decisions; nothing to do. Correct that CONTRIBUTING and templates land with the
   publishing change.
4. **`AUTODEV-COMPARISON.md` does not ship.** Moved to `notes/` (orchestrator-owned, listed under
   "what stays private" in `github-plan.md`; update that section). `spec/` is now only the
   spec sections and the compiled file.
5. Pin the ui static files in the e2e check after ui-2 lands (`goals/ui-2.done.md`); goal gtm-3
   says when.

Ownership update: `src/hx/packaging/**` and `tests/scenario/**` are yours (ORCHESTRATION.md).
There is no container runtime on this machine (no docker, podman, colima or lima), so the
Linux proof of the systemd units waits for CI or a Linux box; do not try to install one.

## 2026-09-20 — answers to your three gtm-3 handoffs

> gtm, DONE 2026-09-20: all four noted; nothing in the pack had to move, since every answer
> went the way it had assumed. `tests/scenario/m8/README.md` now records them as settled
> rather than open — A1 and A2 are confirmed, and the bench-after-complete order it already
> follows is the spec's as of your rewording. The eight `expected/` board files are unchanged
> and still match the real `hx board`.

1. **Bench ordering: option 1.** Spec 12 step 5 now says read the digest and update `PARTNER.md`
   but do not bench; step 8 completes the Partner's own item and then benches the plan's
   workers. `require_done` keeps reading the board. Your pack's order (complete first, bench
   second) is the spec's now.
2. **Goal marker on deferral: written.** `hx goal` writes `run/<id>/goal` in both the paste and
   the `goal-pending` case (spec 08 `hx goal` row). Assumption A1 holds.
3. **Benched item keeps its outcome: intended**, now stated in the `hx bench` row. A2 holds.
4. **Reconciling against the shared tree** rather than the done-file marker was right.

## 2026-09-20 — answers to the three open questions in `goals/gtm-3.done.md`

1. Scripted `decision` is right for M8. An unscripted second scenario is gtm-4 item 3, small.
2. `docs/companion-eval.md` gets a pass when `hx metrics` lands; noted in that build goal.
3. One pod is fine.

Plan change that affects you: `hx install` full, `hx repo add`, the sparse worktree, `hx push`,
`hx upgrade` and unit rendering move forward to **build-4** (they were "build-11"); M6 live
tests and your M10 proof both need them. gtm-4 writes the deploy proof script against the
`--from-user-config` and unit-rendering contracts you already published, gated on those commands
existing.

## 2026-09-20 — answers to the three open questions in `goals/gtm-4.done.md`

1. m8b: leave it; the first live run (M8) decides, and rebalancing the two halves is the fix.
2. Tighten the bypass-acceptance merge assertion in gtm-5, after build-4 lands.
3. Units on Linux: CI job or a Linux box; not this machine. Nothing to do now.

## 2026-09-20 — answers to `goals/gtm-5.done.md`

1. Checked on the next live run: build-5's live check reports the boundary read form.
2. Resolved: the boundary read comes first even when the first turn is a question; the persona
   costs no read (spec 13 M2 as reworded). Your "before anything else" wording is right as is.
3. `BASE.md`'s waste-recording rule is the shape M7 wants; the build lane reads it at build-8.
gtm-6 (deploy proof) goes out when build-4 lands.

## 2026-09-20 — branch name and the CI trap (your gtm-6 handoffs)

1. `agent/<id>` is the agent branch everywhere. Build changes the `repo.py` fallback; your
   template, docs, and the spec already say it. `goals/gtm-6.md` step 1 corrected.
2. CI fix is right. `tools/milestone-check.sh` with no argument now means "everything
   required", so the no-lane form is safe again; keep CI on plain pytest anyway.
