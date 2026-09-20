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
