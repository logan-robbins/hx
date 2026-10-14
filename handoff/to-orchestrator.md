# Graph validation — 2026-10-12

The default `tools/milestone-check.sh` stops at
`test_user_claude_home_matches_pre_build_baseline`. The repository's preserved
`.baseline/claude-home.manifest` was recorded on 2026-09-21. Its eight differing
configuration paths all have modification times before this goal began at
2026-10-12 20:17:59 America/Los_Angeles. The graph implementation did not reset
that baseline, alter the guard exclusions, or write the user's Claude home.

The mismatches are `settings.json`, `mcp-needs-auth-cache.json`,
`state/mcp-discover-verdicts.json`, `plugins/plugin-directory-cache-v2.json`,
`plugins/.last_inuse_sweep`, `plugins/installed_plugins.json`,
`plugins/known_marketplaces.json`, and a feedback draft dated 2026-09-28.
The packaging installation/deployment scenarios independently hash the actual
user home before and after their runs and pass that unchanged-home check.
The other four guard tests pass. The default persisted-baseline check remains
an environmental failure and is not reported as green.

Broad core testing exposed an existing fake-version mismatch and a hook-test
stub that wrote through an installed symlink. The fixture now records the
selected simulator's supported reported version, and the hook test replaces
its scratch symlink before writing. The graph routing text also fits the
existing compaction packet budget. These repairs preserve production version
checks and context limits.

Implementation and measurements are documented in `docs/system-design.md`,
`docs/search-and-retrieval.md`, `README.md`, and `CONTRACTS.md`. Optional
compiler-backed checks skip with named reasons when their prerequisites are
absent. No live fleet promotion wave has been run; promotion effectiveness
remains unmeasured, and clustering proposals remain hypotheses.

## Verified regression chunks

- Core and fake harness: 1,527 passed, 14 skipped.
- UI: 399 passed, one skipped.
- Packaging and scenarios: 166 passed.
- Final graph/map/planning/completion integration: 137 passed, three optional
  adapter checks skipped.
- Other guard checks: four passed; default saved-home guard has the pre-existing
  mismatch described above. Optional live suite: 11 skipped by its environment gates.

The complete `tools/milestone-check.sh` run passes with
`HX_CLAUDE_HOME_BASELINE` pointing to a separate temporary manifest captured
immediately before the run: **2,103 passed, 26 skipped**, plus its initial guard
pass. This uses the guard's supported per-run checkpoint and includes every test;
the repository's September baseline and the user's home remain untouched.
The default dated-baseline failure remains explicitly reported above.

The full run also covers the final additions after the earlier core chunk:
SCIP relationship/enclosing-symbol ingestion, committed C# map anchors, and
completion refresh replay after interrupted derived publication. The one warning
is chromadb's existing Python 3.16 deprecation notice, not a graph failure.
