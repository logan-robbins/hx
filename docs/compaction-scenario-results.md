# Small-app live run: results and self-evaluation

Run: `tests/live/test_small_app.py` on 2026-10-12, three Sonnet 5.5 workers
(medium effort) on a task-board app, contract published first, instance
model thresholds rewritten to 5,000 tokens. Preserved instance:
`/tmp/hx-app-basetemp/test_three_lane_task_board0/instance`.

## What happened

- All three lanes finished in about 20 seconds of wall time, in parallel:
  frontend (`index.html`, `app.js`, `check.sh`), API (`api.py`,
  `test_api.py`), backend (`store.py`, `test_store.py`).
- The test itself, not the agents, re-ran every lane's checks: all pass.
- Every lane has exactly two commits: the published contract, then its own
  work. No lane waited on another; the frontend fetched the contract paths
  from the stub, as instructed.
- Workers followed the standing instructions: they built, ran their
  checks, committed, and did not run `hx complete` (the goals forbade it
  in this harness shape).

## Compaction and preservation — honest limits

- Context packets are 3.8 KB each and no seam fired: the tasks were too
  small to reach even a 5k seam in anger. **Compaction effectiveness is
  therefore not proven by this run.** The fixture tests prove the packet
  and threshold contracts; a longer build (or a forced seam mid-task) is
  still needed to prove reshaping under pressure.
- Companions did run: a first pass exists per worker and recorded
  `context_tokens: 33674` at pass time — most of a small worker's context
  is invariant overhead (system prompt, tools, persona), which is exactly
  why the invariant budget and cache discipline matter more than packet
  trimming at this scale.
- Tool noise is visible in transcripts (ToolSearch/Skill/Workflow entries
  alongside 5 Bash and 3 Read calls for a two-file task): harness overhead
  is a real share of a small task's context and belongs in the same audit.

## How well did we (this session) follow our own rules?

- Search before build: followed for design (both codebases read first,
  targeted search throughout, no full-repo scans).
- Write small, test early: **violated, then corrected.** Subagents wrote
  whole modules before tests; later work (discipline, prompt cache,
  learnings, planning) was test-first with watched failures.
- Test kinds: unit and module tests per change, broad core suite in chunks
  (1,270 passed), required lanes run; the user-home guard failure is
  pre-existing drift, left untouched.
- Commit discipline: the branch is still one uncommitted diff, and
  `hx discipline commit` correctly says `split`. Landing per module
  remains the outstanding act.
- Live claims: Jev verified live repeatedly; Anthropic verified live via
  the OAuth seed token; the earlier 401 was an API-key/OAuth mix-up, now
  understood and documented. Haiku 5.5 is configured but the installed
  CLI 2.1.289 calls it unrecognized — config-verified, not live-verified.

## Rerunnable runner (added 2026-10-12; no run recorded yet)

`HX_LIVE=1 .venv/bin/python -m pytest tests/live/test_compaction_scenario.py` reruns this
scenario with the two changes the small-app run showed were missing: goals of four or five
committed units per lane, so a lane's context crosses the 5,000-token threshold, and one
forced `hx seam <id>` per lane once its first unit has a file, so a post-seam window exists
even when no threshold seam fires. Companions run on Haiku at low effort. Each lane ends with
`hx complete done` against its goal's `### Checks`, and the test re-runs those checks itself.
The runner reads every number from `hx metrics <id>` (`packet_bytes`, `next_10_turns`,
`searches_before_first_edit`, `modules_read_before_first_write`, `task_reprints`) and appends
one `## Compaction scenario run <ts>` block below this section *before* it asserts, so a
failing run is recorded too. A seam window without a parseable transcript counts as
unmeasured and fails the waste criterion; it is never estimated. The first block appended
here is the baseline. It has not been run yet: it was written without live credentials, so
there is no live result to report.
