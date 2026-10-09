# Compact continuation language

Write the smallest complete proposition that lets the next worker act correctly.
Use explicit subjects, concrete verbs, and exact objects. Remove narration and repeated
context, not grammar that carries causality, conditions, scope, uncertainty, or time.
These are reusable patterns, not an exhaustive vocabulary or fixed sentence template.

| Information relationship | Compact complete statement |
|---|---|
| Behavior + condition | `Ingest leaves late events pending until the next pass.` |
| Means + purpose | `Use "hx progress --file update.json" to persist the next action.` |
| Conditional action | `When capture catches up, issue the checkpoint.` |
| Exception / prohibition | `Do not retry publication unless the original operation is confirmed absent.` |
| Location + responsibility | `"companion.py::ingest" commits companion updates.` |
| Prerequisite | `Dispatch requires a current map slice and materialized prerequisite outputs.` |
| Ownership / scope | `T17 owns "src/hx/companion.py"; T18 owns its adapter fixtures.` |
| Interface / data flow | `The observer sends normalized events to the ledger.` |
| Input → result | `A duplicate capture ID returns its existing event ID.` |
| Decision + rationale | `Use SQLite transactions because records and cursors must commit together.` |
| Cause + consequence | `Changing the lockfile invalidates the check receipt.` |
| Expected vs observed | `The check expected cursor 40 but observed 41.` |
| Verification + applicability | `R12 passed for source S8 and environment E3.` |
| Current state + blocker | `T17 is blocked until T12 publishes interface I4.` |
| Completed action + continuation | `The cursor patch is complete. Next, run the late-arrival check.` |
| Failed approach + retry condition | `The hook missed sessionless children. Retry log-only capture only if children persist events.` |
| Scoped negative result | `No caller references "old_api" under "src/" at S8.` |
| Change + replacement | `Component C2 now uses Go; discard its Python command recipes.` |
| Bound + consequence | `The packet limit is 8,000 tokens; required overflow blocks dispatch.` |
| Uncertainty + resolving action | `The release outcome is unknown. Query operation O7 before retrying.` |
| Hypothesis + evidence needed | `The queue may drop late events; the concurrent-append check is unrun.` |
| Remaining obligation | `The unit check passed; the required integration check remains unrun.` |

The examples illustrate language; they do not certify current implementation state.

## Selection and composition

1. State what changed, what is true now, what constrains the next action, and what remains
   unresolved. Delete completed investigation narration once its useful result and current
   evidence are preserved.
2. Prefer one subject–verb–object clause. Attach a condition, reason, scope, or evidence
   qualifier when removing it would change a decision. Use two short sentences for distinct
   obligations; do not compress them into an ambiguous slash or unexplained abbreviation.
3. Share a subject or condition across coordinated clauses only when their applicability,
   evidence, and lifetime match. Keep independently invalidated facts as separate records.
4. Preserve binding user wording verbatim. Keep `not`, `only`, `unless`, `before`, `after`,
   units, revision/environment scope, `may`, `unknown`, and `not run` when they apply.
   An observed result, requirement, and hypothesis must remain distinguishable.
5. Use exact commands, paths, symbols, operation IDs, and required arguments. Quote
   whitespace-sensitive strings losslessly. Never shorten code to make a sentence prettier.
6. Put one stable fact/version ID on the statement. Keep full hashes, repeated provenance,
   and raw evidence behind that ID; include the precise applicability needed to act now.
7. Prefer domain names to pronouns when multiple entities are present. Do not repeat the
   worker's role, repository identity, or goal on every fact when the packet already binds
   those values unambiguously.
8. Express facts outside these patterns as ordinary concise sentences. Never force an
   unrelated fact into `use`, `status`, or another ill-fitting slot. Typed schemas enforce
   evidence and field integrity, not an exhaustive ontology of English.

## Runtime responsibility

The companion writes new concise propositions from bounded evidence. The renderer keeps
their text and semantic qualifiers; it does not heuristically remove words or ask another
model to paraphrase the same state at every boundary. Common structured records (commands,
cursors, search results) render deterministic complete clauses. Free-text findings preserve
other relationships without inventing a new record kind for every sentence pattern.

Tests check retained negation, exceptions, source scope, evidence status, exact commands,
and outstanding obligations. A short string is not evidence of faithful compression.
Required overflow blocks admission; optional omissions go through the explicit
context/store/compress/drop lifecycle. Length alone never authorizes losing a qualifier.
