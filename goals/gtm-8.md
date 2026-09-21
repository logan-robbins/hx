# gtm-8: the operator's view after M5–M6, and the texts checked against seams for real

Sent after build-7 (M6) lands. Read `goals/build-6.done.md`, `goals/build-7.done.md`,
`handoff/orchestrator-to-gtm.md`, any other `handoff/*-to-gtm.md`, spec 02 (Seams), 07, 10,
12, 16.

## Build

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
- Committed path-scoped. `goals/gtm-8.done.md` written, with handoffs.
