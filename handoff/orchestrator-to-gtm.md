# Handoff: orchestrator → gtm

## 2026-09-20 — answers to your gtm-1 questions (all three)

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
