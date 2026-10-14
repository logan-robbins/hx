# Audience-aware prompts for planned work

`hx.prompt_compiler.build` (called by the planned runtime: `native_launch`,
`native_controller`, `context_packets`) builds versioned static instructions from common
policy, audience mechanics, the agent's persona, validated identity, and operator policy.
The `companion` and `subagent` audiences are built for the same executor. Partner uses its own audience and its own persona instead of an exemption.
A planned prompt reads the sources a live agent reads (§ Planned prompt sources); only
the common policy and the audience mechanics live under `continuity/prompts/`. Task facts
remain in the current checkpoint or frozen pass and never become static persona history.

## Prompt layers

Every agent's prompt is four layers, and each layer has exactly one source:

| Layer | Source | Reaches the agent through |
|---|---|---|
| Invariants | `config/CLAUDE.md` | one channel per flavor (below) |
| Persona | `personas/<role>.md`, one file per role, no per-id text | one channel per flavor (below) |
| Identity | `harness.json` id, pod and `purpose` | the `context` hook's identity line |
| Learnings | `LEARNINGS/<category>.md` (global) and `personas/<role>/<category>.md` (the role's) | the `context` hook, each non-empty file whole |
| Session memory | the work item: `## Tasks` (the agent's) and `## Status` (hx's: step state, memory episodes, open subagent handles); `PARTNER.md` for the Partner | the one Read the `context` hook points at |

The invariants and the persona each reach an agent once (`config_harness.INVARIANTS_CHANNEL`,
`config_harness.PERSONA_CHANNEL`):

| Flavor | Invariants | Persona | Why |
|---|---|---|---|
| `claude` | `home` | `hook` | Claude Code loads the home's `CLAUDE.md` natively (`start.sh --exec` refreshes it from `config/CLAUDE.md`); the persona comes with the rest of the chain at every boundary, with no `--append-system-prompt-file` |
| `codex` | `agents` | `agents` | Codex reads `$CODEX_HOME/AGENTS.md` at every start; `start.sh --exec` writes it as `config/CLAUDE.md` then `personas/<role>.md` |
| `pi`, `grok` | `hook` | `flag` | a system-prompt flag, given the role file itself (`--append-system-prompt <path>`, `--rules "$(cat <path>)"`), but no native `CLAUDE.md` |
| `meta` | `hook` | `hook` | no system-prompt flag and no instruction file hx can use |

A second copy is a second place to drift, and it costs prefix bytes on every turn.

Nothing is rendered into a file. The `context` hook (SessionStart, matcher
`startup|resume|clear|compact`; `hx.hook_context`) prints the chain at every boundary
(`hx.persona.boundary`; `hx persona ID` prints the same text), so an edit to any source lands
at the next boundary:

1. `config/CLAUDE.md`, where the hook is the invariants' channel (Pi, Grok, Meta);
2. `personas/<role>.md`, whole, where the hook is the persona's channel (Claude, Meta). A role
   without one has no persona; the Partner's is `personas/partner.md` and is required;
3. the identity line, `You are <id> in pod <pod>. <purpose>`. `purpose` is the one paragraph
   that makes this agent different from the next of its role; `templates/worker/harness.json`
   ships `"purpose": "{{purpose}}"`, and until the Partner replaces it the line stops after the
   pod and `hx doctor` warns `purpose:<id>`;
4. every `LEARNINGS/<category>.md` that holds a rule, then every `personas/<role>/<category>.md`
   that does, whole;
5. `Use the Read tool once on <abs path> before anything else; do not cat it and do not read it
   twice.`, naming the `-working` work item (`PARTNER.md` for the Partner). An agent with no
   task in progress is told its goal arrives as a `/goal` pointer.

The output is bounded: 10,000 bytes on Claude, whose SessionStart output is cut to a
2,000-character preview and a file path past 10,000 characters (code.claude.com/docs/en/hooks,
"JSON output"), and 32 KiB elsewhere (`hx.persona.hook_bound`). Over the bound the rules are
listed as files to read once instead of printed; still over, the persona joins the list.
`hx doctor` warns `hook-size` for every agent that happens to.

Session memory is the work item. The agent keeps `## Tasks`; hx keeps `## Status`, rewritten at
every main-stream `hx compose` (launch, restart, resume, seam, and each `SessionStart`: the
boundaries, where the agent is not writing the file) in the `-working` item, and in `PARTNER.md`
for the Partner (`hx.compose.write_status`). It holds the step state, the memory episodes (not
the Partner's) and the open subagent handles, as `###` subsections; an unchanged status leaves
the file untouched, and an idle or completed item is never written. There is no main-stream
context file: the boundary's one Read is the work item, which already holds the goal, the
tasks and the status. Subagent streams keep theirs (`run/<id>/<stream>.context.md`).

Nothing per id is written by hand except `purpose` and the work item's `## Tasks`. An instance
from before this is moved by `hx install`, `hx up`, `hx launch` and `hx restart`:
`hx.persona.migrate_layout` turns `personas/<role>/AGENTS.md` and `config/partner/AGENTS.md`
into `personas/<role>.md` and moves rules scoped in place with `Role:` into
`personas/<role>/<category>.md`; `hx.persona.migrate` turns a worker's `config/<id>/AGENTS.md`
into `purpose` (while that is still the placeholder) and one pending text proposal for whatever
else it held that no shared source already says, then removes it. `hx doctor` warns
`persona:<id>` while anything is left.

## Planned prompt sources

A planned run reads the same persona and Companion role file a live one does, so an edit to a
role's judgment reaches both. `hx.prompt_compiler.build` reads these and nothing else:

| Section | Source | Audiences |
|---|---|---|
| `common` | `continuity/prompts/common.md` | all |
| `audience` | `continuity/prompts/audiences/<audience>.md` | all |
| `domain` | the `## Domain judgment` section of `personas/<role>.md` (the Partner's: `personas/partner.md`); none when the role has no persona | worker, partner, subagent |
| `purpose` | `harness.json` `purpose`, as the live identity line carries it | worker |
| `role` | `companion/roles/<role>.md`, the file the Companion's live system prompt is composed from | companion |
| `identity` | validated `harness.json` | all |
| `operator-policy`, `operator-audience` | `continuity/policy.md` where it differs from the shipped one; `continuity/policy/<audience>.md` | all |

Each role's domain judgment is its persona's `## Domain judgment` section and its Companion
retention judgment is its role file's `## Retention judgment`; there is no second role text
under `continuity/prompts/`, and no `generic.md` fallback (a Companion without a role file is
refused, as it is live). The rest of a persona is the live session's mechanics (its tmux
session, the `/goal` pointer, `## Tasks`, `hx complete`), which the planned audience file
replaces; it is not compiled in, which also keeps a planned worker's system prompt (about
5.4 to 5.6 KB for a shipped role) inside the native launch's 8000-byte initial budget
(`hx.native_launch`), as before. `config/CLAUDE.md` is not compiled in: it is the live agents'
invariants, a planned capsule's `config/CLAUDE.md` is the compaction instructions, and a
planned run's shared rules are `common.md` and operator policy. The learnings are the live
`context` hook's.

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

Each domain's Companion retention rules are its role file's `## Retention judgment`. QA and
release both retain map access and a companion because their decisions depend on current
contracts, proof inputs, and unresolved outcomes. QA ships a persona and a Companion role file
like every role, so the configuration validator accepts `qa-engineer`.

Identity variables in a planned bundle resolve from validated configuration
(`hx.persona.expand_identity`). There is no memory below a header any more: a persona an older
hx installed still ends at `## UPDATES BELOW ONLY`, and the readers stop there
(`hx.persona.LEGACY_HEADER`), so nothing below it enters a prompt.

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

Context-channel worker bundles can already join a ledger packet, given the manifest the
planned runtime compiled:

```sh
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

## Operator policy

`continuity/policy.md` holds shared operator policy; `continuity/policy/AUDIENCE.md`
holds audience-specific policy. The old memory heading ends only a persona an older hx
installed (`AGENTS.md`); the same heading in an operator policy file does not silently
discard the remainder of that file. Nothing else is reconciled: `config/CLAUDE.md` and a
per-id `config/<id>/AGENTS.md` are not compiled in (the second no longer exists:
`hx.persona.migrate`), so a bundle carries no `migration_review`. A rule for planned runs goes
in `continuity/policy.md`; a rule for one role's agents, live and planned, goes in its persona
or is a learnings rule scoped to that role. Companion and subagent prompts do not inherit
executor mechanics; put their applicable policy in the shared or audience-specific source.

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

`hx cache report [ID]` measures the invariant part of each agent's prompt prefix, the part a
seam should not have to rewrite (operator and Partner only). It adds up each source once,
whatever its channel: `config/CLAUDE.md`, `personas/<role>.md`, the identity line, the rules as
the `context` hook prints them (or the list of files the bound made of them), and the
frontmatter descriptions of the installed skills; it also reports the hook's output size
against its bound. Skill bodies are excluded because they load on
demand. The total is checked against `prompt_cache.invariant_budget` for the agent's model row,
and the age of the last seam against its `harness.json` `cache_ttl` via `prompt_cache.assess`.
Native tool schemas and host-injected context are printed as `unmeasured`, never estimated, so
the figure is a floor. `hx metrics <id>` carries the same object under `cache`. Use the local
number, not a vendor's published saving, before anyone claims that a prompt change reduced cost
(CONTRACTS.md has the shape).
