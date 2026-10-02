# Continuity capture and progress contracts

These commands implement the staged ledger runtime on `codex/continuity-runtime`.
The legacy fleet remains authoritative until the coordinated migration in
[the implementation specification](continuity-implementation.md). Command availability
does not mean the installed native adapters have switched to this runtime.

The [application-map contract](application-map.md) covers `hx map`, portable repository
records, source validation, selected reads, and remaining shared-update integration.

## Planned-assignment context

The explicit ledger path of `hx compose` produces one immutable packet for an active
planned run. It never reads the legacy work item, personal memory region, episode
store, or another worker's task facts:

```sh
hx compose eng-001 --run RUN --request BOUNDARY_ID --json
hx compose eng-001 --run RUN --checkpoint CHECKPOINT
hx compose eng-001 --run RUN --checkpoint CHECKPOINT --ack
```

The first command returns the checkpoint ID, content-addressed packet path, selected
fact versions, omitted optional IDs/reasons, stream boundaries, capture state, and
charged size. Without `--json`, composition/replay prints only the immutable artifact
path for one native file read. Workers can compose only their own assignment;
Partner/operator callers may prepare context for a named worker.

The packet carries the task/version, parent goal, exact current constraints and
acceptance, run phase, cursor, writes and prerequisites, required map records,
lossless check recipes, recorded check evidence, and ordered unresolved events per
stream. A recorded receipt is explicitly evidence for its recorded inputs; it is
not an unqualified assertion that the present source still passes. Its event ID
provides bounded `hx evidence` recovery. Scope pauses remain binding even if a worker
reports another progress phase; paused packets explicitly prohibit further mutation.
Verbose fingerprints and proof hashes stay behind pinned record/receipt IDs in the
checkpoint metadata. A map check identical to an assigned recipe references that
recipe once; commands, conditions, and current semantic claims remain lossless.

Current goal, constraint, and cursor facts are mandatory, along with `--require ID`
facts. `--record ID` chooses optional current-task facts. Without that option, the
compiler considers up to 16 current context-retained facts, prioritizing the current
step's declared consumers. It validates each selected fact's source applicability,
skips stale optional facts, and refuses stale required facts. Selection uses headers
and sizes before loading bodies. Pending bodies up to 768 bytes appear intact;
larger bodies stay behind exact event addresses. No transcript scan or map search
runs during packet construction. Required map versions were selected by the plan.

The default packet limit is 8,000 tokens with at most 1,000 for optional facts.
Until a versioned adapter tokenizer is supplied, each UTF-8 byte costs one token.
`--max-tokens` and `--optional-tokens` set the explicit packet budgets. There is also
a 256 KiB decoded-packet ceiling. Mandatory overflow refuses the next packet rather
than truncating commands, negation, constraints, or pending obligations. A checkpoint
supports at most 64 required facts, 32 streams, 16 registered log sources, and 32
unresolved events; larger unresolved sets require reduction or task decomposition.

`--instructions FILE` includes already-resolved operator/role instructions exactly
once and charges them to the packet. Native installation of compiled personas,
remaining conversation, tool schemas, and reserved output accounting still need
integration; packet size alone is not full-request admission.

`--prompt-manifest FILE` instead validates a [compiled prompt bundle](prompt-runtime.md)
against current sources, identity, channel placement, and assignment workdir. Context-channel
instructions enter the packet once, and checkpoint replay detects changed policy. Prepared
system prefixes remain unusable through this path until native installation is acknowledged.

Forced mode, the default, retains pending extraction, explicit capture gaps, and
lag detected by comparing registered file identity/size with committed offsets.
`--planned` refuses while known lag or unresolved evidence remains. This establishes
extraction readiness only: a packet always reports `native_reset_ready: false`
because the adapter's final-message/background-work barrier is not wired yet. No
composition command resets a native model or blocks native compaction.

Issuance freezes its stream vector and pending IDs without classifying events.
Reusing the same retained boundary request returns the same bytes; changed request
inputs conflict. Rehydration refuses changed required facts, selected versions,
source applicability, or assignment phase. Late arrivals remain eligible for the
next packet. Acknowledgement names the current checkpoint and does not advance any
event cursor. Retain the current packet and its immediate predecessor; acknowledgement
retires the predecessor. Retired artifact references become eligible for the existing
GC. Compact request keys prevent a retired request from being silently reissued;
closed-task key cleanup remains part of the retention lifecycle implementation.

