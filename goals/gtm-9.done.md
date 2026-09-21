# gtm-9 done — the cut, and the operator's view after M5–M6

Every text is written to the cut spec (D25), not to the code.

## The cut (step 0)

Unit templates, the mirror/worktree/units/push/upgrade steps of the deploy proof, and
`tests/packaging/test_units.py` are gone. The skills and every doc stopped mentioning `after`,
queued items, Partner orders and self-dispatch, guard rules, `hx push`, `hx upgrade`, units,
mirrors, worktrees and `--require-done`. `templates/work-item.md` lost `{{after}}`;
`templates/order.md` has no frontmatter. CI lost the `systemd accepts the rendered units` step,
which would have passed by skipping on any runner without `systemd-analyze`.

Both scenario packs are rewritten: no `orders/partner.md`, no Partner row, no `after` chain.
m8 went from eight observation points to seven and m8b from six to four, and **every
`expected/*.txt` was regenerated from a real `hx board`** rather than edited. The dependency in
m8 is now the Partner's to keep by waiting for a completion wake, which is what the sequence is
testing.

## Built

- `docs/operating.md` and `docs/getting-started.md` wired in; `docs/deploy.md` re-captured from
  a real `hx install` — four steps, not six.
- `hx-partner`, `hx-worker`, and the new Partner-only `hx-fleet` (creating, changing and
  retiring HarnessAgents). The personas and Companion role files the orchestrator wrote are
  wired in unedited; `config/partner/AGENTS.md` is now a byte-identical copy of
  `personas/partner/AGENTS.md`, and a test keeps them equal.
- `companion/BASE.md`: the two-tool rule is stated as a rule the Companion keeps, not a fence —
  its home has one Stop hook, no guard hook and bypass permissions, so a third tool call would
  simply work. Plus build-6's one-delivery-path mechanics. The role files are the
  orchestrator's text and were left alone.
- `CHANGELOG.md` gains M5, M6, and a **Removed** section recording the whole D25 cut.
- `tests/packaging`: the doctor block still matches (25 rows); `docs/operating.md` must quote
  spec 06's `/goal` pointer **verbatim**, with the test taking the line out of the spec rather
  than retyping it; `docs/getting-started.md` is held to the flags, exit code, tested version
  and wheel name the CLI really has.

## Found and fixed

The fake claude wrote `run/<id>/fake-argv.json` from both the main session and its Companion,
which launch into the same directory milliseconds apart. The interleaved writes left invalid
JSON and failed step 12 of the deploy proof. Fixed in build's tree (`HX_ROLE=companion` writes
its own name, atomically) and reported — every existing reader wanted the main session's record
and was racing for it.

## Tests

`tools/milestone-check.sh gtm` **passes**: `tests/guard`, `tests/packaging` and
`tests/scenario`, 120 in the lane's own paths, plus both e2e scripts end to end.

It first stopped at `tests/guard/test_user_home_untouched.py` on three files the Claude Code
client itself wrote into `~/.claude` at 20:44 — `remote-settings.json`, `policy-limits.json`
and its stamp, all server-pushed account settings. Nothing in hx names them, and both e2e
scripts still prove `~/.claude` byte-identical across a full install and launch. The test's
docstring says to report rather than exclude, so I did; the orchestrator excluded them from the
manifest and re-recorded the baseline.

## Handoffs

`handoff/gtm-to-build.md`: `EXPECTED_SKELETON_FILES` names one persona and one Companion role
file of four each, so an instance missing a worker role reports a healthy skeleton and then
fails at `hx launch`; the fakeclaude race, fixed in place; and `docs/getting-started.md`
describing a UI step `hx install` does not have yet (spec 16's, the UI lane's).

`m8/README.md` records one open question for the spec author: the `repo/.claude/` tripwire lost
its defence when D25 cut sparse worktrees, and nothing now keeps a `.claude/` in a worker's
workdir from loading.
