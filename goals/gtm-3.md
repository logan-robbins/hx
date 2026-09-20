# gtm-3: the M8 scenario pack, skills reconciled with the real CLI, and the Companion eval plan

Read `goals/gtm-2.done.md` (yours), `handoff/orchestrator-to-gtm.md` (answers to your five
open questions and an ownership update), any other `handoff/*-to-gtm.md`, spec 12, 13 (M7,
M8), 06, 10, 08. `tests/scenario/**` is yours as of today.

## Build

1. **The M8 scenario pack**, `tests/scenario/m8/`: the concrete data the build lane will run
   spec 13 M8 with, so the end-to-end test is written against real files and not invented at
   the last minute. Contents: `chat.md` (what the human types to the Partner, turn by turn, and
   what a correct Partner reply must contain); `orders/partner.md` (the Partner's own order as
   it should write it from that chat, checks = `hx board --require-done eng-001 eng-002`);
   `orders/eng-001.md` and `orders/eng-002.md` (`eng-002` has `after: [eng-001]`; both
   `### Checks` blocks run real commands against a tiny fixture repo you also ship at
   `tests/scenario/m8/repo/` with a `CLAUDE.md` that must never load); `orders/eng-002.addendum.md`
   (the human's answer to the `decision` outcome eng-002 must reach first); `expected/` with the
   `hx board` text expected after each step of spec 12 (dispatch, eng-001 done, eng-002
   decision, resume, all done, bench); a `README.md` walking the sequence step by step with
   the exact hx commands the Partner runs and the state transitions of spec 06 each causes.
   Every order must pass `hx.orders.parse_order`; every AGENTS.md you add for `eng-001` and
   `eng-002` (under `tests/scenario/m8/config/`) must have the header. A test in
   `tests/scenario/test_m8_pack.py` asserts all of that plus that the `after` graph is acyclic
   and that `expected/` covers every step in the README.
2. **Skills and docs reconciled with the real CLI.** If `goals/build-2.done.md` exists when you
   reach this step, run every command the `hx-partner` and `hx-worker` skills name against a
   scratch instance (`hx install --skeleton-only`, then the M8 pack) and fix every discrepancy
   in the skills and `docs/deploy.md`: flags, output lines (`HX-COMPLETE`, `HX-CHECK-FAILED`),
   file names, the exact `/goal` pointer text. Record each command's real output in the done
   file. If build-2 has not landed, do the same against build-1's commands only (`install`,
   `doctor`, `board`) and say what remains for gtm-4.
3. **Pin the ui static files** in `packaging/e2e-install.sh` step 4 if `goals/ui-2.done.md`
   exists; otherwise leave the note in `handoff/gtm-to-ui.md` and move on.
4. **`docs/companion-eval.md`**: the M7 plan in concrete terms for whoever builds it: the
   recorded-log corpus (where it comes from, how it is captured from M6 runs), the five seam
   points per task, the two metrics spec 13 M7 names and how `hx metrics` records them, the
   pass bar, and how a prompt change to `companion/BASE.md` or a role file is judged (before
   and after on the same corpus). No code.
5. `docs/github-plan.md`: move `notes/` and `AUTODEV-COMPARISON.md` to "what stays private";
   `spec/` is only the spec.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard`, `tests/packaging`, `tests/scenario`.
- Every order in the pack parses; the README's sequence and `expected/` agree.
- Committed path-scoped. `goals/gtm-3.done.md` written, with handoffs (the build lane needs to
  know the pack exists and what M8 should run: write `handoff/gtm-to-build.md`).