The existing compose route without `--run` remains legacy. Native dispatch, hooks,
seams, personas, and controller replay must switch together; this explicit interface
does not activate a partial fleet migration.

## Planned companion execution

```sh
hx companion eng-001 --run RUN --request PASS_REQUEST --stream main
hx companion eng-001 --run RUN --request PASS_REQUEST --prepare-only
hx companion eng-001 --job JOB
```

One request freezes up to 32 events within a 6,000-byte pass and selected current
records. Reusing its request ID returns the same job. `--record ID` explicitly includes
a fact; goal, constraint, and cursor records remain mandatory. `--job` only
inspects status. Known bookkeeping-only batches commit deterministically without
a model call. Failed model outcomes and unknown event shapes require classification.

Before freezing a semantic pass, Jev selects additional current-task facts through
the TypeSafe API. This path is enabled directly, without an opt-in flag. It considers
up to 16 current candidates using whole compact facts, the goal/constraints/cursor,
and bounded recent observations. Nouls at or above 0.90 select facts; explicitly named
facts and mandatory records bypass filtering. Larger sets report `has_more`.

The client pins `jev-1.13.0` and calls `POST https://api.typesafe.ai/v1/systemone`.
It reads `TYPESAFE_API_KEY` from the environment or the named entry in
`~/workspace/.env`; `TYPESAFE_API_KEY_FILE` can specify another key file. The key is
never stored in the ledger or passed to the native companion. Independent questions
batch within 4,000 UTF-8 bytes per request, at most 16 questions, four requests, and
16,000 bytes per selection. Each API request has a 500 ms deadline and no synchronous
retry. Jev failure stops preparation with an explicit error: no heuristic ranking,
substitute model, or native companion execution follows that failed decision.

Successful selection is cached by exact task/run, cursor, source-bound fact versions,
model, policy, state, and questions. Repeating those inputs reuses the stored decision.
Scores remain outside the companion packet. Required inputs and bookkeeping need no
semantic selection. This selects facts for the planned companion; checkpoint selection,
tool loading, map traversal, retention decisions, and output reduction still need wiring.

`--map-snapshot SNAPSHOT --map-record ID` supplies up to eight selected map IDs, within
the same pass budget. A selected absent ID has version zero and may be created. The
companion can include one `map_patch` in its existing `submit_patch` response. The shared
map writer handles version conflicts and identical concurrent edits. Map changes,
fact updates, dependent invalidation, and cursor advancement commit together; a failed
operation rolls all of them back. Facts rebound to the new map version in that same
response stay current. Map selection loads only the named records, checking sizes first.

The configured native Claude companion runs once in a temporary private home with
the compiled companion instructions and two MCP tools: `read_evidence` and
`submit_patch`. It has no shell, repository read/write, dispatch, or completion
tools. The evidence reader serves only event IDs in the frozen pass or its selected
records: up to eight requests, 2 KiB each, with an 8 KiB total requested-byte budget.
The patch validator accepts at most two submissions and 16 KiB per patch. It applies
the existing atomic fact/version/cursor checks; native prose does not update state.
Late events remain for the next pass. Ordinary checks and bookkeeping need no model
review, and the Partner consumes compact progress/readiness results rather than raw
check output. Details are retrieved only when they affect a decision.

Instructions, pass, and supplied tool schemas are limited to 32 KiB. Native execution
is limited to six turns, 60 seconds, and 64 KiB captured output, with a requested
4,096-token generation limit. This is not complete provider-request/cost accounting.
One durable root-wide running slot serializes model execution on low-memory hosts.
The current implementation honors the configured model and creates no worker memory.

A failed extraction leaves events pending and records an unresolved job; repeating
that job never resends the model request. An interrupted driver retains running
ownership without a timeout-based takeover. Process reconciliation/recovery, automatic
observer scheduling, and complete Jev integration remain unfinished. This explicit
command does not switch the legacy companion or fleet authority.

## Native capture

