# Installed native interface checks

Checked locally on 2026-09-28, serially, without model requests or a live worker fleet.
These results establish the observed interface surface, not completed fleet compatibility.

| Adapter | Installed version | Observed launch surface |
|---|---|---|
| Codex | `codex-cli 0.156.1` | Bypass/hook-trust flags, sandbox, model, and reasoning configuration accepted with `--help`. |
| Claude | `2.1.281 (Claude Code)` | User settings source, permission mode, model/effort, and appended system-prompt file accepted with `--help`. |
| Meta | `Muse Code 1.4.0 (1.4.0-R4161.1)` | `--yolo`, model, and reasoning effort accepted with `--help`. Help exposes no dedicated system-prefix flag. |
| Grok | `1.0.41 (4220f3b224a6) [stable]` | Minimal UI, permission mode, model/effort, and appended rules accepted with `--help`. |
| Pi | `0.84.3` | Offline mode, thinking/model, session directory, and appended system prompt accepted with `--help`. |

Each binary's `--version`, `--help`, and help invocation with the current launch
options exited zero. Help can exit before validating other arguments; these results
do not prove that the full launch argument list is accepted. Help-mode parsing does not establish authentication, provider
request behavior, actual prompt placement, tool visibility, event coverage, or reset
semantics. In particular, the option list alone cannot prove that no additional
instruction mechanism exists behind a configuration setting.

## Frozen hook identity

The existing Meta adapter documents cleared hook environments. Planned capture
therefore cannot depend only on inherited `HX_CONTINUITY_*` variables. All five
installers now register the launch/run/worker/adapter tuple and bake it into their
hook invocation. The tuple is immutable in Schema 12. Explicit installed arguments
take precedence over an environment inherited from another assignment. Old callbacks
validate the original registration even if the worker's current configuration changes.
Late public observations remain quarantined against that original run.

For Claude, Codex, Grok, and Meta, the identity is in the generated hook command.
Pi loads its small `hook-contract.json` once when its extension module initializes.
The per-agent settings/home still need native controller lifecycle isolation before
coordinated activation; registration alone does not prove which process loaded them.

The installer integration tests invoke the generated commands with all `HARNESS_*`
and `HX_*` environment variables removed, then again after worker reuse and a
configuration change. These tests exercise the actual generated command and hook
entrypoint without substituting the capture ledger. They do not invoke a model.

## Installed Pi loader

The installed Pi 0.84.3 `loadExtensions` function loaded the shipped TypeScript
extension, registered its handlers, and ran the `tool_result` callback against a
scratch ledger. The test changed both the environment and the on-disk contract
after module initialization; the callback retained its original identity and
delivered its public result. `node --check` also accepted the extension.

Reproduce with an explicit installed loader path:

```sh
HX_PI_EXTENSION_LOADER=/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js \
  .venv/bin/python -m pytest \
  tests/core/test_hook_contract.py::test_installed_pi_loader_delivers_with_frozen_contract -q
```

This test makes no model call and requires no credentials. It uses an isolated
home and the real
installed Pi loader with controlled event input. Native CLI emission of those
events, complete user/assistant/failure coverage, controller launch/resume,
companion integration, and real model execution remain to be verified separately.

## Native controller checks on 2026-10-01

The installed Muse Code `1.4.2 (1.4.2-R4684.1)` TUI ran on a private tmux server with
its built-in echo provider. Its real startup and request hooks reached the shared
ledger, installed the current context pointer, and confirmed the exact submission.
The test wrapper disables updates and removes model/effort options, which echo does
not support. No model request, provider authentication, or task execution is proven
by this test.

This check exposed two defects missed by the CLI stand-ins: the launcher used `-m`,
which the actual TUI rejects, and the controller waited for startup before sending
the first prompt, while Muse defers that hook until the first prompt. The adapter
now uses `--model`; the controller supports deferred startup and rejects request
execution until the startup context is verified.

```sh
HX_MUSE_TEST_BIN=/Users/loganrobbins/.local/bin/muse \
  .venv/bin/python -m pytest \
  tests/core/test_native_controller.py::test_installed_muse_startup_and_request_hooks -q
```

The installed Pi extension loader also exercised the new `input` handler and
delivered an exact request through its frozen hook contract. The controller suite
uses actual tmux and generated hook commands for all five adapters, with CLI
stand-ins for deterministic startup, collision, missing acknowledgement, transport
failure, and request rejection scenarios.

Request-hook contracts were checked against the primary
[Codex hook reference](https://developers.openai.com/codex/hooks),
[Muse hook reference](https://meta-models.github.io/muse-code-sdk/next/guides/extend/hooks/),
and [Grok hook source documentation](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/10-hooks.md).
The installed Pi extension type declarations document `input` and its handled
result. Controlled shutdown/recovery, complete source capture, full tool visibility,
companion execution, and model-backed unit completion remain unverified.

## Planned tool admission contracts (2026-10-01)

The planned installers now add `PreToolUse` admission. Failure-result coverage
follows the adapters' distinct contracts: [Claude's hook reference](https://code.claude.com/docs/en/hooks),
[Muse's event payload reference](https://meta-models.github.io/muse-code-sdk/next/guides/plugins/reference/hook-events/),
[Grok's hook source documentation](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/10-hooks.md),
and [Codex's hook reference](https://learn.chatgpt.com/docs/hooks).
Claude/Muse/Grok declare `PostToolUseFailure`; Codex documents nonzero Bash results
through `PostToolUse`, without declaring a separate failure hook. Pi's installed
0.84.3 extension declarations expose `tool_call` and `{block, reason}`; its installed
argument parser confirms that explicit `--extension` paths still load with
`--no-extensions` discovery disabled.

These contracts also establish limits. A host may skip or fail open on a crashed,
timed-out, or oversized hook. Codex documents tool coverage exceptions. Grok's
`subagentType` is not a unique child identity. Muse supplies `subagent_id` and
`child_session_id` on child lifecycle events; tool events carry `tool_use_id`.
The runtime records missing identity/settlement evidence as a gap and retains leases.
Hook admission does not establish operating-system process containment or prove
that detached/background work ended.

The installed Pi loader check now exercises both parent and child `tool_call`
callbacks against a planned launch: valid admission, durable result settlement,
then native `{block: true}` after drain, including the resulting denied-tool error
observation. The parent pane in that test uses the existing CLI transport fixture;
this proves the real extension loader/callback integration, not model execution.
The combined 240-test regression run also retained the installed Muse echo-provider
startup/request check. A final 22-test admission suite passed after the transaction
lock adjustment. No model provider calls were made.
