# Portable application map

The map records current responsibilities, observable behavior, contracts, source
locations, and declared checks. Stable IDs describe responsibilities independently of
language, framework, path, and worker. A stack replacement changes source anchors and
namespaced attributes while preserving IDs whose responsibilities remain the same.

This implements the portable baseline and transactional update portions of the
[continuity specification](continuity-implementation.md). Committed baselines, selected
reads, isolated dirty-worktree overlays, proposal collisions, indexed source refresh,
and invalidation of declared map consumers work. Source-watcher registration, downstream
task-graph replanning, write-lease integration, and coordinated export recovery remain.
The native fleet has not switched to this map.

## Repository format

Commit `.hx/map/manifest.json` and one JSON file per record. The manifest contains
`schema_version: 1` and a persistent UUID `repo_id`. Record folders are `behaviors`,
`components`, `interfaces`, `files`, `checks`, and `resources`. The filename is the
record ID. This splits unrelated Git changes into independent files.

Each record has exactly these fields:

```json
{
  "schema_version": 1,
  "id": "context-compiler",
  "version": 1,
  "kind": "component",
  "claim": "required",
  "summary": "The compiler preserves the task's current obligations.",
  "data": {"responsibility": "Assemble bounded continuation context."},
  "anchors": [],
  "edges": [],
  "attributes": {"python.runtime": "3.14"},
  "replaces": []
}
```

`claim` is `required`, `observed`, or `hypothesis`. Observed claims require source
anchors. An observed file record must anchor its declared path. IDs use lowercase
letters, digits, dots, underscores, or hyphens, up to 128 characters. Versions are
positive integers; `replaces` records split/merge lineage. Namespaced attributes retain
unfamiliar values. Unfamiliar structural types require a schema migration.