Installed `hx-hook` entrypoints have an explicit planned-run route. The controller
must supply `HX_CONTINUITY_RUN`, `HX_CONTINUITY_LAUNCH`, and
`HX_CONTINUITY_ADAPTER` to the adapter installer for the original executor launch.
The installer validates the active run and configured adapter, then registers an
immutable launch/run/worker/adapter tuple. A launch ID cannot be reassigned. Generated
hook commands carry `--continuity-run`, `--continuity-launch`, and
`--continuity-adapter` with the root and worker ID, so cleared native environments
cannot silently fall back to legacy handlers. Explicit arguments override inherited
environment values; every planned hook checks its registered tuple. Pi snapshots
the same arguments from `hook-contract.json` once at extension load. This route
binds observed native sessions and child
streams to that run, captures public observations, and skips legacy memory,
context, compaction, completion, and companion side effects. The Partner guard
retains its existing enforcement path. Missing launch identity or malformed input
creates a visible capture gap when the ledger is writable. Hooks still return
without blocking native compaction.

`native_producer.py` stores decoded public observations in a durable retry queue
before delivery. Unknown/private raw fields do not enter the queue. Queue admission
is atomic and limited to 1,024 records and 64 MiB for the root; an individual public
delivery is at most 8 MiB. This disk queue is additional to the capture spool, not a
process-memory budget. The installed Codex/Grok/Meta normalizers and shared hook
reader also bound incoming data. Queue overflow or undecodable input records a gap;
the original native source is still needed for reconciliation.

Only an acknowledged delivery is removed. Lost acknowledgements retry the same
identity and bytes. An older queued observation prevents newer observations in
that binding from overtaking it. The existing root observer retries on notification
and reconciliation, with at most 16 binding heads and a 256 KiB scheduling quantum;
one complete larger record may exceed that quantum. A blocked binding does not
prevent another worker's head from being tried. No additional resident is started.

```sh
hx capture pending --run RUN
hx capture retry
```

The first command reports queued count/bytes and gaps for the caller's run. The
second is a Partner/operator command for one bounded retry pass. Planned context
waits for pending deliveries; forced context exposes the lag. Unit completion checks
the queue, gap state, and registered-source readiness in its final publication transaction. Late observations
after stop or amendment stay queued against their original run; they never move to
the worker's new task. Unresolved producer capture also blocks downstream admission
and final prerequisite verification, even if the producer had previously completed.
Late-record/gap reconciliation and automatic pause notification remain controller
work; there is no automatic gap-clearing or invented acknowledgement.

Context compilation, unit completion, and prerequisite admission share the same
bounded source snapshot: at most 16 registered sources, inspected through file
metadata rather than body reads. Unread bytes, a missing/replaced file, changed
timestamps, pending partial records, persistent gaps, or excessive source scope
block readiness. The observer records modification/change timestamps alongside
identity, offset, and its fixed tail fingerprint. A metadata change without an
append leaves a persistent gap, including a same-size rewrite outside that tail;
an ordinary retry cannot silently clear it. Metadata-only changes can therefore
require reconciliation too. Old source records without timestamps require an
observer pass. This snapshot does not prove writer quiescence or full native
source coverage; process ownership remains a separate completion gate.

Planned Pi extensions relay completed user/assistant messages through
`hx-hook message`, including sessionless children. The extension projects public
content before serialization; thinking, signatures, image bytes, and arbitrary
provider fields never enter the hook pipe. The Pi decoder retains text, proposed
tool calls and namespaces, stop/error descriptions, usage counts, and
unavailable-image markers. These are claims, not tool admissions or verification
receipts. Tool results retain their existing admission/settlement route. Messages
without native IDs keep separate delivery identities even when their text
matches. Unknown completed-content shapes become visible gaps, and durable
retries use the shared observer without a new process or transcript scan.

