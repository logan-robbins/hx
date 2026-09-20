## 17. Packaging, install, launch, and isolation

### 17.1 Package and instance

Two things exist and are never mixed.

- **Package** `hx`: the autodev rewrite. Python 3.14, zero runtime dependencies, `pyproject.toml`, entry points `hx` and `hx-hook`. Installed with `uv tool install hx` (or `pipx`). Ships `adapters/claude/{install.sh,start.sh}`, `templates/`, `companion/{BASE.md,roles/}`, `skills/{hx-partner,hx-worker}`, `ui/` static files, and the instance skeleton. Upgrading the package never writes into an instance except through `hx upgrade` (17.6).
- **Instance** `HARNESS_ROOT`: the user's data, created by `hx install` (default `/srv/hx` on a server, `~/hx` on a workstation). Holds `config/`, `orders/`, `pods/`, `logs/`, `state/`, `run/`, `archive/`, `seed/`, `repos/`, `wt/` (`03-layout.md`). `config/` is the only part worth committing to the user's own git; everything else is runtime state.

`bin/hx` and `bin/hx-hook` in `03-layout.md` are the package entry points; hook commands in `run/<id>/home/settings.json` reference the absolute path `hx install` recorded in `config/hx.json`, so a package upgrade that moves the binary is followed by `hx upgrade`, not by silently broken hooks.

### 17.2 `hx install` (the one manual command)

1. Refuse root. Check `tmux`, `git`, Python ≥ 3.14, and the `claude` binary; record `{bin, version}` in `config/claude.json`. The version must be in the package's tested list (the list the M6 live suite last passed on); otherwise install stops and says which version to install.
2. Create `HARNESS_ROOT` from the skeleton: `config/CLAUDE.md`, `config/models.json`, `config/partner/{AGENTS.md,SUBAGENTS.md,harness.json}`, `companion/`, `templates/`, empty `orders/`, `pods/partner/`.
3. **Seed token.** Print the two steps the human performs: run `claude setup-token` (their own Claude, interactive) and paste the token into `$HARNESS_ROOT/seed/token`; `hx install` then sets mode 0600 and stops with exit 4 until the file exists. There is no `--from-user-config`: hx reads nothing from `~/.claude`, on any platform.
4. **Mirror the product repo.** `hx repo add <url|path>` creates a bare mirror at `repos/<name>.git` fetched from upstream and records it in `config/repo.json` (`{name, upstream, base_branch, keep_claude_dir: false}`). Worktrees are cut from the mirror at `wt/<id>`; agent branches `agent/<id>` exist only in the mirror. The user's checkout and remote see nothing until the Partner is ordered to push (`hx push <id>` runs `git push upstream agent/<id>` from the mirror). This is the "without messing with the project upstream" guarantee: hx writes nothing into the user's checkout, adds nothing to their repo, and pushes nothing unasked.
5. Install the boot and heartbeat units: launchd plist on macOS (`hx up` at login, heartbeat `StartInterval` 900), systemd user unit plus timer on Linux. Both call the recorded binary path.
6. `hx launch partner`; print `tmux attach -t partner`.

After this the human types nothing but chat.

### 17.3 Same binary, two worlds

The harness runs the same `claude` binary the user already has. Separation is by config dir, worktree, and version pin; nothing about the user's own Claude changes.

| | The user's normal `claude` | A harness session (`start.sh`) |
|---|---|---|
| Config dir | `~/.claude` | `CLAUDE_CONFIG_DIR=$HARNESS_ROOT/run/<id>/home` |
| Settings and hooks | The user's | `home/settings.json` written by `install.sh`: hx hooks, bypass acceptance, `claudeMdExcludes`, instruction-files mode `claude-md` (the real key is `pluginConfigs["agents-md@builtin"].options.instructionFiles`, honoured in the settings file at the root of `CLAUDE_CONFIG_DIR`; verified 2026-09-20 against `docs/en/memory`), Partner `crossSessionInbound: accept` |
| Skills | The user's `~/.claude/skills` | `home/skills/hx-partner` or `home/skills/hx-worker` only |
| CLAUDE.md | The user's and the repo's | `config/CLAUDE.md` only; the repo's is excluded |
| Memory and transcripts | The user's, accumulating | Per home, wiped at every dispatch |
| Credentials | The user's login (macOS Keychain or `~/.claude/.credentials.json`) | A long-lived token in `seed/token`, exported as `CLAUDE_CODE_OAUTH_TOKEN`; the user's login is never read |
| Permissions | Whatever the user chose | Bypass, always |
| Working dir | The user's checkout | `wt/<id>`, a sparse worktree from the mirror, without the repo's `.claude/` |
| System prompt | Default | Default + `run/<id>/persona.md` |
| Version | Auto-updating | Pinned: `DISABLE_AUTOUPDATER=1` in the session env; changed only by `hx upgrade` |
| Goal | None | The `/goal` pointer |
| Visibility | Their terminal | Only through `tmux attach` or `hx ui` |

