# Continuity capture and progress contracts

These commands implement the staged ledger runtime on `codex/continuity-runtime`.
The legacy fleet remains authoritative until the coordinated migration in
[the implementation specification](continuity-implementation.md). Command availability
does not mean the installed native adapters have switched to this runtime.

The [application-map contract](application-map.md) covers `hx map`, portable repository
records, source validation, selected reads, and remaining shared-update integration.

## Native capture

The lifecycle controller creates a task/run, then binds each native execution stream:

```sh
hx capture bind --run RUN --stream main --adapter codex --session SESSION
```

The returned binding identifies the exact run, stream, adapter, native session, and
decoder. Repeating the registration returns the same binding. A native session cannot
be rebound to a new assignment. Every adapter accepts `hook-v1`; Pi also accepts
`--decoder pi-v1` for native JSON events, including children without session files.
Tree transcript records use `hx observe register` with explicit active ancestry instead.

Submit a complete delivery through stdin:

```sh
hx capture enqueue BINDING --delivery DELIVERY_ID < native-event.json
```

The producer allocates the delivery ID before sending, retains the source until
acknowledged, and reuses that ID when retrying those same bytes. Success returns
`committed: true` and the captured event IDs only after the transaction commits.
Changing the payload under an existing delivery ID is a conflict. A retry can return
its original acknowledgement after the run closes; a new delivery cannot enter that
closed run or a worker's later assignment. `HARNESS_ID`, when present, must own the run.

Capture preserves public messages, tool inputs/results, usage, and boundary evidence.
Private reasoning and streaming deltas are excluded. Producers should submit final
native events, not every token update. A Stop boundary preserves its final message and
background-work list even when a session log has not flushed. It does not declare
background work finished.

Native IDs link hook and log observations. Identical observations share one event;
complementary observations keep their separate evidence and common logical ID.
Calls with different IDs survive even if their output matches. Without a native ID,
delivery identity remains explicit and uncertain; output text never establishes identity.

The command performs no transcript scan, model call, or tmux delivery. It commits a
`capture_ready` outbox notification for the controller. Notification loss does not lose
the event. Unknown formats, session mismatches, stale assignments, and spool pressure
fail without acknowledging the delivery. Spool admission precedes artifact installation.
The input bound is 8 MiB; larger records remain unacknowledged until the producer supplies
a supported bounded representation. Full large-record streaming support remains an adapter integration
requirement, not a capability claimed by this endpoint.

## Typed progress

Read the current progress revision and cursor without loading prior task state:

```sh
hx progress --run RUN
```

Submit a sparse update:

```sh
hx progress --file progress.json
```

```json
{
  "task_id": "TASK",
  "run_id": "RUN",
  "expected_revision": 0,
  "phase": "implementing",
  "step_updates": [
    {
      "step_id": "cursor-update",
      "status": "active",
      "summary": "Keep events arriving during extraction pending.",
      "last": "Located the cursor update in src/hx/passes.py."
    }
  ],
  "next": "Make record updates and cursor advancement atomic.",
  "blocker": null,
  "deliverables": [
    {
      "path": "src/hx/passes.py",
      "op": "upsert",
      "description": "Transaction handling for frozen companion passes."
    }
  ],
  "evidence_ids": []
}
```

`expected_revision` is the task's progress revision: start at zero and use the revision
returned by the command. The runtime separately checks the run's pinned task revision.
A task amendment requires explicit lifecycle rebinding. Retrying the same submitted
update returns its original result; a conflicting stale update changes nothing.

Step statuses are `planned`, `active`, `blocked`, `done`, and `cancelled`. At most one step
is active. Transition the previous and next steps in the same update. Omitted steps and
optional step fields retain their current values and original field evidence. Top-level
`phase`, `next`, and `blocker` describe the current state; `blocker: null` clears it.

Deliverable paths are normalized relative to the task workdir. An `upsert` declares the
path and description. A `drop` removes that declaration without deleting the product file.
Omitted deliverables stay unchanged. Declarations and reported step completion are agent
claims; they neither create verification receipts nor complete the task.

Progress updates are limited to 16 KiB, 32 changed steps, 16 changed deliverables, and
32 cited event IDs. Cited events must belong to the same task. Progress, selected field
updates, the typed cursor, and projection notification commit atomically. Existing goals,
constraints, decisions, and unrelated findings remain unchanged. The event is reduced
deterministically, so routine progress does not trigger a companion model pass.

Native lifecycle wiring, producer retry storage, controller scheduling, projection
consumers, prompt deployment, and coordinated activation remain tracked in
[the build status](continuity-build-status.md).

## Check receipts

`hx check CHECK_ID` executes the assigned recipe for the caller's active run. Supply
`--run RUN` when no `HARNESS_ID` is set. The task's `checks` object maps check IDs to
versioned recipes. `--definition FILE` permits an explicit exploratory recipe but cannot
replace an assigned acceptance check with different content.

```json
{
  "schema_version": 1,
  "id": "cursor-regression",
  "argv": ["python", "-m", "pytest", "tests/test_cursor.py", "-q"],
  "cwd": ".",
  "inputs": [],
  "environment": {
    "complete": false,
    "executables": [],
    "inputs": [],
    "external_versions": {}
  },
  "timeout_s": 120,
  "max_output_bytes": 1048576
}
```

`cwd` resolves inside the task workdir. Commands are argument arrays; a shell script
must explicitly name its shell and arguments. Preserve executable invocation paths,
including virtual-environment symlinks. Recipe content determines its check version.

Source identity includes tracked contents and nonignored untracked inputs across the
repository. `inputs` adds declared ignored fixtures or other required files/directories;
relative paths resolve from the check cwd. A non-Git workdir needs explicit inputs.
Environment identity hashes the passed environment, OS identity, resolved executables,
and declared environment files. Include runtime/dependency manifests and other actual
environment inputs; a lockfile alone does not establish the installed environment.
`external_versions` maps each external dependency to its authoritative version file,
or `null` when no version is observable. Only a complete declared environment with
known external versions permits reuse. Environment values are not stored in receipts.

Fingerprinting streams file contents in 64 KiB chunks. Before/after content and metadata
identities reject source/environment changes, including writes that restore original
bytes. Watcher-backed input-generation validation remains part of the final admission
transaction; these snapshots alone do not eliminate every possible concurrent-write race.

Output streams to a temporary disk file, then into a content-addressed artifact without
a whole-output RAM buffer. Timeout, output overflow, nonzero exit, unresolved background
processes, incomplete source identity, and changed assignment invalidate the result.
`hx evidence EVENT --offset N --limit N` reads exact output pages. Output overflow remains
an explicit incomplete result; a retained prefix cannot establish a passing check.

A receipt binds exact argv/cwd, check version, source/environment fingerprints, exit,
duration, and output artifact. Check events reduce deterministically without waking the
companion. Reuse requires identical inputs/version/environment and intact retained output.
Unknown environment or external state permits recording an execution but disables reuse.
`--fresh` explicitly executes again. `--request ID` makes an uncertain acknowledgement
retry idempotent; a previously unresolved execution is never silently restarted.

`hx check CHECK_ID --receipt RECEIPT_ID` revalidates a receipt against current inputs
without executing its command. A successful execution or replayed acknowledgement is
not a claim that the inputs can never change. Completion must consume current proof.
Ledger completion, recovery of interrupted executions, input-generation guards, and
shared retention/admission quotas remain part of the remaining runtime integration.

The legacy completion path now checks source stability and Git cleanliness after checks
and after the companion flush. It also rejects changed acceptance/assignment before
writing completion metadata. Repository inspection failures are not treated as clean.
