# gtm-9: the operator's view after M5–M6, and the texts checked against seams for real

Sent now, in parallel with build-7 (the build lane is deleting the cut code). Write every text to
the cut spec (`spec/HARNESS_SPEC.md`, 857 lines, D25 in spec 14), not to the code; where the
code still has a cut feature, the spec wins and build-7 removes it. README.md first: it is the
document a reader opens, and it must describe the system as the spec now defines it: the human
talks to the Partner in tmux; the Partner writes order files and dispatches workers when it
decides; each worker is a Claude Code session with its own home, persona, Companion, and work
item `pods/<pod>/<id>-<state>.md`; seams replace compaction; `hx complete` runs the checks;
deployment is `hx install`. No mention of anything in D25's cut list. Read `goals/build-6.done.md`, `goals/build-7.done.md`,
`handoff/orchestrator-to-gtm.md`, any other `handoff/*-to-gtm.md`, spec 02 (Seams), 07, 10,
12, 16.

## Build

0. **The v1 cut first** (spec 14 D25, ORCHESTRATION.md): delete `src/hx/packaging/` unit
   templates, `packaging/e2e-deploy.sh`'s mirror/worktree/units/push/upgrade steps (keep the
   install-with-token proof), and their tests; the skills and docs stop mentioning `after`,
   queued items, Partner orders or self-dispatch, guard rules, `hx push`, `hx upgrade`, units,
   mirrors, worktrees, `--require-done`; the Partner gets its goal from the human in chat; work
   items stay `pods/<pod>/<id>-<state>.md`; `templates/work-item.md` drops `{{after}}`; `templates/order.md` has no frontmatter. The M8 and m8b
   packs: no `after`, no `orders/partner.md`, expected boards regenerated from the cut `hx board`
   (Partner not a row), README sequence per the rewritten spec 12 and 13 M8.

1. `docs/operating.md`: what the human sees and does day to day, in the spec's terms: talk to
   the Partner in `tmux attach -t partner`; what a `decision` looks like in chat and how the
   answer becomes an addendum; what the heartbeat does; what `hx board` errors mean; when to
   look at the UI; what a seam looks like in a pane (the `/clear`, the one Read); how to stop
   everything (`hx down` if the build lane ships it, else the unit commands). Every command and
   output copied from a real instance.
2. `src/hx/skills/hx-partner/SKILL.md` and `hx-worker/SKILL.md`: seams and the Companion as the
   agent experiences them (the boundary, the context file's sections, why not to re-read
   working-set files, that compaction is off), checked against build-7's live transcript
   excerpts; the `hx seam`/`goal-pending` behaviours as they really are.
3. `src/hx/skeleton/companion/BASE.md` and `roles/*.md`: apply whatever build-6's live Companion
   calls showed (`handoff/build-to-gtm.md`): output failures, budget overshoots, fields the model
   left empty. Keep the output contract strict.
4. `README.md`, `CHANGELOG.md`, `docs/deploy.md` for M5–M6 (the Companion window, `seed/token`
   used twice, `hx upgrade`'s live-suite gate).
5. `tests/packaging`: the doctor-output assertion still holds on a fully installed instance
   (rerun); a new assertion that `docs/operating.md` quotes the exact `/goal` pointer text from
   `spec/06-work-items.md`.

## Done when

- `tools/milestone-check.sh gtm` passes.
- Committed path-scoped. `goals/gtm-9.done.md` written, with handoffs.
