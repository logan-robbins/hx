# greet — the M8 fixture repo

A deliberately tiny product repo, so the M8 end-to-end run (spec 13) is about the harness and
not about the software. Standard library only, no build step, no dependencies: every command in
the two orders' `### Checks` blocks runs anywhere `python3` does.

```
greet.py              the CLI
tests/test_greet.py   stdlib unittest, run with `python3 -m unittest discover -s tests -q`
CLAUDE.md             TRIPWIRE — must never load in a harness session
.claude/settings.json TRIPWIRE — must never be present in wt/<id>
```

## Starting state

English only. `greet.py World` prints `Hello, World!` and nothing else. Two tests pass.

`eng-001` adds `--upper`; `eng-002`, which depends on it, adds `--lang` and has to stop on a
`decision` before it can finish. See `../README.md` for the whole sequence.

## How it is used in M8

`hx repo add <this directory>` mirrors it bare at `repos/greet.git`, and each agent gets a
sparse worktree at `wt/<id>` on branch `agent/<id>` (spec 17.2 step 4, 17.3). Both agents work
in the same product, on separate branches, which is what makes the `after` dependency between
them real rather than decorative.

## The two tripwires

Both files exist to be *absent* from a harness session, and both fail loudly rather than
quietly if the isolation breaks:

- **`CLAUDE.md`** is kept out by `claudeMdExcludes` plus instruction-files mode `claude-md` in
  the harness user's settings. Its instructions contradict the work item, forbid
  `hx complete`, and demand a banner on every reply, so an agent that loaded it is instantly
  identifiable from its transcript.
- **`.claude/settings.json`** is kept out by `git sparse-checkout set --no-cone '/*'
  '!/.claude/'`, so it is not even on disk in a worktree. It registers a `PreToolUse` hook that
  denies every tool call; an agent that loaded it would deadlock on its first action instead of
  silently running with the wrong rules.

Neither is a test by itself. The assertion is M8's: the run completes, and neither
`TRIPWIRE-CLAUDE-MD-LOADED` nor `TRIPWIRE-REPO-CLAUDE-DIR-LOADED` appears anywhere in any
transcript or log.
