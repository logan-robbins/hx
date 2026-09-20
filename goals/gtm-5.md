# gtm-5: the boundary read in `config/CLAUDE.md` and the skills, from the first live run

Small and immediate. Read `goals/build-3.done.md` ("What the live check did not establish"),
`handoff/build-to-gtm.md` (their entry on the double read), spec 09.1 (the hook line now names
the Read tool), 13 M2 (reworded), 07.3.

## Change

1. `src/hx/skeleton/config/CLAUDE.md`: the boundary rule says, in one short paragraph, that the
   context file named by the SessionStart line is read with the Read tool, exactly once, before
   anything else, never with `cat`, `head`, or any Bash command, and never re-read in the same
   turn; that the persona is already present and needs no file read; and that files listed in
   the context file's working set are not re-read unless the file changed. Keep the whole file
   short; this is system-prompt-adjacent text loaded every turn.
2. `src/hx/skills/hx-worker/SKILL.md` and `hx-partner/SKILL.md`: the same rule in the lifecycle
   step about boundaries, and the exact hook line quoted from spec 09.1.
3. `src/hx/skeleton/companion/BASE.md`: the seam metric counts Read-tool calls; a `Bash cat` of
   a working-set file is recorded as a dead-end-class waste in step state (so M7 sees it).
4. `tests/packaging/test_skeleton_texts.py`: assert the CLAUDE.md paragraph names the Read tool
   and forbids `cat`; assert both skills quote the 09.1 line verbatim from `spec/09-hooks.md`.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard`, `tests/packaging`, `tests/scenario`.
- Committed path-scoped. `goals/gtm-5.done.md` written.
