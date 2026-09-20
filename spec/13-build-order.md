## 13. Build order and acceptance tests

pytest, temp `HARNESS_ROOT` fixtures, hook JSON piped into stdin, recorded raw logs for companion tests, and for M0–M5 a fake `claude` executable inside a real tmux session that records argv, env, and pasted input and emits scripted hook payloads. Milestones M6 onward run against a live Claude Code.

| M | Build | Pass criteria |
|---|---|---|
| M0 | Layout, `models.json` + `harness.json` validators, order-file and filename parsers, `install.sh`, `start.sh` | Validators and parsers reject every malformed fixture (order without `### Checks` included); `hx board` reports each invariant violation; `run/<id>/home/settings.json` validates: hooks present with the right `--id`, instruction-files mode `claude-md` (the real key is `pluginConfigs["agents-md@builtin"].options.instructionFiles`, honoured in the settings file at the root of `CLAUDE_CONFIG_DIR`; verified 2026-09-20 against `docs/en/memory`), `claudeMdExcludes` set; `start.sh` argv is exactly `--dangerously-skip-permissions --effort … --model … --append-system-prompt-file run/<id>/persona.md` with no prompt argument; `persona.md` equals `AGENTS.md` above the header |
| M1 | `hx` control-plane commands | Every 06-work-items.md transition passes; all others exit non-zero; dispatching 2 of 20 ids changes exactly 2 tasks and 2 work items and archives 2 log/state dirs; an item with an unmet `after` lands `queued` with no goal marker and is promoted by `hx complete done` of its last dependency; `hx complete done` is refused with `HX-CHECK-FAILED` on a failing check, a dirty worktree, or an open stream and changes nothing; `hx resume` keeps logs, state, and `## Tasks`, appends the addendum, and sends the goal; `hx dispatch partner` and `hx resume partner` archive nothing and leave `goal-pending`; interrupted dispatch recovers on re-run; `hx bench` archives the body before reset |
| M2 | `context`, `hx compose`, Claude adapter | On `startup`, `resume`, `clear`, `compact` the hook prints one path line; the file holds memory section, task with addenda, `## Tasks`, step state, open handles, in that order and no persona; Partner's file also holds `PARTNER.md` and board output; the agent's first tool call after a boundary is one Read of that path; asked who it is in its first turn, the agent answers from the persona with zero Reads |
| M3 | `guard` | Table below passes under bypass permissions |
| M4 | `log`, `subagent-start`, `subagent-stop`, `subagent-result` | Three parallel subagents produce three isolated streams with correct handles; each receives its own context file path; main stream records every spawn and close; closed-stream digest reaches the parent via `PostToolUse(Agent)`; `hx complete` refuses while any stream is `-open` |
| M5 | Companion loop, schema validator, cache layering, FIFO retention | State stays within budget across a 500-record replay; cache-read tokens reported on every call after the first; invalid output keeps prior state; `hx flush` returns with `seq` at log head; stream truncation never drops records ahead of `state.seq`; `prompt_version` stamped on every write; after a replayed `hx resume` the state keeps its closed steps and absorbs the addendum |
| M6 | Seam policy + `hx seam` + `context` on `clear` + `hx goal` readiness wait | Companion marker written only at step close above `seam_min_context_tokens`, after `seam_min_interval_s`, with no open subagents; `log` hook marker written when `context_tokens ≥ threshold` on the main stream and never for subagent streams; `hx seam` refuses while `background_tasks` is non-empty and succeeds at the next boundary; transcript order is `Stop` → `SessionStart(clear)` → `/goal` → one Read of the context file; `hx restart` and `hx launch` of a working item deliver the goal after the idle prompt appears; a goal left in `goal-pending` by a mid-turn `hx dispatch partner` is pasted by the `stop` hook and runs as the next input; no `compact_boundary` in the main transcript across a 10-seam run |
| M7 | Companion replay eval | From recorded logs, seam at 5 points per task; a fresh HarnessAgent continues from each context file without re-reading files noted in `working_set` or repeating dead ends. Record via `hx metrics`: tool calls in the first 10 turns after each seam, split into Reads of noted files vs. other; Reads of the context file per seam (must be 1) |
| M8 | End-to-end: Partner + 2 HarnessAgents with subagents | Human tells the Partner what to do in its tmux session → the Partner writes `orders/partner.md` and dispatches itself → decompose → one `hx dispatch` with an `after` chain → work with subagents → seams → one item ends `decision`, the human answers, `hx resume` continues it from its step state → all complete with Digest → Partner wakes → read → `PARTNER.md` update → bench → Partner's `hx complete done` passes `hx board --require-done` and it reports in chat; the human runs no hx command at any point; `hx board` exits 0 throughout; same metrics as M7 recorded per seam |
| M9 | UI (`16-ui.md`) | Board, agent, Partner, orders, archive views render from `hx board --json` and `hx show --json` fixtures; SSE fires within 1 s of a file change; a message from the Partner page arrives in the Partner pane; no endpoint mutates instance state |
| M10 | Packaging (`17-packaging.md`) | `uv tool install` from a wheel; `hx install` on a clean macOS and Linux user creates the instance, exports the token from `seed/token` into every agent environment, mirrors a repo, cuts a sparse worktree without `.claude/`, installs the boot and heartbeat units; the user's `~/.claude`, checkout, and remote are byte-identical before and after a full M8 run; `hx upgrade` refuses a `claude` version the live suite has not passed on |

**M3 tests** (all under bypass permissions):

| As | Call | Result |
|---|---|---|
| eng-001 | Read `config/eng-002/AGENTS.md` | deny |
| eng-001 | Read `config/eng-001/SUBAGENTS.md` | deny |
| eng-001 | Edit `config/eng-001/AGENTS.md` below `## UPDATES BELOW ONLY` | allow |
| eng-001 | Edit `config/eng-001/AGENTS.md` above the header | deny |
| eng-001 | Edit `config/eng-002/AGENTS.md` below the header | deny |
| eng-001 | Bash `cat ../../config/*/AGENTS.md` | deny |
| eng-001 | Read via symlink into `config/` | deny |
| eng-001 | Read `companion/BASE.md` | deny |
| eng-001 | Bash `hx-hook --id eng-002 context` | deny |
| partner | Read `config/eng-001/AGENTS.md` | deny |
| partner | Edit `config/eng-001/AGENTS.md` above the header | allow |
| eng-001 | Edit own `-working` work item | allow |
| eng-001 | Edit `pods/engineers/eng-002-working.md` | deny |
| partner | Edit own `pods/partner/partner-working.md` | allow |
| partner | Write `orders/eng-001.md` | allow |
| eng-001 | Write `orders/eng-001.md` | deny |
| eng-001 | Edit `logs/eng-001/eng-001-main.jsonl` | deny |
| eng-001 | Edit `tasks.json` | deny |
| eng-001 | Bash `hx complete done` | allow |
| eng-001 | Bash `hx task` | allow |
| eng-001 | Bash `hx dispatch eng-002 orders/eng-002.md` | allowed by guard, refused by hx (`HARNESS_ID` is not `partner`) |
| partner | Edit `PARTNER.md` | allow |

All harness assumptions were settled by docs, changelog, or live test on 2026-09-20 (`01-terminology.md` 1.1).
