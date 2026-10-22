# Deploying hx

HX is one unreleased product. Install its current package, create one instance, and configure
that instance for the project. The [system design](system-design.md) describes the complete
directive-to-release lifecycle; [getting started](getting-started.md) covers initial setup.

## Install and create the instance

Use a non-root macOS or Linux account with Python 3.14, `uv`, `tmux`, `git`, and Claude Code
available. Build and install the current checkout:

```bash
uv build
uv tool install --python 3.14 dist/hx_harness-1.0.0-py3-none-any.whl
hx install --root ~/hx
```

The package contains executable code and templates. The instance at `~/hx` contains its
configuration, private agent homes, captured evidence, and authoritative continuity ledger.
It must be outside the user's personal `~/.claude` directory. Installation does not import
that personal configuration.

Without configured credentials, installation stops with exit 4 and names the instance's
`seed/token`. Run `claude setup-token`, save the resulting token there with mode 0600, and
repeat the same `hx install` command. A local dotenv credential file can instead be configured
with `--env-file /absolute/path/to/.env`; existing instance seed credentials take precedence.
See [getting started](getting-started.md) for the credential procedure.

A completed installation starts the Partner and reports the UI address. Attach to its session:

```bash
tmux attach -t partner
```

Tell the Partner the project repository and desired outcome. Repository registration,
policies, and prepared worker directories are project configuration. A packet reuses suitable
prepared directories; optional source materialization is needed when those inputs are missing.
Every admission still revalidates current source, graph evidence, dependencies and ownership.

## Execution and the two Companions

Every directive uses the code/application dependency graph and search for existing behavior,
interfaces, providers and tests before construction. The Partner defines observable packets
and schedules maximum useful parallel work. Workers own implementation and exact executable
checks, which require independent binding approval before edits or proof.

Every agent has a Context Companion and a separate Learning Companion. The Context Companion
observes frozen events and proposes validated task/map updates. The Learning Companion has
its own cursor, jobs and budget, assesses that agent's evidence, and creates pending reusable
rule proposals. A different authorized session accepts, merges or rejects those proposals.
Reviewed global and applicable role rules reach every agent and both Companions in full.

Instructions and required context are delivered completely at managed native boundaries.
Source/instruction evidence is a complete verified object or an explicit refusal; bounded
non-file observations retain their own evidence contract. Neither Companion can turn an
agent's completion claim into proof.

## Observe and recover

The UI projects the same ledger task/run identities, bindings, progress, ownership and
delivery evidence as the CLI. Its Partner chat sends an explicit human message. Recorded
milestones do not substitute for current readiness checks.

```bash
hx doctor --root ~/hx
hx board --root ~/hx
hx up --root ~/hx
hx heartbeat --root ~/hx
```

`doctor` checks configuration and installed assets; `board` reads current fleet projections.
`up` recovers the Partner, UI and root service. `heartbeat` advances existing durable native
intents. Worker launch, drain and shutdown retain their admitted run and original request
identity; uncertain process ownership retains obligations until reconciled.

## Project release

Verified packet coverage releases worker ownership and can unlock dependent work. Integration,
intent review, local landing and release eligibility have their own exact-source gates. Fixed
CI candidates permit main to advance while checks run; matching .NET builds share immutable
artifacts and tests use private writable shards without recompiling.

The deploy gate requires same-source integration, resolved real-provider obligations and the
tested immutable artifact. The project's configured release system performs deployment and
health checks. HX does not imply a remote push or deployment from local landing. See
[agent-scale runtime](agent-scale-runtime.md) for the commands and evidence contracts.
