# Compaction scenario: three lanes, 5k seams, reshaped context

The end-to-end test of whether context reshaping works. It needs valid
native credentials (a working Anthropic key or OAuth seed token); the
fixture-level contracts are already proven in
`tests/core/test_compaction_reshape.py`. Run it live when credentials work,
never report the fixture version as the live result.

## Setup

- One instance, three workers (`fe-001`, `api-001`, `be-001`) plus Partner.
- `config/models.json` for the test models: `threshold: 5000`,
  `autocompact_window: 8000`, so seams fire constantly and compaction
  pressure is the norm, not the exception.
- Companion per worker: Haiku (`claude-haiku-5-5`), effort `low`,
  8,192 max output tokens, `cache_ttl: "1h"`. The companion extracts state;
  it does not do the task, so worker effort would be wasted on it.

## The build

A small task-board app (the popular-app shape that divides cleanly):

1. **Contract unit first** (Partner, wave 1): the API contract — routes,
   request/response shapes, error format — published as map interface
   records plus a stub server that returns fixture data. Frontend and
   backend both stub against it; nobody waits for the real API.
2. **Three lanes** (wave 2, parallel, disjoint write scopes): frontend
   (list/board UI against the stub), API (real endpoints against the
   contract tests), backend (storage + domain rules). Each lane commits
   per behaviour unit with its tier tests green.
3. **Integration unit** (wave 3): frontend switched from stub to real API,
   contract tests run against the integrated tree.

## What to measure after each compaction

- The restored packet: does it carry goal, constraints, cursor, next
  action, and unresolved failures — and nothing obsolete? (Packet bytes
  charged vs. the 5k threshold.)
- `hx metrics <id>`: tool calls in the 10 turns after each seam, split
  into re-reads of files already in the working set (waste) versus new
  work. Reshaping is effective when waste reads trend to zero.
- Searches before first edit after a seam: should be zero for facts the
  packet was supposed to carry; each one is a packet defect to record.
- Cache behaviour: seams that land cold (`hx cache assess`) restructure
  freely; warm seams keep the invariant prefix byte-stable.
- Lane independence: no lane blocked on another lane's unfinished work
  before the integration wave; blocks that do occur name the contract,
  not a person.

## Pass criteria

All lanes complete through checked receipts, integration checks green,
zero goal restatements by any worker after a seam, and waste re-reads
under 10% of post-seam tool calls by the final wave.