The repo's own `.claude/` (project settings, hooks, commands) is kept out of harness worktrees by sparse checkout: `git sparse-checkout set --no-cone '/*' '!/.claude/'`. Those settings belong to the user's interactive work and could add hooks or permission rules that fight hx. `config/repo.json` `keep_claude_dir: true` opts a project back in when its `.claude/` carries skills the agents need.

### 17.4 Launch

`start.sh <id>` is the entire launch; nothing else ever starts a Claude Code process:

```
env HARNESS_ID=<id> HARNESS_ROOT=<root> CLAUDE_CONFIG_DIR=<root>/run/<id>/home DISABLE_AUTOUPDATER=1 \
  <config/claude.json bin> --dangerously-skip-permissions --effort <level> --model <full id> \
  --append-system-prompt-file <root>/run/<id>/persona.md
```

No prompt argument, no `--resume`, no compaction variables, cwd `wt/<id>` (`HARNESS_ROOT` for `partner`). `run/<id>/persona.md` is regenerated from `config/<id>/AGENTS.md` above the header immediately before exec. `hx up` at boot runs `hx launch` for every id; `hx launch` runs `start.sh` in window `main` and `hx companion` in window `companion`, and sends the goal if the item is `working`. The human's manual commands, in total: `hx install` once, `tmux attach -t partner` whenever they want to talk.

### 17.5 Skills and instruction files

Operating knowledge is delivered in three layers, none of which touches `~/.claude`:

- `config/CLAUDE.md` (always loaded, short): what the `/goal` pointer means, that the work item is the task list, that the first action after any boundary is one Read of the path the hook printed, that `hx complete` is the last action.
- `config/<id>/AGENTS.md` above the header (system prompt): the project-scoped persona.
- Skills, installed by `install.sh` into `run/<id>/home/skills/` from the package, loaded on demand:
  - `hx-partner`: the order file format and what makes a good definition of done and `### Checks`; `hx launch`, `dispatch` with `after`, `board`, `read`, `resume`, `bench`, `restart`, `push`; what each outcome means and the action for it; how self-dispatch and `goal-pending` work; that the human never runs hx and that decisions go to chat.
  - `hx-worker`: `## Tasks` discipline, commit-as-you-go, one Read per file, subagent use and what `SUBAGENTS.md` gives them, `hx task`, `hx complete` and `HX-CHECK-FAILED`, memory below the header.

autodev's `autodev-operator` and `autodev-gm` skills are dropped: the operator role does not exist (the human runs nothing) and the GM is the Partner. Nothing is symlinked into the user's skill directories.

### 17.6 `hx upgrade`

Package upgrade, then: re-render every `run/<id>/home/settings.json` and `home/skills/` from the new package; if `claude --version` changed, run the M6 live suite against it in a scratch instance before writing the new version to `config/claude.json`; only then restart sessions (`hx restart <id>`, at a boundary, one id at a time). A failed suite leaves the pinned version in place and says why.

### 17.7 Build plan from autodev

| autodev module | Disposition in `hx` |
|---|---|
| `storage.py` (`locked`, `atomic_json`) | Reuse as-is |
| `state.py` (root resolution, per-project paths, registry) | Reuse; one instance, `HARNESS_ROOT` |
| `sessions.py` (tmux lifecycle, paste-buffer transport, `pipe-pane` log, launcher script) | Reuse; launch becomes bare (17.4), launcher script keeps the exec-from-file pattern for the env and flags |
| `providers.py` | Claude only; argv per 17.4 |
| `prompts.py`, `identity.py` | Drop; replaced by the pointer and `persona.md` derivation |
| `tasks.py`, `task_plans.py` | Rewrite as work items, `tasks.json`, `after`, `queued`, `resume` (`06`, `08`) |
| `workspaces.py`, `integrate.py`, `contracts.py`, `pillars.py` | Drop; keep only mirror + sparse worktree creation (17.2, 17.3); `### Checks` replaces contract checks |
| `config.py` | Rewrite as `models.json`, `harness.json`, `claude.json`, `repo.json`, `ui.json` validators |
| `chat.py` | Drop |
| `fleet.py` | Reuse tmux snapshot and capture; feed `hx board --json` |
| `service.py`, `web/` | Reuse server; rewrite the model (`16`) |
| `skills/` | Replace with `hx-partner`, `hx-worker` |
| `scaffold.py`, `wizard.py` | Replace with `hx install` |
| `cli.py` | Rewrite to the `08-hx-cli.md` command set plus `install`, `up`, `doctor`, `ui`, `show`, `repo`, `push`, `upgrade` |
| tests with a fake harness in real tmux | Keep the pattern (`13`) |
