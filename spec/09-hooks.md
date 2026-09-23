## 9. Hooks

Every hook command is `/srv/hx/bin/hx-hook --id <id> <event>`, with the id written literally by `adapters/claude/install.sh` into `run/<id>/home/settings.json` (the settings file at the root of `CLAUDE_CONFIG_DIR`). Those hooks apply to the main thread and to every subagent spawned in the session. These are the only hooks hx configures. This file is the single source for hook behavior; `11-adapters.md` covers launch and config only.

Verified facts this file relies on (docs 2026-09-19, live tests 2026-09-20, `01-terminology.md` 1.1): tool hooks fire inside subagents with `agent_id`/`agent_type`; `SessionStart` plain stdout is injected as context but `SubagentStart` needs JSON; `Stop` fires after every turn and runs before a queued `/clear`, and a slash command pasted from inside it runs after it returns; a `/goal` pasted from inside the `SessionStart(clear)` hook is taken by the TUI once ready; `PreCompact` blocks suppress compaction for the whole turn and fire for subagent compactions too; `SubagentStart` context is not re-injected at a subagent's compaction.

### 9.1 Events

| `hx-hook` event | Claude Code event | Applies to | Action |
|---|---|---|---|
| `context` | `SessionStart`, matcher `startup\|resume\|clear\|compact` | Main thread | `hx compose <id> <id>-main`; print one line to stdout: `Use the Read tool once on <path> before anything else; do not cat it and do not read it twice.` Never JSON, never a leading brace. On `source=clear`, if the work item is `working`: `hx goal <id> --now` (this completes a seam). On `startup` and `resume` the hook sends no goal: `hx launch`/`hx restart` send it from outside once the pane is ready, and `resume` restores it natively. Partner: the same, with `PARTNER.md` and `hx board` output included by compose, plus write `CLAUDE_CODE_MESSAGING_SOCKET` and `CLAUDE_CODE_MESSAGING_TOKEN` to `run/partner/socket.json` (they are exported before `SessionStart` runs). |
| `subagent-start` | `SubagentStart` | Non-Partner | Assign next `sNNN`; record `{agent_id: sNNN}` in `run/<id>/subagents.json`; create `logs/<id>/<id>-sNNN-open.jsonl` with an `open` record (spawn prompt); append `spawned sNNN` to main; `hx compose <id> <id>-sNNN`; return `{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"Read <path> before doing anything else."}}`. Plain stdout is not injected for this event (only `SessionStart`, `UserPromptSubmit`, `UserPromptExpansion`, `PostModelSwitch` inject stdout) |
| `subagent-stop` | `SubagentStop` | Non-Partner | Append `close` record (last assistant message, transcript path); rename stream to `-closed`; append `closed sNNN` to main; wake companion. No decision output |
| `subagent-result` | `PostToolUse`, matcher `Agent` | Non-Partner | When `tool_response.status = completed`: map `tool_response.agentId` to `sNNN`; if the companion has written a closed-stream digest for it, return it as `additionalContext`. This is the only path that reaches the parent |
| `log` | `PostToolUse`, matcher `*` | All, including subagents | Resolve stream: `agent_id` present → `subagents.json` handle, else main. Append one raw record. On the main stream, if `context_tokens ≥ threshold` from `models.json`, touch `run/<id>/seam` |
| `stop` | `Stop` | Main thread | Write `background_tasks` to `run/<id>/turn`; wake companion; if `run/<id>/seam` exists, `hx seam <id>`, which takes only when the pane can receive `/clear` and the Companion is caught up — otherwise it leaves the marker and appends a `seam_waiting` watcher record for the next boundary. No decision output: the `/goal` evaluator owns continuation. Live-verified: `Stop` runs before a queued `/clear` |
| `precompact` | `PreCompact` | All (fires for subagent compactions too, with the parent's `session_id`) | Log-only: append a `compact_pending` record; signal the Companion and return. Never blocks: a block suppresses compaction for the whole turn and would also disable subagent compaction, and a flush-wait inside the hook hangs the turn it was meant to protect |
| `postcompact` | `PostCompact` | All | Append a `compact` record with `compact_summary` so the companion sees what Claude kept on the fallback path |

**Write discipline for streams:** each raw record is one line under 4 KB, written with a single `write(2)` on an `O_APPEND` descriptor, with `seq` assigned under a per-stream lock. Concurrent hook invocations on the same stream then append whole lines in order.

**Removed:** the stop gate and `stopblocks` counter. `/goal` keeps the agent working and decides met / impossible; `exhausted` is its check-in retries running out (`06-work-items.md`). A `working` item whose goal was lost shows on the board as a live session with no goal marker, and the Partner restarts it.

No hook enforces anything. There is no `PreToolUse` guard: each agent is told the absolute paths of its own files and that it reads nothing else under `HARNESS_ROOT`, and the Partner-only `hx` commands check their caller inside hx from `HARNESS_ID` (`08-hx-cli.md`). That is the whole of it.

### 9.2 Seam handshake (live-verified order)

1. `run/<id>/seam` is written by the Companion (step seam) or by the `log` hook (`context_tokens ≥ threshold` on the main stream).
2. Next `stop`: `hx seam <id>` runs. It requires empty `background_tasks`; otherwise it returns and the next `stop` retries. It then requires a pane free of menus awaiting the human and a caught-up Companion; otherwise it signals once, appends a `seam_waiting` watcher record with the per-stream state/head it is waiting on, and the next `stop` retries. Nothing in this path blocks: a hung turn is a worse failure than a late seam.
3. `hx seam` composes the context file, pastes `/clear`, appends a `seam` record, removes the marker, and returns. The queued `/clear` runs after the hook returns.
4. `SessionEnd(clear)` then `SessionStart(clear)`: the `context` hook prints the path line and, the item being `working`, runs `hx goal <id> --now`.
5. The agent's first action in the new conversation is one Read (the Read tool, once; never `cat`) of the context file. Its persona is already in the system prompt and costs no tool call.

`hx seam` cannot wait for step 4 inside step 3: the clear only runs once the `Stop` hook has returned.

### 9.3 Subagent compaction (accepted limitation)

No hook fires on a subagent's own compaction, so its step state cannot be recomposed at that moment. What is documented: after a subagent's auto-compaction discards the `SubagentStart` context, Claude Code injects it again on that subagent's *next run* (a resume or a new message to it), not at the moment of compaction. A subagent that compacts and finishes without being re-run gets nothing back. Decision: accept this. Two mitigations are in force: the parent's standing instructions size subagent tasks to finish inside one window, and `SUBAGENTS.md` tells the subagent to commit as it goes so `git log` carries its progress across its own compaction. No subagent-scoped hooks are used, and the subagent system-prompt flag is unavailable in interactive mode.