| Kind | Exact `data` fields |
|---|---|
| `behavior` | `outcome`; string arrays `preconditions`, `inputs`, `outputs`, `side_effects`, `failure_modes`, `invariants` |
| `component` | `responsibility` |
| `interface` | `contract` as a signature string or schema object; string array `invariants` |
| `file` | Repository-relative `path` |
| `check` | `recipe` using the [check contract](continuity-runtime.md#check-receipts), with the record's ID |
| `resource` | `responsibility`, `resource_type` |

An anchor contains `path`, `symbol` (qualified name or null), and `sha256`. Optional
`line` and `end_line` are display hints. Python qualified symbols are resolved from
syntax, including decorators. Other languages currently use whole-file anchors;
unsupported symbol resolution fails explicitly. Paths cannot escape the repository
or fingerprint the map itself. Whole-file hashing streams data without putting file
contents into the model's context. Source changes during validation fail the operation.

An edge contains `kind`, `to`, `status`, and `evidence`. Kinds are `implements`,
`contains`, `depends_on`, `provides`, `consumes`, and `checked_by`. Statuses are
`candidate`, `validated`, `stale`, and `disputed`. Validation checks endpoint existence
and allowed node kinds. A validated source relationship cites its record's anchor
indexes: `{"kind":"source","anchors":[0]}`. A validated `checked_by` edge requires
`{"kind":"check_declaration","check_id":"CHECK_ID","declaration":"Exact scope."}`.
Passing a test alone cannot establish a coverage relationship. Hypotheses and candidate
edges cannot establish validated relationships.

## Commands

```sh
hx map init --repo /path/to/product
hx map anchor src/compiler.py --symbol Compiler.build --repo /path/to/product
hx map check --repo /path/to/product
hx map import --repo /path/to/product
hx map get context-compiler --snapshot git:COMMIT --repo /path/to/product
hx map export --snapshot git:COMMIT --repo /path/to/product
hx map overlay --snapshot git:COMMIT --repo /path/to/product
hx map propose --file patch.json --repo /path/to/product
hx map refresh --snapshot worktree:HASH --path build.cfg --limit 16 --repo /path/to/product
```

`init` creates a stable repository identity; competing initializers publish one complete
manifest. `anchor` returns source evidence to include in a record. `check` validates
all portable records, relationships, and source anchors in an explicit batch, suitable
for CI. Commit source and map together before `import`; it requires a clean repository
and verifies actual Git blobs as well as working files. Fresh clones reconstruct the
same immutable `git:COMMIT` baseline. Reimporting identical content is idempotent.

`get` retrieves one indexed record and checks its anchors against the current worktree.
Unchanged file metadata permits reuse of a rebuildable source cache. A source change
marks the returned record stale and removes its returned validated edges. Unchanged
symbol content can refresh line hints without changing semantic identity. A baseline
must be an ancestor of the current checkout; unmerged sibling-branch knowledge is
rejected. Lookup validates the selected record's own anchors; it is not a readiness
proof for every destination record or consuming task.

`export` refreshes source evidence without the lookup cache, refuses stale records and
uncommitted map edits, and writes deterministic JSON shards. Individual file writes are
atomic. Whole-export coordination, concurrent-edit protection during publication, and
crash recovery remain P06c requirements; do not enable shared worker export yet.

## Shared proposals and collisions

`overlay` creates an isolated ledger snapshot from an imported baseline. This explicit
batch copies the baseline through SQLite without loading the graph into Python memory.
The returned `worktree:HASH` is pinned to the repository ID, baseline, absolute worktree,
Git metadata directory, branch, and HEAD. Repeated creation returns the same overlay.
A branch switch, worktree change, or new commit requires a new overlay; reconciliation
and promotion across those snapshots remain integration work. Dirty source changes
within the pinned checkout can be described by proposals with current source anchors.
Committed baselines remain immutable and visible to other workers.

`propose` accepts this envelope:

```json
{
  "schema_version": 1,
  "patch_id": "discover-packet-contract",
  "task_id": "TASK",
  "run_id": "RUN",
  "snapshot": "worktree:HASH",
  "read_versions": {"packet-contract": 0},
  "operations": [{
    "op": "put",
    "record": {
      "schema_version": 1,
      "id": "packet-contract",
      "version": 1,
      "kind": "interface",
      "claim": "required",
      "summary": "Every continuation packet identifies its current goal.",
      "data": {"contract": "Packet(goal: string)", "invariants": ["The goal retains its conditions."]},
      "anchors": [], "edges": [], "attributes": {}, "replaces": []
    }
  }],
  "evidence_ids": ["EVENT_ID"]
}
```

The assigned task's absolute `workdir` must match the repository. The run must be active
and bound to the current task revision. `HARNESS_ID`, when supplied, must own the run.
Evidence events must exist and belong to the same task. A patch cites every record it
read or changed, including each referenced edge endpoint. Version zero means absent.
A `put` supplies a complete record with expected version plus one. An `invalidate`
operation uses `{"op":"invalidate","id":"RECORD_ID","reason":"Contradictory evidence."}`
and preserves the record as stale. These operations grant no permission to edit source.

Record versions, source fingerprints, evidence, endpoint existence, and incident edge
types are checked before committing all operations together. Source bodies are checked
outside the writer transaction; unchanged sources use the existing cache. File metadata
and worktree identity are checked again before commit. Proposals read selected records
and indexed incident relationships, not the entire graph. A failed check rolls back
record changes, evidence, the patch receipt, and its notification.

| Collision | Result |
|---|---|
| Different records with unchanged read dependencies | Both patches commit. No global map revision causes a retry. |
| Identical resulting content for the same record | One version survives; both event references remain in the evidence table. |
| Changed record or read dependency | Reject with structured base/current/proposed values. The worker reconciles from evidence. |
| Repeated patch ID and identical request | Return the original result, including after its run closes. |
| Reused patch ID with different content | Reject. |
| Different worktrees, branches, or HEADs | Retain separate overlays; reject use of the wrong overlay. |

Changing an existing node marks incoming validated edges stale unless their source is
explicitly revalidated in the same patch. Invalidating a node marks its incident
validated edges stale. Reads expose those statuses; a prior relationship does not
silently remain validated. Line-hint changes alone preserve semantic identity. Added
nodes are reported separately from changed existing nodes through a transactional
`map_changed` outbox notification. Declared consumers are invalidated in that transaction;
the future task planner consumes the replan queue. This endpoint does not restart tasks
or manufacture verification receipts.

Each patch is bounded to 256 KiB, 16 operations, 64 read versions, and 64 evidence IDs.
Evidence is stored as individual relational references, so coalescing does not grow a
record's JSON indefinitely. Native companions will use this same envelope once their
map operations join the frozen-pass transaction; that routing is not activated yet.

## Source refresh and consumer bindings

Task payloads declare `map_inputs`; continuation records declare `inputs.map`. Both
contain at most 64 references of this form:

```json
{"repository":"REPO_UUID","snapshot":"worktree:HASH","id":"context-compiler","version":2}
```

Creating a task revision or current fact validates the exact versions, current source
anchors, and ownership of the worktree overlay. A delayed watcher cannot admit an
already stale source: selected-input validation checks current metadata and hashes on
cache misses. Task dispatch and check admission revalidate these bindings. Goals,
constraints, and active cursors cannot carry expiring map dependencies; their obligations
remain intact when application facts become obsolete.

`refresh --path PATH` queues records anchored to that exact relative source/configuration
path, then drains one bounded batch. Repeat `--path` for up to 128 paths. With no `--path`,
the command continues the durable queue. The default batch is 16 records; `--limit` accepts
1–32. The result lists refreshed and invalidated IDs and `more`, indicating remaining work.
Queueing uses the source-path index. A batch fetches only record headers first, then
processes one body at a time. The controller will call these APIs from registered source
watchers; that automatic registration is not wired yet.

Queued records are pending and cannot provide validated relationships or new dependency
bindings. Each drain checks source outside the writer transaction, then compares the
record version, queue generation, worktree identity, and observed file metadata before
committing. A newer notification or intervening source write leaves the item pending.
Process exit leaves unfinished items durable. Unchanged sources use the cache; unchanged
symbol content with moved lines refreshes display hints without changing semantic versions.

A changed or missing anchor creates a stale map version. The same transaction marks
incident validated edges stale, creates invalid versions of bound continuation facts,
invalidates receipts for affected task revisions, and queues exact consuming tasks for
replanning. Map proposals use the same consumer invalidation path. Unrelated facts and
tasks retain their state. A check that finishes after its map inputs change cannot create
valid proof; retrying an invalidated receipt returns that invalid status without executing
again. Receipts without declared map dependencies still require the source/environment
revalidation described in the check contract; this index does not infer their dependency
coverage from test success.

Restoring old file bytes does not revive invalidated facts, receipts, or task bindings.
Reconcile the map, amend the task with current input versions, and explicitly bind a new
run. Old replan entries belong to the old task revision. Downstream prerequisite expansion
and replan scheduling remain part of P08. Direct `inputs.files` facts still use their
existing source checks; automatic indexing of those file-only consumers is not provided
by the map-source queue.

## Memory and context bounds

Routine reads fetch selected ledger records. They do not scan the whole graph or load
source bodies into agent context. Full validation/import/export are explicit batch
operations. Batch graph validation uses a temporary SQLite database with a 1 MiB
page-cache target, disk-backed temporary storage, and explicit connection closure.
It walks records one at a time. Each record is at most 64 KiB, with at most 64 anchors
and 128 edges. Whole-file hashes read chunks of at most 64 KiB and stop at the initial
file size, rejecting files that grow or change. Python syntax parsing accepts at most
1 MiB of source and 40,000 tokens; larger files require whole-file anchors. Parser
objects have overhead beyond the raw source size; these bounds are not an RSS limit.

No local embedding model, vector service, or worker process is started by these commands.
The map is shared application knowledge; it does not store workers' personal histories.
