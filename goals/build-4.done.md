# build-4 done: the full `hx install`, the mirror, sparse worktrees, `hx push`, `hx upgrade`, units

## What was built

### 1. `hx install` — all six steps of spec 17.2

`hx install --root <root> [--claude <bin>] [--repo <url|path>]`:

1. **Preflight.** Refuses root (every agent launches with `--dangerously-skip-permissions`,
   which Claude Code itself refuses under root); checks `tmux`, `git`, Python ≥ 3.14; finds
   `claude` on PATH or takes `--claude`; reads its **bare** version and refuses one that is not
   in `src/hx/packaging/tested-claude-versions.json`, naming the version to install and exiting
   5. Writes `config/claude.json` and `config/hx.json`.
2. **Skeleton.** As before, and `--skeleton-only` still stops here.
3. **The seed token.** The one thing hx cannot do for the human. When `seed/token` is missing
   or empty it prints the two steps — `claude setup-token`, then paste — and exits **4**, a
   distinct code so a script can tell "waiting for the human" from "something is wrong". When
   it is there, its mode is tightened to 0600.
4. **The mirror**, when `--repo` was given.
5. **The units**, rendered into this user's own `~/Library/LaunchAgents` or
   `~/.config/systemd/user`. hx writes them and prints the `launchctl`/`systemctl --user`
   commands; it never enables or loads them. Starting something at boot is the human's call.
6. **`hx launch partner`**, then `tmux attach -t partner`.

hx reads nothing from `~/.claude` at any point, on any platform, and a test asserts it by
watching `open` during a real preflight and install.

### 2. `hx repo add` and the sparse worktrees

`git clone --mirror` to `repos/<name>.git`, the remote renamed to `upstream`, and
`config/repo.json` as `{name, upstream, base_branch, keep_claude_dir}`. One repo per instance;
a second is refused. `base_branch` comes from the mirror's own HEAD.

`hx launch <id>` cuts `wt/<id>` from the mirror with `--no-checkout`, applies
`sparse-checkout set --no-cone '/*' '!/.claude/'`, and only then checks out — so the product's
own `.claude/` is never written to disk at all, rather than written and deleted. A test proves
it is absent from the worktree and still present in the mirror, and `keep_claude_dir: true`
opts back in. The branch is `agent/<id>`, or `config/<id>/harness.json`'s `branch`.
`hx dispatch` resets the worktree to `base_branch`, because a dispatch is a fresh start.

### 3. `hx push <id>` — and a hazard worth naming

Pushes one branch to `upstream`, only when asked. A test with a second local bare repo as the
upstream proves the branch lands there, and a second test proves that `hx launch`, `hx dispatch`
and `hx board` leave every upstream ref byte-identical.

**`git clone --mirror` sets `remote.<name>.mirror=true`**, which makes *any* push a force-push
of every ref — including deleting upstream refs the mirror does not have. On a user's own
repository that is a destructive operation hx has no business performing. `hx repo add` unsets
it explicitly, `hx push` refuses if it is ever set again, and the push names one explicit
refspec rather than relying on a default. This surfaced as a plain test failure
(`fatal: --mirror can't be combined with refspecs`); the fix is the interesting part.

### 4. `hx upgrade`

Reads the binary's bare version, refuses one not in the tested list **before writing anything**
so a rejected upgrade leaves the pin intact, then moves the pin. It restarts nothing: a restart
mid-turn throws a turn away, so it prints the `hx restart <id>` instruction instead (spec 17.6).

### 5. `hx doctor` tightened

Now `fail` rather than `warn`: a missing or group-readable `seed/token`, a `config/hx.json`
whose paths are missing or not executable, a home without `settings.json`, an unreachable
mirror (`git --git-dir repos/<name>.git rev-parse HEAD`), and a binary whose version is not the
pinned one. A `--skeleton-only` root therefore now exits 1 and names `seed/token` — correct,
because that root is not a working instance until the human has pasted a token.

### 6–7. Tests, and reaping what a live check launches

A fake `claude` that answers `--version` with a bare tested version, a `HOME` under `tmp_path`,
and a test that watches `open` and asserts no path under the real `~/.claude` is read.

The tmux fixture now kills its server and then **asserts no `partner`/`eng-*` session survived**,
and removes its socket file. Ordering matters: the first version of that assertion listed
sessions before killing the server and failed 112 tests for the wrong reason.

## How it was verified

```
$ ./tools/milestone-check.sh build
== required: tests/guard
== required:  tests/core tests/fakeclaude
MILESTONE-CHECK PASSED for build (own paths; add --all for the advisory run)

$ .venv/bin/python -m pytest tests/core
417 passed
```

### `packaging/e2e-deploy.sh` (the gtm lane's M10 proof) passes end to end

