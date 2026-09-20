## 11. Claude Code adapter

`adapters/claude/install.sh <id>` writes the per-agent home and hook wiring from `09-hooks.md` and seeds credentials and bypass acceptance from the harness user's `~/.claude`; `adapters/claude/start.sh <id>` derives the persona file and launches Claude Code bare in window `<id>:main`. The adapter is launchable once the M2–M6 suites pass under it (`13-build-order.md`).

| Item | Claude Code |
|---|---|
| Config home | `CLAUDE_CONFIG_DIR=/srv/hx/run/<id>/home` in the tmux session env. `install.sh` writes `home/settings.json` (hooks with `--id <id>` baked in, instruction-files mode `claude-md` (the real key is `pluginConfigs["agents-md@builtin"].options.instructionFiles`, honoured in the settings file at the root of `CLAUDE_CONFIG_DIR`; verified 2026-09-20 against `docs/en/memory`), `claudeMdExcludes` for the product repo's `CLAUDE.md`/`AGENTS.md`) and `home/CLAUDE.md` as a copy of `config/CLAUDE.md`. No `.claude/` in the worktree |
| Auth | Credentials are per home. The human logs the harness user's default `~/.claude` in once at system setup; `install.sh` copies its credentials file and writes the bypass acceptance entry into each `run/<id>/home/`, so no launch is ever interactive. Both survive every dispatch wipe (`08-hx-cli.md`). `start.sh` refuses to launch a home without them. Live experiments hit exactly this gap |
| Permissions | Bypass, always: `--dangerously-skip-permissions` at launch. The harness user is non-root (Claude Code refuses the flag under root/sudo). `permissions.defaultMode` is not used: it is ignored in project/local scope and would silently degrade to manual |
| Persona | `--append-system-prompt-file /srv/hx/run/<id>/persona.md`. `start.sh` derives the file from `config/<id>/AGENTS.md` above `## UPDATES BELOW ONLY` at every launch. The persona is therefore in the system prompt of every turn, survives compaction and `/clear`, and costs no read. Interactive-mode flag; the subagent variant exists only under `-p`, so subagents keep the hook path to their context file |
| Initial prompt | Never. Claude Code is launched bare; every instruction arrives as the `/goal` pointer pasted by `hx goal` or as a file path from a hook |
| Effort, model | `--effort <level>` and `--model <full id>` at launch from `config/<id>/harness.json` (`low`…`max`) |
| Compaction | Native, untouched: no `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, no `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, `CLAUDE_CODE_DISABLE_1M_CONTEXT` unset. The 1M window is default for Opus 5 and Sonnet 5 on the API and Max/Team/Enterprise plans. hx's seam threshold (`models.json`, 500k on 1M models) fires through the `log` hook well before the native window (~967k) |
| Compaction summary | Never used on the planned path; seams replace it. If a single turn runs from the seam threshold to the native window, the harness compacts and `SessionStart(compact)` still hands over the context file; the persona is untouched because it is system prompt. `PreCompact`/`PostCompact` are log-only |
| Subagents | Built-in; `agent_id`/`agent_type` on tool events inside subagents; `tool_response.agentId` on the parent's `PostToolUse(Agent)` |
| `context_tokens` source | `usage` of the latest assistant record at `transcript_path` |
| Run mode | Interactive TUI in tmux, driven by `/goal` |
| Goal delivery | `hx goal`: the pointer pasted via tmux buffer into `<id>:main`; from outside a hook it waits for the idle prompt first; from the `context` hook on `clear` it pastes immediately (`--now`) |
| Fallback seam | `hx restart`: kill `<id>:main`, `start.sh <id>` bare, `hx goal <id>` once the pane is ready |
| Messaging (Partner only) | `crossSessionInbound: accept` in `home/settings.json` (messaging is on by default, nothing to enable); the `context` hook records socket and token to `run/partner/socket.json` (`12-partner-loop.md`) |
| Env in session | `HARNESS_ID`, `HARNESS_ROOT`, `CLAUDE_CONFIG_DIR`, `DISABLE_AUTOUPDATER=1` |
| Binary and version | `config/claude.json` `{bin, version}` recorded by `hx install`; the version must be in the package's tested list; changed only by `hx upgrade` after the M6 live suite passes on it (`17-packaging.md`) |
| Worktree | `wt/<id>` cut from the bare mirror `repos/<name>.git` with sparse checkout excluding `.claude/`, so the product repo's own hooks and settings never load (`17-packaging.md` 17.3) |
| Pane log | `start.sh` runs `tmux pipe-pane -o -t <id> 'cat >> $HARNESS_ROOT/logs/<id>/<id>-pane.log'` right after launch; the file is the UI's capture fallback when the session is dead and is archived with `logs/<id>/` at the next dispatch |

Verified 2026-09-20 against the CLI reference, permission-modes, model-config, and cross-session-messaging pages.
