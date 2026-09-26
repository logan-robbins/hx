# Portable application map

The map records current responsibilities, observable behavior, contracts, source
locations, and declared checks. Stable IDs describe responsibilities independently of
language, framework, path, and worker. A stack replacement changes source anchors and
namespaced attributes while preserving IDs whose responsibilities remain the same.

This is the P06a foundation of the [continuity specification](continuity-implementation.md).
Committed baselines and selected-record reads work. Shared proposal transactions, dirty
worktree overlays, incident-edge invalidation, task replanning, and coordinated export
recovery remain P06b/P06c work. The native fleet has not switched to this map.

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
