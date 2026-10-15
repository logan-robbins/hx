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
