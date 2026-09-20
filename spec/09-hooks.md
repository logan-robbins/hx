## 9. Hooks

Every hook command is `/srv/hx/bin/hx-hook --id <id> <event>`, with the id written literally by `adapters/claude/install.sh` into `run/<id>/home/settings.json` (the settings file at the root of `CLAUDE_CONFIG_DIR`). Those hooks apply to the main thread and to every subagent spawned in the session. The worktree carries no `.claude/settings.json`, so this is the only hook source. This file is the single source for hook behavior; `11-adapters.md` covers launch and config only.

Verified facts this file relies on (docs 2026-09-19, live tests 2026-09-20, `01-terminology.md` 1.1): `PreToolUse` denies block under bypass permissions; tool hooks fire inside subagents with `agent_id`/`agent_type`; `SessionStart` plain stdout is injected as context but `SubagentStart` needs JSON; `Stop` fires after every turn and runs before a queued `/clear`, and a slash command pasted from inside it runs after it returns; a `/goal` pasted from inside the `SessionStart(clear)` hook is taken by the TUI once ready; `PreCompact` blocks suppress compaction for the whole turn and fire for subagent compactions too; `SubagentStart` context is not re-injected at a subagent's compaction.

### 9.1 Events

| `hx-hook` event | Claude Code event | Applies to | Action |
|---|---|---|---|
| `context` | `SessionStart`, matcher `startup\|resume\|clear\|compact` | Main thread | `hx compose <id> <id>-main`; print one line to stdout: `Read <path> before doing anything else.` Never JSON, never a leading brace. On `source=clear`, if the work item is `working`: `hx goal <id> --now` (this completes a seam). On `startup` and `resume` the hook sends no goal: `hx launch`/`hx restart` send it from outside once the pane is ready, and `resume` restores it natively. Partner: the same, with `PARTNER.md` and `hx board` output included by compose, plus write `CLAUDE_CODE_MESSAGING_SOCKET` and `CLAUDE_CODE_MESSAGING_TOKEN` to `run/partner/socket.json` (they are exported before `SessionStart` runs). |
| `subagent-start` | `SubagentStart` | Non-Partner | Assign next `sNNN`; record `{agent_id: sNNN}` in `run/<id>/subagents.json`; create `logs/<id>/<id>-sNNN-open.jsonl` with an `open` record (spawn prompt); append `spawned sNNN` to main; `hx compose <id> <id>-sNNN`; return `{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"Read <path> before doing anything else."}}`. Plain stdout is not injected for this event (only `SessionStart`, `UserPromptSubmit`, `UserPromptExpansion`, `PostModelSwitch` inject stdout) |
| `subagent-stop` | `SubagentStop` | Non-Partner | Append `close` record (last assistant message, transcript path); rename stream to `-closed`; append `closed sNNN` to main; wake companion. No decision output |
| `subagent-result` | `PostToolUse`, matcher `Agent` | Non-Partner | When `tool_response.status = completed`: map `tool_response.agentId` to `sNNN`; if the companion has written a closed-stream digest for it, return it as `additionalContext`. This is the only path that reaches the parent |
| `guard` | `PreToolUse`, matcher `*` | All, including subagents | Rules in 9.2. Deny = exit 2 with the reason on stderr. Never exit 1 |
| `log` | `PostToolUse`, matcher `*` | All, including subagents | Resolve stream: `agent_id` present → `subagents.json` handle, else main. Append one raw record. On the main stream, if `context_tokens ≥ threshold` from `models.json`, touch `run/<id>/seam` |
| `stop` | `Stop` | Main thread | Write `background_tasks` to `run/<id>/turn`; wake companion; if `run/<id>/goal-pending` exists, `hx goal <id> --now` and remove it (the Partner's self-dispatch and self-resume land here); else if `run/<id>/seam` exists, `hx seam <id>`. No decision output: the `/goal` evaluator owns continuation. Live-verified: `Stop` runs before a queued `/clear` |
| `precompact` | `PreCompact` | All (fires for subagent compactions too, with the parent's `session_id`) | Log-only: append a `compact_pending` record; `hx flush`. Never blocks: a block suppresses compaction for the whole turn and would also disable subagent compaction |
| `postcompact` | `PostCompact` | All | Append a `compact` record with `compact_summary` so the companion sees what Claude kept on the fallback path |

**Write discipline for streams:** each raw record is one line under 4 KB, written with a single `write(2)` on an `O_APPEND` descriptor, with `seq` assigned under a per-stream lock. Concurrent hook invocations on the same stream then append whole lines in order.

**Removed:** the stop gate and `stopblocks` counter. `/goal` keeps the agent working and decides met / impossible; `exhausted` is its check-in retries running out (`06-work-items.md`). The board invariant (working item with no goal marker) catches a session whose goal was lost.

### 9.2 `guard` rules

First match wins. Evaluate over every string in `.tool_input`, after `cd` to `.cwd`, resolving paths with `realpath -m`. `<id>` is the hook's baked-in id.

1. Edit/Write target is `config/<id>/AGENTS.md` → **allow** iff the resulting file is byte-identical above the line `## UPDATES BELOW ONLY` (compute by applying the edit to a copy). Otherwise **deny**: `only the section below the header is yours`.
2. Any string resolves under `config/` or `companion/`, or contains `hx-hook` → **deny**: `identity is delivered by hook`.
3. Edit/Write target is `PARTNER.md` → **allow** iff id = `partner`.
4. Edit/Write target is `pods/<pod>/<id>-working.md` for this `<id>` → **allow**.
5. Edit/Write target is under `orders/` → **allow** iff id = `partner`; otherwise **deny**: `orders are the Partner's`.
6. Edit/Write target is under `pods/`, `logs/`, `state/`, `run/`, `archive/`, or is `tasks.json` → **deny**: `managed by hx`.
7. Bash command contains `$HARNESS_ROOT` → **allow** iff it starts with `hx `.
8. Otherwise → **allow**.

Bypass permissions removes every prompt; these rules are the only enforcement and they are not bypassable by permission mode. Partner-only `hx` commands enforce their caller inside hx from `HARNESS_ID` (`08-hx-cli.md`).

### 9.3 Seam handshake (live-verified order)

1. `run/<id>/seam` is written by the Companion (step seam) or by the `log` hook (`context_tokens ≥ threshold` on the main stream).
2. Next `stop`: `hx seam <id>` runs. It requires empty `background_tasks`; otherwise it returns and the next `stop` retries.
3. `hx seam` flushes, composes the context file, pastes `/clear`, appends a `seam` record, removes the marker, and returns. The queued `/clear` runs after the hook returns.
4. `SessionEnd(clear)` then `SessionStart(clear)`: the `context` hook prints the path line and, the item being `working`, runs `hx goal <id> --now`.
5. The agent's first action in the new conversation is one Read of the context file. Its persona is already in the system prompt.

`hx seam` cannot wait for step 4 inside step 3: the clear only runs once the `Stop` hook has returned.

### 9.4 Subagent compaction (accepted limitation)

No hook fires on a subagent's own compaction, so its step state cannot be recomposed at that moment. What is documented: after a subagent's auto-compaction discards the `SubagentStart` context, Claude Code injects it again on that subagent's *next run* (a resume or a new message to it), not at the moment of compaction. A subagent that compacts and finishes without being re-run gets nothing back. Decision: accept this. Two mitigations are in force: the parent's standing instructions size subagent tasks to finish inside one window, and `SUBAGENTS.md` tells the subagent to commit as it goes so `git log` carries its progress across its own compaction. No subagent-scoped hooks are used, and the subagent system-prompt flag is unavailable in interactive mode.
