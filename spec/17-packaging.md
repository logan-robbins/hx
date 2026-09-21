## 17. Packaging, install, launch, and isolation

### 17.1 Package and instance

Two things exist and are never mixed.

- **Package** `hx`: the autodev rewrite. Python 3.14, zero runtime dependencies, `pyproject.toml`, entry points `hx` and `hx-hook`. Installed with `uv tool install hx` (or `pipx`). Ships `adapters/claude/{install.sh,start.sh}`, `templates/`, `companion/{BASE.md,roles/}`, `skills/{hx-partner,hx-worker}`, `ui/` static files, and the instance skeleton. Upgrading the package never writes into an instance.
- **Instance** `HARNESS_ROOT`: the user's data, created by `hx install` (default `/srv/hx` on a server, `~/hx` on a workstation). Holds `config/`, `pods/`, `logs/`, `state/`, `run/`, `archive/`, `seed/` (`03-layout.md`). `config/` is the only part worth committing to the user's own git; everything else is runtime state.

`bin/hx` and `bin/hx-hook` in `03-layout.md` are the package entry points; hook commands in `run/<id>/home/settings.json` reference the absolute path `hx install` recorded in `config/hx.json`. If a package upgrade moves the binary, `hx doctor` says so and `hx install` re-records it.

### 17.2 `hx install` (the one manual command)

1. Refuse root. Check `tmux`, `git`, Python ≥ 3.14, and the `claude` binary; record `{bin, version}` in `config/claude.json` and the hx entry-point path in `config/hx.json`. The bare version string must be in the package's tested list (the list the M6 live suite last passed on); otherwise install stops and says which version to install.
2. Create `HARNESS_ROOT` from the skeleton: `config/CLAUDE.md`, `config/models.json`, `config/partner/{AGENTS.md,SUBAGENTS.md,harness.json}`, `companion/`, `templates/`, empty `pods/`.
3. **Seed token.** Print the two steps the human performs: run `claude setup-token` (their own Claude, interactive) and paste the token into `$HARNESS_ROOT/seed/token`; `hx install` then sets mode 0600. Until that file exists, install stops with exit 4. There is no `--from-user-config`: hx reads nothing from `~/.claude`, on any platform.
4. `hx launch partner`; print `tmux attach -t partner`. Then start the UI: `hx ui` in tmux session `ui`, and print `http://127.0.0.1:<port>/`. The human runs nothing after this.

That is the whole of deployment. hx ships no launchd plist and no systemd unit: `hx up` (launch everything) and `hx heartbeat` (restart dead sessions, wake the Partner when the board moved) are ordinary commands, and a human who wants them at boot or on a timer puts them in their own cron. After install the human types nothing but chat.

Working directories are not hx's business. Each worker's `workdir` is whatever absolute directory the Partner puts in `config/<id>/harness.json` — a fresh directory it created, or a checkout that already exists. hx creates no repository and no branch for it and pushes nothing anywhere. The only git it ever runs is `git status --porcelain` inside `hx complete done`, and only when the workdir is a git repository.

### 17.3 Same binary, two worlds

The harness runs the same `claude` binary the user already has. Separation is by config dir and version pin; nothing about the user's own Claude changes.

| | The user's normal `claude` | A harness session (`start.sh`) |
|---|---|---|
| Config dir | `~/.claude` | `CLAUDE_CONFIG_DIR=$HARNESS_ROOT/run/<id>/home` |
| Settings and hooks | The user's | `home/settings.json` written by `install.sh`: hx hooks, bypass acceptance, `claudeMdExcludes`, instruction-files mode `claude-md` (the real key is `pluginConfigs["agents-md@builtin"].options.instructionFiles`, honoured in the settings file at the root of `CLAUDE_CONFIG_DIR`; verified 2026-09-20 against `docs/en/memory`), Partner `crossSessionInbound: accept` |
| Skills | The user's `~/.claude/skills` | `home/skills/hx-partner` or `home/skills/hx-worker` only |
| CLAUDE.md | The user's and the repo's | `config/CLAUDE.md` only; the repo's is excluded |
| Memory and transcripts | The user's, accumulating | Per home, wiped at every dispatch |
| Credentials | The user's login (macOS Keychain or `~/.claude/.credentials.json`) | A long-lived token in `seed/token`, exported as `CLAUDE_CODE_OAUTH_TOKEN`; the user's login is never read |
| Permissions | Whatever the user chose | Bypass, always |
| Working dir | The user's checkout | `harness.json.workdir`, the directory the Partner chose for that agent |
| System prompt | Default | Default + `run/<id>/persona.md` |
| Companion | Not applicable | A second Claude Code session per agent, window `<id>:companion`, same binary, same flags, own home; woken by pasting, never `claude -p` |
| Version | Auto-updating | Pinned: `DISABLE_AUTOUPDATER=1 IS_SANDBOX=1` in the session env |
| Goal | None | The `/goal` pointer (workers; the Partner has none) |
| Visibility | Their terminal | Only through `tmux attach` or `hx ui` |