```
== 15. the boot and heartbeat units were rendered into this HOME
   ok  com.hx.up.plist rendered with HARNESS_ROOT and HX_BIN substituted
== 16. hx upgrade refuses a version the suite has not passed on
   ok  refused 9.9.9 (exit 5), naming 2.1.278 as the way out
   ok  config/claude.json unchanged by the refusal
== 18. hx push lands one branch upstream and moves no other ref
   | HX-PUSH eng-001 agent/eng-001 -> …/product.git
   ok  exactly one ref upstream: refs/heads/agent/eng-001
   ok  it matches the mirror, and the source checkout was never contacted
== 19. the real ~/.claude is unchanged
   ok  /Users/loganrobbins/.claude manifest identical before and after

== PASS  full install, mirror, sparse worktree, launch, units — no Claude home touched
```

All 19 steps. The `~/.claude` manifest hash is unchanged across everything in this goal.

## A leaked agent, and who leaked it

After the e2e run I found a live `partner` session with a real `claude` in it on the **default**
tmux server — the one `build-0`, `gtm-2` and `ui-1` run in. I wrote a handoff blaming
`packaging/e2e-deploy.sh` on timing alone. That was wrong, and the session's own working
directory said so:

```
/…/pytest-of-loganrobbins/garbage-…/test_step_1_pins_a_bare_tested0/instance | Python
```

It came from **my own** test. The full `hx install` reaches step 6, `hx launch partner`, and
three of my `test_packaging.py` tests ran it without `HX_TMUX`, so the session landed on the
default server. The gtm script already isolates (`export HX_TMUX="tmux -L hx-deploy-$$"`) and
reaps in an EXIT trap — the shape I had told *them* to adopt. I retracted the handoff in place,
killed the session by name (never `kill-server`, which would take the three lanes with it), and
gave those tests the private server.

This is the second session I have leaked this way. A leaked agent is a bug and not untidiness:
it holds a token, it can still act, and nothing will ever reap it. The teardown assertion added
in item 7 is there so the next one fails a test instead of sitting on the default server.

I also removed ~4,500 stale tmux socket files my own suites had accumulated in
`/private/tmp/tmux-501/`. The `hx-ui-test-*` sockets there are the ui lane's; I left them alone.

## Open questions

1. **`hx upgrade` does not do all of spec 17.6.** It moves the pin. It does not re-render every
   `run/<id>/home/settings.json` and `home/skills/` from the new package, and it does not run
   the M6 live suite against a new binary before pinning it — the live suite does not exist yet.
   Both are cheap to add once M6 lands; say the word and I will, or leave it for the goal that
   builds the live suite.
2. **`hx install` step 5 writes into `$HOME`, which no test can fully prove safe.** The tests
   point `HOME` at `tmp_path`, so they never touch the real `~/Library/LaunchAgents`. That is
   the right test, but it means the real path is exercised only by `e2e-deploy.sh`, which also
   uses a scratch HOME. Nothing has ever written to this machine's real LaunchAgents directory,
   and nothing in the suite can.
3. **The worktree is reset on dispatch, which discards uncommitted work.** `hx dispatch` runs
   `checkout -B`, `reset --hard` and `clean -fd` on `wt/<id>`. That is what "a dispatch is a
   fresh start" means, and the agent is told to commit as it goes — but if an agent is
   dispatched while it has uncommitted changes, they are gone. `hx complete done` already
   refuses a dirty worktree; dispatch does not check. Worth deciding whether dispatch should
   refuse a dirty worktree too, rather than silently discarding.
4. **`base_branch` is read once, at `hx repo add`.** If the product repo's default branch
   changes later, `config/repo.json` keeps the old one until a human edits it. `hx doctor` does
   not check that the recorded `base_branch` still exists in the mirror.

## Handoff entries written

- `handoff/build-to-gtm.md` — the deploy proof passes; plus the retraction above.

## Handoff entries read and applied (marked `DONE` in place)

- `handoff/orchestrator-to-build.md` — the branch name is `agent/<id>`, not `hx/<id>`;
  `hx.repo.branch_for`'s fallback changed and two tests pin it (the default, and
  `harness.json`'s `branch` overriding it).
- `handoff/gtm-to-build.md` — the unit templates are read from the package at
  `hx/packaging/`, substituted by literal `str.replace` of `{HARNESS_ROOT}` and `{HX_BIN}`
  only, with a test that a brace in a comment does not raise and a refusal to write a unit
  that still has a token in it.

## Notes for whoever writes build-5

- `hx.repo.git(*args, cwd=...)` is the one git wrapper; it raises `HxError` with the command
  and its output, which is what made the `--mirror` push failure obvious.
- `hx.claude_bin` holds the pin, the tested list and `bare_version`; `hx upgrade` and
  `hx doctor` both go through it, so a change to version handling has one place.
- The `tmux_server` fixture now enforces reaping. Any new live test gets it for free; a test
  that launches an agent some other way must kill it itself.
