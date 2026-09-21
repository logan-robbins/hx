# gtm-8: the `hx-companion` skill and the Companion's system prompt for a tmux session

Immediate. Read spec 02 ("Model calls"), 05 (Companion paragraph), 08 (`hx companion`,
`hx wake companion`, `hx flush`), 10 (whole, rewritten today), 07.2, CONTRACTS.md ("The
Companion is a tmux session"), `handoff/orchestrator-to-gtm.md`.

The Companion is no longer a headless call: it is a Claude Code session in window
`<id>:companion`, woken by `/clear` and one fixed pointer to a pass file, and it writes its
answer with its Write tool. Its behaviour therefore lives in two texts you own.

## Author

1. `src/hx/skills/hx-companion/SKILL.md`: what one pass is (read the pass file; read the state
   file if present and the log from `from_seq`; apply BASE.md and the role rules; write exactly
   one JSON object matching spec 07.2 to the `write` path; touch nothing else; say nothing in
   chat beyond one line); what `retry_reason` means; that it must never read the agent's files,
   the work item, or anything not named in the pass; that it uses only Read and Write.
2. `src/hx/skeleton/companion/BASE.md`: rewrite the output-contract section for a file write
   (the object goes to the `write` path, not to stdout); keep every rule; add the seam-policy
   inputs it must read from the pass (hx puts `context_tokens`, `last_seam_ts`, `open_subagents`
   in the pass file: propose the exact keys in `handoff/gtm-to-build.md` and use them).
3. `src/hx/skeleton/companion/roles/*.md`: unchanged unless the new shape needs it.
4. `tests/packaging/test_skeleton_texts.py`: the new skill has valid frontmatter, names Read and
   Write as the only tools, quotes the pointer text from CONTRACTS.md verbatim; BASE.md names the
   `write` path rule.

## Done when

- `tools/milestone-check.sh gtm` passes.
- Committed path-scoped. `goals/gtm-8.done.md` written (short).
