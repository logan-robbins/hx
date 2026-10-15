# Shared learning rules

Learnings are reusable operating rules supported by concrete evidence. Current task state,
source findings, proof receipts, and progress belong to the packet ledger; learnings are not
a personal notebook, task history, or replacement for current graph/dependency evidence.

An instance stores category files in the configured `HX_LEARNINGS` directory, the `learnings`
path in `config/hx.json`, or its default `LEARNINGS/` directory. A rule has signal, mechanical
check, and evidence. Role scope applies a rule only to the relevant audience; global scope
applies everywhere. One-off filenames and dates belong in evidence rather than the invariant.

```sh
hx learn propose --category testing --signal 'The observed failure' --check 'The mechanical check' --evidence 'Source/run/check identity'
hx learn pending
hx learn accept ID
hx learn reject ID --why 'Why it is not a useful supported rule'
hx learn check
hx learn path
hx learn categories
```

The proposer cannot approve its own lesson. A different authorized session reviews the
scope, wording, evidence, and overlap with existing rules. Merge repeated rules instead of
appending duplicates. Human authorization determines who can review; do not invent an extra
approval requirement beyond the configured runtime rule.

Current prompts select applicable configured rules with their audience/role sources. Preserve
exact conditions, negation, uncertainty, and decision reasons. A learning cannot loosen
assignment ownership, dependency gates, worker binding approval, or evidence requirements.

Every directive still follows current graph exploration, search/reuse before construction,
and maximum useful parallel decomposition. A past lesson does not prove present source,
absence, test success, integration, or deployment. Check changed inputs and preserve exact
current proof identities. See [planning](planning-runtime.md) and [prompt runtime](prompt-runtime.md).

Each agent is paired with a dedicated Learning Companion, separate from its Context Companion.
The assessor observes that agent's captured event stream, including native child-agent activity,
with its own cursor, frozen job, budget and model settings. It assesses reusable failure
prevention and proposes complete signal/check/evidence rules; it never approves its own lesson
or changes current task facts. Partner observations use the Partner capture stream; engineering,
QA and release use their own admitted run streams, regardless of native harness flavor.

`config/ID/harness.json` has an independent `learning` object with `model`, `effort`,
`batch_records`, `request_bytes`, `max_proposals`, and optional `disabled`. Context management
continues to use `companion`. `hx companion ID --learning --request REQUEST --run RUN`
prepares and executes an assessment; `--prepare-only` and `--job JOB` inspect the exact job.
The Partner assessor uses its Partner stream and has no worker run. Root scheduling is fair
between the two independent jobs within host capacity; stable unresolved inputs do not cause
repeated paid attempts. Learning lag retains evidence without blocking packet completion.

Accepted assessments create pending proposals through the durable outbox, with exact event
identities and retained source evidence. They become rules only after different-session review.
Reviewed global and applicable role rules are automatically delivered in full to Partner,
ENG, QA, release, subagents, and both Companions; they are not a Partner-only boundary.

An evidence handle requires a complete, identity-verified artifact read before it can support
a proposal. Captured excerpts never establish the completeness of their original file or
command output. The assessor can explicitly ignore irrelevant observations with a reason;
relevant unavailable evidence remains pending. Accept and merge check the 40,000-byte rule-file
budget before writing, so an oversized result leaves the proposal pending for review.
