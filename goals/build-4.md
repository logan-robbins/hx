# build-4: `hx install` complete, `hx repo add`, sparse worktrees, `hx push`, `hx upgrade`, units

Pulled forward from the end of the plan: M6 live tests need seeded homes and worktrees, and the
gtm lane's M10 proof (`packaging/e2e-deploy.sh`) needs the whole install. Read
`goals/build-3.done.md`, `handoff/gtm-to-build.md` (the `--from-user-config` contract, the unit
substitution keys `{HARNESS_ROOT}`/`{HX_BIN}` with literal replacement, package-data paths),
`handoff/orchestrator-to-build.md`, spec 17.2 (all six steps), 17.3, 17.6, 11 (Worktree, Binary
and version), 08 (`hx repo add`, `hx push`, `hx upgrade`, `hx launch` worktree creation).

## Build

1. `hx install --root <root>`: step 1 refuse root, check `tmux`,
   `git`, Python ≥ 3.14, find `claude` on `PATH` (or `--claude <bin>`), bare version must be in
   `src/hx/packaging/tested-claude-versions.json` else stop and say which to install; write
   `config/claude.json` and `config/hx.json`. Step 2 skeleton (exists). Step 3 seed token: print the two
   human steps (`claude setup-token`, paste into `<root>/seed/token`), set mode 0600 when the
   file appears, and stop with exit 4 until it exists. No `--from-user-config`; hx reads nothing
   from `~/.claude` or the Keychain. Step 4 `hx repo add`
   if `--repo` given. Step 5 render the five unit templates from `src/hx/packaging/` with
   literal replacement into `~/Library/LaunchAgents` or `~/.config/systemd/user` of the current
   `HOME`, never enabling or loading them (print the `launchctl`/`systemctl --user` commands the
   human runs). Step 6 `hx launch partner`.
2. `hx repo add <url|path>`: bare mirror at `repos/<name>.git` (`git clone --mirror`),
   `config/repo.json {name, upstream, base_branch, keep_claude_dir: false}`; refuse a second
   repo. `hx launch <id>` (non-partner) creates `wt/<id>` from the mirror as a sparse worktree
   on branch `hx/<id>` from `base_branch`, `git sparse-checkout set --no-cone '/*' '!/.claude/'`
   unless `keep_claude_dir`; a test proves `.claude/` is absent from the worktree and present in
   the mirror. `hx dispatch` resets the worktree to `base_branch` (not for `partner`).
3. `hx push <id>`: push `hx/<id>` from the mirror to `upstream`, only when invoked; nothing else
   ever contacts `upstream`. Test with a second local bare repo as the upstream.
4. `hx upgrade`: read `claude --version`, refuse unless the bare version is in the tested list,
   then update `config/claude.json`; `hx doctor` fails when the recorded version is not the
   binary's.
5. `hx doctor` tightens: seed credentials, `config/hx.json` paths, every home's settings and
   credentials, mirror reachability (`git --git-dir repos/<name>.git rev-parse HEAD`) become
   `fail`.
6. Tests: every step with a fake `claude` that prints `2.1.278 (Claude Code)` for `--version`
   and a fake user config dir under `tmp_path`; the real `~/.claude` is never read in tests
   (guard test stays green and a test asserts no path under `Path.home()/.claude` is opened, by
   patching `open`/`Path.read_*` or by `HOME=tmp_path`).

## Done when

- `tools/milestone-check.sh` passes for `tests/guard` and `tests/core`.
- `packaging/e2e-deploy.sh <scratch>` (gtm lane) passes if it exists; paste its tail.
- Committed path-scoped. `goals/build-4.done.md` written, with handoffs.
