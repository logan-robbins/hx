---
name: hx-signoff
description: Verify packet proof, intent review, exact source landing, fixed integration candidates, tested artifacts, and authorized delivery without overstating completion.
metadata:
  hx-audience: worker
  hx-roles: release-engineer, qa-engineer
  hx-loads-when: preparing packet completion, reviewing evidence, handing off source, validating a fixed candidate
---

# hx-signoff

Every directive uses graph/dependency exploration, search/reuse before construction, and
maximum useful parallel scheduling within ownership, resources, and capacity. Packet count
follows actual decomposition. The current packet and ledger define the proof obligations.

Workers bind exact executable checks and coverage before edits/proof; the configured
independent reviewer approves the frozen hash. Preserve admitted source identity and exact
provider/contract versions. Run meaningful checks against the changed behavior and retain
current source, environment, fixture, toolchain, commands, and output evidence. A selected
subset cannot certify broader coverage. Skips and unrun obligations remain explicit.

Commit bounded declared source outputs and request `hx complete done`. The request is not
completion: the runtime drains managed work/capture, executes or reuses approved checks,
verifies exact receipts/dependencies/output coverage, publishes, and releases leases.
`hx plan finish RUN --file receipts.json` is the same boundary for a run whose native session
is already quiesced or that never had one; a live session is refused. Fix a failed gate and
retry using current proof. Agent prose or a nonempty evidence note cannot bypass the gate.

Packet coverage, independent intent review, local landing, integration proof, deployability,
and external deployment are separate states. Early declared interfaces can release permitted
consumers while provider implementation continues. A stand-in retains real-provider binding.
Check current readiness/proof; a historic milestone is not evidence that inputs stayed valid.

## Delivery authorities

The Partner plans and admits; it holds no delivery authority. The run's worker submits exact
run/base/head/behavior through `hx change submit`; the head must equal the completion proof and
the diff must stay within its published changed paths. A qa-engineer or release-engineer who
is not the submitter records intent approval with `hx change review` against the revision and
intent hash; sensitive kinds or paths need every configured sensitive reviewer or a
release-engineer. A release-engineer prepares merge work outside the short publication
transaction, then lands with `hx change land` and the expected integration head; a moved head
requires renewed preparation. Local landing does not push or deploy remotely.

QA or release authority freezes integration candidates and runs the configured post-merge
checks one after another against fixed source while main can advance. Freeze deploy candidates
only with matching verified integration and resolved real-provider obligations; failure
attribution and revert belong to release authority. Pre-deploy/build proof names immutable
source and tested artifact manifest. Use `hx change status` for current state rather than
promoting a worker's claim.

`hx handoff delivery DIR --change CHANGE --batch BATCH` captures exact packet/review/landing/
candidate/check/artifact identities. `hx handoff check DIR --live` rechecks current identity.
An older capture remains historical evidence; it cannot become current release proof by relabeling.

Use the product's configured release tool/pipeline within the human's authorization for external
deployment. Record the observed operation/artifact/source and unresolved outcome. Reconcile an
uncertain remote side effect before retrying. Report only the outcome the proof actually supports.
