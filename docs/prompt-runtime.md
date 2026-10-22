# Prompt and context runtime

Configured common, audience, role, policy, and identity sources compile into verified prompt
bundles. Stable instructions are separate from variable task facts. The current ledger packet
supplies behavior, constraints, acceptance, exact dependency/map versions, ownership, check
binding, progress, retained findings, and bounded evidence. Files rendered for a native boundary
are projections; the ledger owns assignment state.

The Partner explores every directive through current source and semantic dependency evidence,
searches/reuses existing capabilities before construction, and maximizes useful parallel work
within ownership, resource, source, and capacity limits. Packet count follows decomposition.
The worker owns implementation and exact checks, approved independently before edits/proof.

Startup and managed continuation submit the complete required instructions and task packet
as verified native input. The runtime freezes and hashes the exact submission before delivery.
Startup callbacks acknowledge native session/ownership; they do not spill long instructions
through a capped hook or ask the model to retrieve missing mandatory text. Full current source,
assignment, facts and context must fit the configured admission budget or the operation refuses.

Full immutable instruction backing material is stored in files no larger than 40,000 UTF-8
bytes, with deterministic NEXT/END markers, part identities and an aggregate content identity.
The runtime follows and verifies the entire chain automatically. There is no model-controlled
line/byte pagination and no summary substitutes for required material. System prompt transports
and native input are assembled from the exact verified bytes; complete delivery does not prove
attention, comprehension or instruction following.

Chat is a machine channel. The rule lives once in `continuity/prompts/common.md`, so the
compiled worker, subagent, partner, Context Companion and Learning Companion bundles each carry
it exactly once. Packet workers receive the
worker bundle as their system or context channel and both Companions receive theirs; no runtime
path delivers the compiled subagent or partner bundle. The live Partner boundary is
`config/CLAUDE.md`, `personas/partner.md`, identity, reviewed rules and `PARTNER.md`, without
`common.md`. The Partner's exception, that its replies to the human are the one place a sentence
is the deliverable, lives once in the persona's Domain judgment section, which reaches both the
live boundary and the compiled partner bundle. Pi worker children receive the parent's compiled
worker bundle through `--append-system-prompt` and carry the rule once. Partner children receive
`config/partner/SUBAGENTS.md` and other planned-worker children receive no hx SubagentStart
context; neither carries the rule. Planned capsules replace `config/CLAUDE.md` with the compaction
instructions, whose `PROMPT` states that a model reads the summary.

`hx metrics` reports `agents`: for each worker with a run in the bounded projection, its launch
`flavors` and the `chat_bytes`, `output_tokens` and `turns` of its captured main-session events.
A turn is one model response. Only records that report a figure count; a figure that no captured
record reports is `null`, and nothing is estimated. Child streams, hook copies of transcript
messages, failed meta `model_response` attempts and retired or unreadable observations do not
count. The Partner has no run and is not reported.

| Flavor | `chat_bytes` | `output_tokens` | `turns` |
|---|---|---|---|
| claude | UTF-8 bytes of transcript assistant text | largest `output_tokens` per transcript `message_id`, summed | transcript message IDs with usage |
| codex | rollout assistant and agent message text | per-response `token_usage_record` `output_tokens` (`turn_usage` is cumulative and not added) | `token_usage_record` count |
| pi | assistant `message_end` text | assistant `message_end` usage `output` | assistant `message_end` records with usage |
| meta | `null`: its Stop hook reports only the last message | successful `model_response` usage | successful `model_response` count |
| grok | `null` | `null` | `null` |

Whole source-file tools likewise return complete exact files in one operation. On Claude the
native Read result is the delivery and the hook adds only a completeness marker; Grok, Meta and
the Partner, whose hosts cap one hook string, get automatic transport segments, every one of
which must be emitted before the read can settle. Missing, partial or truncated results cannot
authorize later work. Required material over the file budget is split into parts, each its own
complete read; other work is admitted only once every part is read.
Known file-inspection shell wrappers cannot route file reads into lossy command-output reduction.
`hx file-read PATH` is the standalone native shell entry point when no dedicated read tool
is available; its broker delivers the complete content before acknowledging the call. Claude
workers have one and are refused the shell brokers.
Search candidates, captured log deltas and isolated tool-output reduction retain their explicit
contracts, and never establish exhaustive source absence.

Reviewed global and configured-role learning files are compiled into Partner, worker, subagent,
Context Companion and Learning Companion instructions for all native worker flavors. Source
hashes and exact conditions remain intact; pending proposals and foreign role rules are excluded.
Changed reviewed rules invalidate the relevant compiled input instead of mutating an active
frozen instruction bundle. At the next managed context boundary, a changed bundle closes tool
admission and queues a controlled, drained restart with a fresh capsule and complete current
instructions. The existing run and checkpoint are retained; native acknowledgment reopens
admission. Newly prepared Companion jobs use the current prompt identity, while attempted paid
jobs retain their frozen inputs.

A native boundary restores current instructions and the current run packet. Checkpoints freeze
source/task/event identities and acknowledge an exact frontier; late events remain pending.
The worker reuses retained source facts unless their inputs changed. It does not reread entire
transcripts, retrieve personal episodes, or request another narrative summary after each reset.

Captured native tool/lifecycle events feed bounded Companion passes. Jev judges bounded
observation/routing candidates; those judgments do not prove facts, graph edges, absence,
completion, or deployment. The Companion proposes typed task/map changes with exact evidence
references. Deterministic validation checks versions, scope, source applicability, and event
frontiers before publication. Agent prose by itself changes no authoritative task state.

Native launch names an admitted packet run and stable request identity. It verifies installed
bundle/context identity and native acknowledgment. Reservation does not imply execution.
Context reset, tool expansion, drain, completion, and recovery retain this same run identity.
The public native interface governs available tool-schema reduction; advisory adapters do not
claim schema savings unsupported by that interface.

Workers report actions, blockers, deliverables, and evidence through `hx progress`. Changes
in scope/contracts require stopped/revised assignments, not handwritten context amendments.
Managed completion drains observed work and capture, executes approved bound checks, verifies
source/dependencies and exact output coverage, then publishes and releases ownership.

The Partner's `PARTNER.md` retains current human decisions, constraints, unresolved questions,
and next scheduling consequences. It does not duplicate the authoritative fleet view or carry
intermediate agent dialogue. Board/detail and readiness commands supply bounded current state.

The Learning Companion has an independent cursor, one Messages API exchange per assessment, model/effort,
request budget and proposal limit. It reads frozen captured evidence and can only submit
assessments; it cannot patch task state, complete work or approve lessons. Pending proposals
are projected idempotently through the durable outbox. Another authorized session uses
`hx learn accept`, merge or reject. Both Companions share execution capacity, with independent
fair scheduling and failure states. Learning lag retains its evidence and never pretends to
be a current-context blocker or deployment gate.