Planned Claude lifecycle hooks register reported main and child JSONL sources in
the original launch's private home through `native_sources.py`. Registration
records paths and ownership; it never searches session directories or reads a
transcript body. Main `transcript_path` and child `agent_transcript_path` remain
separate streams, matching [Claude's hook contract](https://code.claude.com/docs/en/hooks#subagentstop).
The hook's final-message observation is retained immediately; a missing file or
partial line remains pending until the observer can ingest the delayed record.
Identical reports reuse one binding. A different path/session for that stream,
a reused path for another actor, or more than 16 sources requires reconciliation.
Other adapters keep their existing hook/direct-event routes; they do not acquire
an assumed Claude decoder merely because a payload contains a transcript path.

The scoped observer pins the private home's filesystem identity and opens each
relative path component without following symlinks. Replacement homes, redirected
paths, and non-regular files produce gaps before content ingestion. It validates
session/actor identity and tracks Claude's `uuid`/`parentUuid` chain incrementally;
main-session sidechains/team branches are excluded, while child sources retain
their actor and separate tool identities. Progress/system links advance ancestry
without becoming assistant findings. A fork, missing ancestry, or compaction
chain reset requires explicit reconciliation instead of silently selecting a
different branch. This policy covers fresh linear launches; controlled branch
selection and resume remain unfinished. [The official SDK's transcript and
subagent chain code](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/sessions.py)
defines the chain fields; its full-file reconstruction is not used in ordinary
capture. Attachment records retain an unavailable-content marker.

Schema 15 indexes registered sources by run and source ID without resetting
existing generations or offsets. Task amendments and closed runs prevent scoped
transcript ingestion; evidence never follows a reusable worker slot to a newer
assignment. EOF and successful source registration still do not establish native
quiescence, complete source coverage, or permission to release assignment leases.

Codex main-thread lifecycle hooks use the same private-home registration with
`codex-rollout-0.156.1`. The decoder is pinned because the
[Codex hook reference](https://learn.chatgpt.com/docs/hooks#common-input-fields)
does not promise a stable transcript format. The first committed record must
identify the expected session and CLI version. Another session/version, repeated
session metadata, rollback, compaction, or an unknown record leaves a visible
gap without advancing past it. A later version requires its own verified decoder.
Codex child transcript ancestry is not yet verified; a reported child source
creates a gap instead of treating inherited parent history as child execution.

The decoder captures public requests/messages, tool calls and outputs, command
completion/exit status, discovered tool definitions, and usage with explicit
response/turn/session scope. Hook and rollout tool observations share call IDs
while retaining complementary fields. Known message broadcasts are skipped in
favor of canonical response items. Reasoning and encrypted content are excluded;
unavailable images/audio retain markers. Native instruction/world-state frames
retain change fingerprints rather than repeating instruction bodies in the spool.
These fingerprints do not establish full-request accounting or prompt equivalence.
Turn completion remains separate from process quiescence and receipt validation.

Planned Claude, Muse, and Grok installs also capture `StopFailure` and `SessionEnd`;
Grok captures `StopCancelled`. They produce observational boundaries with exact
diagnostics, native turn/prompt identity, and an explicit unsettled outcome. A
failure's rendered error is not an assistant finding. Delayed notifications remain
associated with their native turn and never settle tool calls, children, or leases.
Failure/end hooks can register a reported Claude transcript without reading it.

Muse additionally captures `PostLLMCall`: the installed 1.4.2 CLI emitted this event
for a rejected model request but did not emit `StopFailure` in that probe. Keep
request/response IDs, attempt/step, status, error, and provider-reported attempt
usage. Native truncated message/tool summaries and output previews are excluded
before the hook pipe;
they cannot establish full prompt contents, complete responses, or tool visibility.
Repeated identical observations of one request deduplicate; another native request
ID retains its own evidence. Failed-attempt usage is a vendor report, not proof of
zero billing or the current context size. Original session identity separates
main and native reminder sessions. Complete Muse log/feed coverage remains open.

The five adapter configurations can route their existing normalized hook events
through this entrypoint. This is not automatic fleet activation: launch/resume must
install the environment contract, compiled instructions, and checkpoint delivery.
The current installation sets do not yet provide every adapter's user-message,
failure, assistant-only, or child-source registration. The shared `request` and
`log-failure` routes accept those observations when supplied; native coverage and
version checks, companion routing, and reset barriers remain required.
The [installed-interface checks](native-interface-verification.md) separate real
CLI option parsing and the installed Pi loader from model-execution evidence.

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

Native lifecycle wiring, producer-gap reconciliation, controller scheduling, projection
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

## Native tool admission and drain

Schema 14 adds indexed tool admissions and child identities. For planned sessions,
`PreToolUse` calls `tool-start` before execution; Pi uses its blocking `tool_call`
callback. Admission requires the original launch, verified startup, acknowledged
submission, current unpaused assignment, owned pane, and matching main/child session.
The short transaction serializes admission with drain and lease release. At most
256 calls may be admitted concurrently per launch. Store call identities and input
hashes here; preserve tool evidence in the existing bounded capture path.

A captured success or failure settles its exact admission in the capture transaction.
Durable queue retries perform the same reduction. An unmatched result stays captured
and records a gap; it cannot clear another call. A deliberately refused call may
produce a native error result without manufacturing a capture gap. Completed or
refused call IDs cannot authorize another execution. Child IDs are launch-scoped;
Muse's child-session binding allows tool payloads without an actor field. A category
such as Grok's `subagentType` is not a child ID. Missing stable identities remain gaps.

```sh
hx launch eng-001 --run RUN --request ORIGINAL_LAUNCH_REQUEST --drain
```

This Partner/operator command closes new request/tool admission and returns durable
`draining`, `pending_tool_calls`, and `active_children` fields. Repeat it to inspect
progress. Existing calls can settle; the command does not paste another prompt,
replace a native session, kill processes, or release execution leases. Intentional
refusals return explicit native denials. Entry-point/storage errors deny planned
requests/tools and record gaps when storage permits. Observation hooks retain their
capture-first behavior. Legacy workers retain their existing hook configuration.

Claude, Muse, and Grok install `PostToolUseFailure`; Codex uses its documented
`PostToolUse` path, including nonzero Bash results. Pi returns `{block: true}` when
admission fails and carries the frozen contract into explicitly loaded child
extensions. Planned Pi children use the private capsule configuration and report
their own admission/result pair, avoiding duplicate parent log observations.

This barrier counts observable calls, not every process or remote operation a tool
may start. Zero admitted calls, a Stop hook, or a quiet pane cannot prove quiescence.
Native hook timeouts/crashes may fail open outside the hx entrypoint, and complete
adapter/tool coverage is not yet certified. Process ownership, detached/background
work reconciliation, controlled termination/resume, and full compaction barriers
remain required before native completion may release its leases.

## Owned-process shutdown

```sh
hx launch eng-001 --run RUN --request ORIGINAL_LAUNCH_REQUEST --shutdown
```

This Partner/operator command advances one bounded shutdown step. It closes native
admission, persists process-instance identities, stops the observed descendants,
then kills those exact instances. Repeat the same command until it reports
`stopped_unreconciled`. It is an interrupting shutdown: already admitted calls may
be interrupted and remain unresolved. No acceptance receipt or task completion is
invented from termination.

Startup now records the pane process's kernel identity. On macOS, signals use an
audit token with a process generation; Linux uses pidfds and boot/start identity.
Stale identities do not signal a reused PID. Shutdown never selects a replacement
by its tmux session name. A launch lacking the original process-instance receipt
and controlled supervisor is not silently adopted for termination.

A small waiting shell is tmux's immediate child. It launches the native adapter once,
waits, then exits; it does not watch files, poll, restart workers, or run a model.
This is necessary because tmux resumes its immediate child after `SIGSTOP`. The
native process beneath the shell can remain stopped while the controller discovers
its descendants. The supervisor stays runnable while waiting and is terminated last.

Each call sends at most 16 signals or performs one process census. Shutdown retains
at most 256 observed identities. The census contains PID/parent metadata only, is
bounded at 256 KiB before parsing, and does not read process arguments, environments,
source files, or transcripts. Stop intent and identities commit before signals.
A short writer-exclusion transaction surrounds each freeze batch, preventing a
suspended hx hook from holding the ledger's writer lock. Frozen descendants are
expanded before the kill phase. Conditional ledger updates prevent concurrent
controllers from replacing a newer shutdown snapshot; interrupted calls resume
from persisted identities.

`stopped_unreconciled` proves only that the recorded instances cannot execute. A
process that detached and was reparented before discovery, an external service, or
an uncaptured native operation can lie outside that snapshot. Its receipt therefore
keeps `scope: observed_descendants` and `coverage_verified: false`; assignment leases
remain held. Background/source reconciliation, completion handoff, and controlled
resume remain required before the full lifecycle can release or reuse ownership.
