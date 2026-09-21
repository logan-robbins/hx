## 3. Layout

```
$HARNESS_ROOT/                             # the instance (default /srv/hx on a server, ~/hx on a workstation); config/ is the part worth committing
  bin/hx                                   # CLI (08-hx-cli.md); the installed package's entry point, path recorded in config/hx.json
  bin/hx-hook                              # hook entrypoint (09-hooks.md)
  adapters/claude/install.sh               # writes per-id hook/config files and the bypass acceptance; copies no credentials
  adapters/claude/start.sh                 # derives run/<id>/persona.md, launches Claude Code bare from harness.json
  templates/work-item.md
  companion/BASE.md                        # shared companion system prompt
  companion/roles/<role>.md                # per-role retention rules
  config/CLAUDE.md                         # the ONE CLAUDE.md: truly global info; installed as the harness user's ~/.claude/CLAUDE.md
  config/models.json                       # per-model window + seam threshold
  config/claude.json                       # {bin, version} of the pinned Claude Code binary (17-packaging.md)
  config/hx.json                           # {bin} absolute path of the hx entry point, recorded by hx install and baked into hook commands
  config/ui.json                           # {port}
  config/<id>/AGENTS.md                    # persona above the mutable header (→ system prompt); the agent's memory below it (→ context file)
  config/<id>/SUBAGENTS.md                 # identity for this HarnessAgent's subagents (→ subagent context file)
  config/<id>/harness.json                 # per-agent config (05-configuration.md)
  PARTNER.md                               # Partner state doc; the Partner's whole persistent state
  tasks.json                               # {"<id>": {order, addenda, outcome, dispatched, completed}}
  pods/<pod>/<id>-<state>.md               # work items, one per worker; state is the suffix
  pods/<pod>/archive/<id>-<ts>.md          # benched bodies
  logs/<id>/<id>-main.jsonl                # main stream
  logs/<id>/<id>-sNNN-<open|closed>.jsonl  # subagent streams
  logs/<id>/<id>-pane.log                  # raw pane text via tmux pipe-pane, started by start.sh; UI fallback when the session is dead; not a Companion stream
  state/<id>/<stream>.json                 # companion step state per stream
  state/<id>/<stream>.digest.md            # closed-stream digest (subagent streams), returned to the parent
  archive/<id>/<ts>/                       # logs and state from prior dispatches (not from resumes)
  run/<id>/persona.md                      # derived at each launch from AGENTS.md above the header; --append-system-prompt-file target
  run/<id>/<stream>.context.md             # the single file handed to the agent at each boundary (02 Single-file context)
  run/<id>/home/                           # CLAUDE_CONFIG_DIR for this agent: its settings (hooks), auto memory, transcripts; auth comes from seed/token via env
  run/<id>/companion-home/                 # CLAUDE_CONFIG_DIR for this agent's Companion session (its own stop hook only, hx-companion skill)
  run/<id>/companion-system.md             # the Companion's composed system prompt (BASE.md + role + harness facts)
  run/<id>/companion/<stream>.pass.md      # one pass: which state, which log, from which seq, where to write
  run/<id>/companion/<stream>.out.json     # the Companion's output, validated and moved to state/ by hx
  run/<id>/subagents.json                  # {"<harness agent_id>": "sNNN"}
  run/<id>/turn                            # turn-end marker with last background_tasks
  run/<id>/goal                            # goal-sent marker with ts
  run/<id>/seam                            # seam-requested marker (Companion or log hook); removed by hx seam
  run/partner/socket.json                  # Partner messaging socket + token, rewritten at every SessionStart
  run/ui-token                             # UI bearer token, mode 0600
  seed/token                               # long-lived OAuth token from `claude setup-token`, pasted by the human once, mode 0600; exported as CLAUDE_CODE_OAUTH_TOKEN into every agent env. No Keychain or ~/.claude is ever read
```

- `.gitignore`: `pods/`, `logs/`, `state/`, `run/`, `tasks.json`.
- System setup, once, by the human: `hx install` (17-packaging.md 17.2). From then on the human talks to the Partner in `tmux attach -t partner`; the hx commands that run the fleet are run by the Partner or by a worker. `hx up` and `hx heartbeat` exist for the human to run, or to put in their own cron, if they want them; hx ships no unit files.
- The human authors and commits `config/CLAUDE.md`, `config/models.json`, `companion/**`, `templates/**`, and commits `PARTNER.md`.
- Personas and per-agent config live in `config/<id>/`, outside every agent's working directory; each agent is told the exact absolute paths of its own files and that it reads nothing else under `HARNESS_ROOT`. Nothing is enforced.
- `config/<id>/AGENTS.md` has two writers separated by the mutable header (`## UPDATES BELOW ONLY`). Above it: the project-scoped persona for that id, written by the Partner and edited rarely; it reaches the agent as system prompt via `run/<id>/persona.md`. Below it: that HarnessAgent's own long-term memory, written only by that agent; it reaches the agent in the context file. Neither the human nor the Companion writes this file. It survives seams because it is a file, not context.
- The work item is the HarnessAgent's own running task list. The agent updates its body frequently as it works; hx owns the state suffix and the `## Order` addenda.
- `config/CLAUDE.md` is the only CLAUDE.md that loads. The product repo's own `CLAUDE.md` and `AGENTS.md` are not loaded and are not edited: the harness user's settings set `claudeMdExcludes` for the repo file and instruction-files mode `claude-md`, so nothing in the agent's workdir is discovered.
- Per-agent `run/<id>/home/` isolates each HarnessAgent's hooks, credentials, auto memory, and transcripts, so one agent's memory never pollutes another's and hx can reset it between dispatches.
