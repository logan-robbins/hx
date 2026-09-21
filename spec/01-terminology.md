## 1. Terminology

| Term | Definition |
|---|---|
| HarnessAgent | One full Claude Code instance in a tmux session named by its id. Not a bare model loop: every task is given to it as a `/goal` so it runs with the harness's full capability (subagents, hooks, compaction, skills). Claude Code only for now. |
| Partner | The supervising HarnessAgent, id `partner`. The human talks to it in its tmux session (`tmux attach -t partner`) and tells it what to do; it turns that into order files and dispatches workers, one or more at a time, when it decides to, as a human would. The Partner is not a work item and is never dispatched, resumed, or completed: its persistent state is `PARTNER.md`. It has its own Companion. |
| Subagent | A child agent spawned inside a HarnessAgent, addressed by handle `<id>-sNNN` |
| Order | A Partner-written markdown file holding `## Order` and `## Definition of done`. `hx dispatch` reads it, copies it verbatim into the work item and `tasks.json`, and deletes it; the order text then lives in exactly those two places. Never passed as command-line text |
| Task | The control-plane record for one id in `tasks.json`: order text, addenda, outcome, dispatch and completion timestamps. Delivered to the HarnessAgent as a `/goal` pointer, never as prompt text |
| Work item | `pods/<pod>/<id>-<state>.md`, one per worker; the Partner has none. State ∈ `idle`, `working`, `complete` is the filename suffix. The body is the HarnessAgent's own running task list, which it updates frequently |
| Stream | One append-only raw log: `<id>-main` or `<id>-sNNN` |
| Companion | A small model paired one-to-one with a HarnessAgent (the Partner included); maintains coherency state for each of its streams. Companion prompts are tuned to the persona of the HarnessAgent they support. |
| Step state | The bounded JSON the companion maintains per stream; the continuity payload the HarnessAgent is rehydrated with at every boundary. Subagent step state is not merged into the parent by hx. Claude Code delivers each subagent's result to the parent as a completion notification in a later turn, and `/goal` defers its evaluation and issues check-ins while subagents are still running, so subagent deliverables are the harness's responsibility, not the Companion's. |
| Persona | The part of `config/<id>/AGENTS.md` above `## UPDATES BELOW ONLY`, Partner-written. `start.sh` copies it to `run/<id>/persona.md` at every launch and Claude Code loads it as appended system prompt (`--append-system-prompt-file`), so it is present in every turn, survives compaction and `/clear`, and costs no read. |
| Memory | The part of `config/<id>/AGENTS.md` below the header, written only by that agent; carried in the context file at every boundary. |

### 1.1 Verified harness behavior (Claude Code docs, 2026-09-19)

| Behavior | Status | Source |
|---|---|---|
| Background subagents keep running through parent auto-compaction; the compacted context is seeded with a reminder of which are still running | Confirmed | `code.claude.com/docs/en/context-window` "What survives compaction" |
| Subagents have their own transcript and compact independently of the parent, in both directions; `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` applies to them too | Confirmed | `code.claude.com/docs/en/sub-agents#auto-compaction` |
| Subagent results reach the parent as a completion notification in a later turn | Confirmed | `code.claude.com/docs/en/sub-agents#run-subagents-in-foreground-or-background` |
| A completion notification pending at the moment of compaction is still delivered afterward | Not documented; inferred from the two rows above. Covered by an acceptance test in `13-build-order.md` | — |
| `/goal` is a session-scoped prompt-based Stop hook: after each turn a small evaluator model returns met / not yet met / impossible. It does not spawn or supervise subagents; it skips evaluation while background work runs and issues check-ins (default 30 min, backing off 2x, `CLAUDE_CODE_GOAL_CHECKIN_MINUTES`) | Confirmed | `code.claude.com/docs/en/goal` |
| `/goal` survives auto-compaction | Confirmed. The clearing events are enumerated (met, impossible, `/goal clear`, `/clear`, a turn failing on an error, a context overflow compaction could not clear) and successful compaction is not one. Changelog 2.1.274 fixed the one gap, goal loss on `--resume` after compaction | `code.claude.com/docs/en/goal`, CHANGELOG 2.1.274 |
| `/goal` is restored on `--continue` and `--resume` (condition kept; turn count, timer, token baseline reset) | Confirmed | `code.claude.com/docs/en/goal` |
| `/clear` kills only foreground tasks; background subagents and background shells keep running | Confirmed | CHANGELOG 2.1.72 |
| Cross-session messaging is on by default; `crossSessionInbound: accept` delivers every inbound message with no hold, in any permission mode; an idle session starts a turn, a busy one reads it between tool calls | Confirmed | `code.claude.com/docs/en/cross-session-messaging`, settings reference |
| `SubagentStart` context is re-injected only on the subagent's next run after its compaction discarded it, not at compaction time; only JSON `additionalContext` is accepted | Confirmed | `code.claude.com/docs/en/hooks#subagentstart` |
| `--append-system-prompt-file <path>` appends file text to the default system prompt in interactive mode; `--append-subagent-system-prompt-file` exists but applies only with `-p`, so subagents cannot get a file-based system prompt in the TUI | Confirmed 2026-09-20 | `code.claude.com/docs/en/cli-reference` |
| A `/clear` pasted mid-turn is queued; at turn end the `Stop` hook runs first, then `/clear` (`SessionEnd`/`SessionStart(clear)`), before the `/goal` evaluator resolves; a `/goal` pasted from inside the `SessionStart(clear)` hook is taken by the TUI once it is ready | Confirmed by live test 2026-09-20 | E3 |
| Blocking `PreCompact(auto)` suppresses compaction for the rest of that turn even if a later `PreCompact` is allowed; the payload never changes; the window is a soft target and the turn continues past it without error; compaction resumes next turn | Confirmed by live test | E4 |
| `PreCompact`/`PostCompact` also fire for subagent compactions, with the parent's `session_id` | Confirmed by live test | E8 |
| Background completions from before a `/clear` are delivered into the new conversation as task notifications | Confirmed by live test | E5 |
| Messaging socket and token are per process, unchanged across `/clear`; a message to a busy session is folded into the in-flight turn; wire format is an auth line then `{"type":"user","message":{"role":"user","content":"…"}}` | Confirmed by live test | E6 |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` governs subagent compaction; `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` alone does nothing on a model with no native autocompact buffer | Confirmed by live test | E8 |
| On macOS Claude Code stores the login in the login Keychain (`Claude Code-credentials`), not in `.credentials.json`; a fresh `CLAUDE_CONFIG_DIR` is not logged in. Headless auth is `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` | Keychain and fresh-dir behaviour confirmed by live probe 2026-09-20 (build lane); the env var and `setup-token` to be re-verified against `docs/en/` by build-3 | build-3 |
| `SubagentStart` input is `{session_id, hook_event_name, agent_id, agent_type, cwd, permission_mode}`: no prompt, no `tool_input`, no `transcript_path`; the parent's prompt is the subagent's first message | Confirmed by docs and live run 2026-09-20 (build lane) | build-5 |