The guarantee is about the user's Claude, not their repo: hx never reads or writes `~/.claude`, never touches the Keychain, and runs on a pinned binary with the autoupdater off. What happens inside a worker's `workdir` is the worker's own work, committed as it goes, in a directory the Partner picked for it.

### 17.4 Launch

`start.sh <id>` is the entire launch; nothing else ever starts a Claude Code process:

```
env HARNESS_ID=<id> HARNESS_ROOT=<root> CLAUDE_CONFIG_DIR=<root>/run/<id>/home DISABLE_AUTOUPDATER=1 IS_SANDBOX=1 \
  <config/claude.json bin> --dangerously-skip-permissions --effort <level> --model <full id> \
  --append-system-prompt-file <root>/run/<id>/persona.md --setting-sources user
```

No prompt argument, no `--resume`, no compaction variables, cwd `harness.json.workdir` (`HARNESS_ROOT` for `partner`). `run/<id>/persona.md` is regenerated from `config/<id>/AGENTS.md` above the header immediately before exec. `hx up` runs `hx launch` for every id; `hx launch` runs `start.sh` in window `main` and `hx companion` in window `companion`, and sends the goal if the item is `working`. The human's manual commands, in total: `hx install` once, `tmux attach -t partner` whenever they want to talk.

### 17.5 Skills and instruction files

Operating knowledge is delivered in three layers, none of which touches `~/.claude`:

- `config/CLAUDE.md` (always loaded, short): what the `/goal` pointer means, that the work item is the task list, that the first action after any boundary is one Read of the path the hook printed, that `hx complete` is the last action, and that the agent's own files live at named absolute paths under `config/<id>/` and it reads nothing else under `HARNESS_ROOT`.
- `config/<id>/AGENTS.md` above the header (system prompt): the project-scoped persona.
- Skills, installed by `install.sh` into `run/<id>/home/skills/` from the package, loaded on demand:
  - `hx-partner`: the order file format and what makes a good definition of done and `### Checks`; `hx launch`, `dispatch`, `board`, `read`, `resume`, `bench`, `restart`; what each outcome means and the action for it; that sequencing is its own judgement, not a field; that its own goal comes from the human in chat and its memory is `PARTNER.md`.
  - `hx-worker`: `## Tasks` discipline, commit-as-you-go, one Read per file, subagent use and what `SUBAGENTS.md` gives them, `hx task`, `hx complete` and `HX-CHECK-FAILED`, memory below the header.

autodev's `autodev-operator` and `autodev-gm` skills are dropped: the operator role does not exist and the GM is the Partner. Nothing is symlinked into the user's skill directories.

### 17.6 Build plan from autodev

| autodev module | Disposition in `hx` |
|---|---|
| `storage.py` (`locked`, `atomic_json`) | Reuse the helpers where a lock is genuinely needed (per-stream `seq`); `tasks.json` is an ordinary write |
| `state.py` (root resolution, per-project paths, registry) | Reuse; one instance, `HARNESS_ROOT` |
| `sessions.py` (tmux lifecycle, paste-buffer transport, `pipe-pane` log, launcher script) | Reuse; launch becomes bare (17.4), launcher script keeps the exec-from-file pattern for the env and flags |
| `providers.py` | Claude only; argv per 17.4 |
| `prompts.py`, `identity.py` | Drop; replaced by the pointer and `persona.md` derivation |
| `tasks.py`, `task_plans.py` | Rewrite as work items, `tasks.json`, `resume` (`06`, `08`); the plan graph is dropped entirely |
| `workspaces.py`, `integrate.py`, `contracts.py`, `pillars.py` | Drop; a workdir is a path in `harness.json` and `### Checks` replaces contract checks |
| `config.py` | Rewrite as `models.json`, `harness.json`, `claude.json`, `hx.json`, `ui.json` validators |
| `chat.py` | Drop |
| `fleet.py` | Reuse tmux snapshot and capture; feed `hx board --json` |
| `service.py`, `web/` | Reuse server; rewrite the model (`16`) |
| `skills/` | Replace with `hx-partner`, `hx-worker` |
| `scaffold.py`, `wizard.py` | Replace with `hx install` |
| `cli.py` | Rewrite to the `08-hx-cli.md` command set plus `install`, `up`, `doctor`, `ui`, `show` |
| tests with a fake harness in real tmux | Keep the pattern (`13`) |
