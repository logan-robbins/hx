# Audience-aware prompts for planned work

`hx compile ID --planned` builds versioned static instructions from common policy,
audience mechanics, domain judgment, validated identity, and operator configuration.
`--audience companion` and `--audience subagent` build those audiences for the same
executor. Partner uses its own audience and canonical role instead of an exemption.
The new sources live under `continuity/prompts/`; task facts remain in the current
checkpoint or frozen pass and never become static persona history.

## Roles and responsibilities

| Audience or role | Required behavior |
|---|---|
| Common | Reuse applicable facts; read selected source when needed for editing, verification, ambiguity, or omitted detail; search only for a named missing fact. Preserve complete conditions, negation, commands, reasons, and uncertainty. Compress/drop irrelevant task state. |
| Worker | Finish one observable behavior with implementation, fixtures, and checks together. Respect prerequisite versions, leases, pauses, and current host capacity. Report sparse progress and finish through verified receipts. |
| Partner | Use a bounded map brief, create behavior units, preserve real dependencies, enforce ownership and materialization, and consume completion proof. A reservation is not a native launch. |
| Companion | Read one frozen pass and named evidence slices; return evidence-bound operations and dispositions. Do not scan logs, execute work, infer missing task input, acknowledge later events, or certify completion. |
| Subagent | Work within the parent's exact question and inherited scope; serialize shared-worktree writes and leave commits under parent control. Return evidence and explicit gaps. |
| Backend | Preserve data/service/interface invariants; order implementation by actual dependencies; retain current schema/configuration and teardown needs. |
| Frontend | Verify the user journey, UI states, real API boundary, accessibility, and visual evidence tied to build/route/viewport. |
| Release | Use build/deployment/rollback map boundaries and exact artifact identity. Follow explicitly authorized operations. Reconcile unknown outcomes before retrying and refresh remote state when a decision requires it. |
| QA | Use current map contracts and anchors; bind assertion coverage to source/build/environment. Keep unrun checks and hypotheses distinct from proof. A subset does not certify the required acceptance suite. |

Each domain has companion-specific retention rules. QA and release both retain map
access and a companion because their decisions depend on current contracts, proof
inputs, and unresolved outcomes. The QA persona and companion also ship in the
existing role locations so the normal configuration validator accepts `qa-engineer`.

The shared legacy compiler now resolves configured identity variables and removes
exact copied role blocks, including known previous generated role versions, without
removing custom policy sentences. It retains active legacy state below the old
header until the coordinated migration. Planned compilation reads only the prefix
of legacy `AGENTS.md`, so generated memory does not enter its prompt artifacts.

## Artifacts and delivery

Compilation returns paths to `system.md`, `context.md`, and `manifest.json` under
`run/ID/prompts/AUDIENCE/VERSION/`. The manifest records source/body hashes, identity,
model/runtime, actual configured workdir and observed branch, section sizes, and
one intended delivery channel per section. Identical canonical sections deduplicate.
Unknown identity placeholders fail instead of reaching a native session.

The current launcher contracts give Claude, Pi, and Grok a system prefix. Codex and
Meta receive static instructions in the context packet. Companions currently use
the Claude launcher; subagent instructions use their context channel. These are
delivery plans, not proof of installed instructions or visible tools. Every bundle
starts with `activation: pending_controller_installation`, `tool_visibility:
unverified`, and `permissions_enforced: false`.

Context-channel worker bundles can already join a ledger packet:

```sh
hx compile eng-001 --planned
hx compose eng-001 --run RUN --request BOUNDARY --prompt-manifest MANIFEST --json
```

Composition verifies the manifest against current source/configuration and the
assignment's actual workdir, checks the compiled artifact hash, and includes its
instructions exactly once. All instruction bytes count against the packet budget.
Replaying that checkpoint revalidates its prompt bundle; changed policy, identity,
placement, or workdir requires current compilation and a new packet. A prepared
system prefix is refused by this path because its native installation acknowledgement
does not exist yet. Do not bypass that missing acknowledgement by calling the prefix
loaded in a status message.

An explicit instruction file remains available for already-resolved caller inputs.
Do not supply both `--instructions` and `--prompt-manifest`. The full-request budget
must additionally account for the installed system prefix, retained conversation,
tool definitions, and reserved output; that integration remains P09/controller work.

## Operator policy and migration

`continuity/policy.md` holds shared operator policy; `continuity/policy/AUDIENCE.md`
holds audience-specific policy. Existing custom global, role, and per-agent text is
preserved. Only exact recognized generated blocks are removed. Personal-memory
regions are excluded only in `AGENTS.md`; the same heading in an operator policy
file does not silently discard the remainder of that file.

Unfamiliar edits inside legacy global/role mechanics remain intact and produce a
`migration_review` reason. Such a bundle can be inspected but cannot enter the
compiled-context path until those mechanics are reconciled. Companion and subagent
prompts do not inherit executor-specific custom role or per-agent mechanics; put
their applicable policy in the shared or audience-specific source.

Compilation does not rewrite installed configuration, restart sessions, or enforce
tool permissions. Native controller installation/acknowledgement, companion tool
enforcement, task-scoped tool selection, instruction-skill migration, and coordinated
replacement of the legacy prompt path remain necessary before fleet activation.
The role/adapter matrix and CLI stand-in tests establish local assembly and launch
regressions only; they do not certify live model behavior.

## Private planned launch preparation

`native_launch.prepare` reserves one immutable preparation per planned run in
Schema 13. It verifies the assigned worker, current prompt sources, exact admitted
worktree, and prerequisite proofs. It freezes a task packet and prepares a private
installation root under `run/ID/launches/LAUNCH/root`. Only selected configuration,
current adapter code, and compiled instructions enter that root. Old native homes,
personal memories, and legacy memory skills are excluded.

Prepared adapter hooks and worker commands address the shared authority. Native
homes remain private to the launch. The executor installation skips the legacy
companion home; planned companion dispatch still requires its own restricted pass
controller. Credential provisioning follows the existing native adapters.

The initial 8,000-token allowance conservatively charges UTF-8 bytes for both the
custom system prefix and the task packet. Provider base instructions, tool schemas,
retained conversation, and output reserves still require P09 accounting. Prepared
files do not acknowledge native instruction delivery or tool visibility.

Retries retain the same launch identity. Colliding requests cannot reserve another
installation; interrupted preparation remains visible and retains assignment
leases. `native_launch.verify` rejects changed configuration, instructions, packet,
worktree, prerequisites, or newly arriving evidence. This is a library preparation
boundary; native submission, startup acknowledgement, stop/recovery barriers, and
CLI dispatch are not connected yet.
