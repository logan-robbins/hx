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
Do not supply both `--instructions` and `--prompt-manifest`. The packet accounts for
compiled instructions and task context. Hooks, native autocompaction settings, and
model output caps enforce token thresholds at their respective layers; host-injected
context and native tool schemas remain adapter-specific.

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

The initial 8,000-token allowance conservatively charges UTF-8 bytes for the
custom system prefix, task packet, startup pointer, and submission marker. Provider
base instructions, host-injected context, and tool schemas are adapter-specific and
are controlled through the supported native token and tool-loading interfaces where
available. Prepared files alone do not acknowledge native instruction delivery or
tool visibility.

Retries retain the same launch identity. Colliding requests cannot reserve another
installation; interrupted preparation remains visible and retains assignment
leases. `native_launch.verify` rejects changed configuration, instructions, packet,
worktree, prerequisites, or newly arriving evidence.

## Fresh native dispatch

`hx launch ID --run RUN --request REQUEST` advances a reserved planned assignment.
It installs the private native home, creates a unique `hx-LAUNCH` tmux session,
and submits one launch marker when the UI is ready. Repeat the same command to
advance startup or inspect its durable status. Session creation never respawns an
existing window. Legacy launch/restart refuses an active planned worker.

The native startup hook verifies current configuration and the generated persona,
then composes a fresh immutable packet including intervening pending evidence.
The first user message identifies that hook's context pointer rather than an older
prepared file. Muse defers startup until the first prompt; the same protocol
supports both eager and deferred startup. The request hook rejects execution if
startup context is unverified. Claude/Codex/Grok/Meta use `UserPromptSubmit`; Pi
uses its native `input` event.

Only a request observation matching the submitted prompt and original native
session marks submission confirmed. An absent acknowledgement, uncertain transport,
or missing original pane retains ownership and never causes an automatic resend.
The generated prefix check and native startup receipt do not prove that a model
obeyed the prompt or that its full tool surface was restricted.

The root runtime schedules planned launches and controlled continuation boundaries.
It waits for classified capture, companion completion, tools, child processes, and
the original native pane to reach the required idle state before resetting. It records
reset intent before sending the native command and waits for startup acknowledgement.
Active runs retain their leases until recorded process ownership is reconciled. Native
receipts establish delivery and lifecycle state; they do not prove model compliance or
restrict tools that the host preloads outside the adapter's supported interfaces.
