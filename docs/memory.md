# Episode memory

Every pass the Companion writes is a compaction of a slice of one agent's conversation. hx used
to throw each one away as soon as the next pass overwrote `state/<id>/<stream>.json`. Episode
memory keeps them: one searchable collection per instance, holding what every agent worked out,
so the fourth agent to hit the same wall reads the first one's dead ends instead of rediscovering
them.

It is an accelerator and never a precondition. Nothing in hx fails, blocks or slows down because
memory is broken, empty, or not installed.

## What is in it

One **episode** per boundary, four kinds:

| kind | written at | document |
|---|---|---|
| `pass` | `companion.ingest`, after the state is installed | the step state, rendered |
| `seam` | `seam.seam`, after the seam record | the main step state at the cut |
| `compact` | the `postcompact` hook | the step state plus what Claude Code kept |
| `complete` | `complete.complete`, after the Digest is written | the Digest |

The document is `memory.render_episode(state)` — deliberately not the markdown of the context
file. It is a retrieval chunk: goal, decisions with their why, closed steps with verified/commit,
dead ends, the working set's files and notes, the last failure, the hypothesis, blockers. One
line each, no headings, nothing decorative.

The metadata is flat scalars, because that is all chroma stores:

```
ts              2026-09-21T12:05:05Z      (hx.timestamps.now())
t               1789992305.116019         (unix float; the recency weight is computed from it)
id              eng-001                   the agent
pod             engineers
role            backend-engineer          the persona; the default search filter
stream          eng-001-main
kind            pass | seam | compact | complete
seq             41                        the stream cursor
outcome         done                      `complete` only, otherwise ""
prompt_version  a1b2c3d4/e5f6a7b8         `{base}/{role}` shas, flattened
```

The episode id is `<id>/<stream>/<kind>/<seq>`, and indexing upserts, so re-indexing a boundary
is free and never duplicates.

A `pass` whose document is byte-identical to the previous pass on the same stream is dropped
(the sha lives in `state/memory/last/<id>-<stream>.sha256`). A Companion woken on every turn
produces a great many states that differ by nothing a search can use, and indexing each one
fills the collection with near-duplicates that crowd out everything else.

## Two halves, and why

**Writing** is a hook. `memory.enqueue_quietly` writes one small JSON file to
`state/memory/queue/<uuid>.json` with an atomic write and returns. No chromadb import, no lock,
no embedding, no network — an import that costs a second would cost a second on every turn of
every agent. Anything that goes wrong is logged to `logs/<id>/hook-errors.log` as a `memory:`
line and never propagates.

**Indexing and reading** happen later, in `hx memory index`, `hx memory search`, `hx memory
list`, `hx memory stats` and the compose injection. Each drains the queue into chroma first, and
every chroma open — reads included — is wrapped in an exclusive `fcntl.flock` on
`state/memory/index.lock`. Several agents' processes share one `PersistentClient` directory and
chroma is not multi-process safe without it.

`import chromadb` is lazy and its failure is a first-class outcome: `hx memory` prints one line
to stderr and exits 2, and `hx compose` puts `_memory unavailable: <reason>_` in the section and
composes everything else exactly as before.

## Ranking

```
similarity     = 1 - cosine_distance
recency_weight = 0.5 + 0.5 * 0.5 ** (age_hours / half_life_hours)      # half life 24h
score          = similarity * recency_weight
```

Chroma does the ANN fetch (`max(4k, 20)`, bounded by the collection count) with the metadata
filter; the re-rank is in Python, because chroma has no notion of the age of a thing. Recency can
at most halve a score and never inverts a large similarity gap — a six-week-old episode about the
same file is usually worse advice than yesterday's, but a much better match still wins.

## The CLI

```
hx memory search QUERY [--role R] [--pod P] [--id ID] [--kind K] [--k N]
                       [--all-roles] [--half-life-h H] [--json]
hx memory index
hx memory list [--id ID] [--role R] [--kind K] [--limit N] [--json]
hx memory stats [--json]
```

`search` filters by the caller's own persona by default: `start.sh` exports `HX_ROLE` and
`HARNESS_ID` onto an agent's session, so an agent searches its own role's episodes and a human at
a shell searches all of them. `--all-roles` drops the filter, and is what to reach for when the
own-role result is empty or clearly about a different area.

Exact output shapes are in `CONTRACTS.md`.

## In the context file

`hx compose` adds a **Memory episodes** section directly after **Step state**, on main and
subagent streams (subagents get half the `k`). The query is built from this stream's own step
state — goal, every open step's intent and next, the hypothesis — falling back to the goal text
before the Companion has written anything. The agent's own id is excluded: its own state is the
section above, and seeing it again as a "memory" is noise at best. With fewer than two hits under
the role filter the search is repeated across all roles, because a thin own-role result is
exactly when another persona's episode is worth reading.

```
## Memory episodes

_source: `state/memory/chroma`_

Search more: `hx memory search "make the CSV importer stream" --all-roles`

- 2026-09-21T12:05:05Z eng-002/seam s=0.69: goal: make the CSV importer stream…
```

Three `harness.json` `companion` fields control it:

| field | default | meaning |
|---|---|---|
| `memory_inject_k` | 5 | hits in the section; **0 removes the section entirely** |
| `memory_episode_chars` | 700 | how much of each episode the line carries |
| `memory_half_life_h` | 24 | the recency half life used for the injection |

## Operating notes

- The store is `state/memory/chroma`, collection `episodes`, **global to the instance**. Every
  agent's episodes land in it; the persona filter is applied at query time, never at write time,
  so one agent can deliberately read what another learned.
- The embedding function is chroma's default, ONNX all-MiniLM-L6-v2. It downloads once into
  `~/.cache/chroma/onnx_models` (not `~/.claude`, which hx never touches) and runs locally
  thereafter. It is not a model call in the spec-02 sense: no Claude Code session, no API, no
  network at query time.
- An instance that has never produced an episode never opens chroma at all — `hx compose` checks
  for `state/memory/chroma/chroma.sqlite3` or a non-empty queue first.
- To reset: delete `state/memory/`. Everything in it is derived from episodes that have already
  been consumed; nothing else in hx reads it.
- `hx memory index` is safe to run at any time, from cron or by hand. `hx memory search` indexes
  first anyway, so a search never misses an episode that is merely queued.
