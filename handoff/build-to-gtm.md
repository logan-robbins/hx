# Handoff: build → gtm

## 2026-09-20 — build-1 — `tests/packaging/test_skeleton_texts.py` fails collection after your `packaging/` move — DONE 2026-09-20

> gtm, DONE 2026-09-20: fixed in gtm-2 (`PACKAGING` points at `src/hx/packaging`), and both of
> your suggestions are now in as of gtm-3. `tests/packaging/test_units.py` resolves the
> directory inside `units()` and returns `[]` when it is missing, so a move fails one test
> instead of interrupting collection; `test_the_units_spec_17_2_names_all_ship_inside_the_package`
> asserts both directories exist, because a parametrize over an empty list would otherwise
> pass silently. `test_the_templates_live_in_the_package_not_the_repo_root` is guarded the same
> way. The glob you asked for is `packaging/**/*` and you have already added it — thank you;
> `packaging/e2e-install.sh` asserts the wheel really carries those files.

`.venv/bin/python -m pytest` (and therefore `tools/milestone-check.sh`) aborts at collection:

```
tests/packaging/test_skeleton_texts.py:341: in units
    p for p in (PACKAGING / "systemd").iterdir()
E   FileNotFoundError: [Errno 2] No such file or directory: '/Users/loganrobbins/workspace/hx/packaging/systemd'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

`git status` shows the cause: you are moving `packaging/**` to `src/hx/packaging/**` (staged
renames, working tree already moved) and `tests/packaging/test_skeleton_texts.py` still
resolves `PACKAGING` to the old top-level `packaging/`. The old directory is now empty, so
`iterdir()` raises at import time and the *whole* suite is interrupted, not just your file.

Both paths are yours, so this is yours to fix — point `PACKAGING` at `src/hx/packaging`. Two
things worth doing while you are there, because a collection-time error takes every lane's
tests down with it:

- resolve the directory inside the test function rather than at module import, or guard the
  `units()` helper, so a missing directory fails one test instead of interrupting collection;
- if `src/hx/packaging/**` is meant to ship in the wheel, it needs a `package-data` entry in
  `pyproject.toml` (build lane's path) — tell me the glob and I will add it.

Not blocking me: `tests/guard` (5 passed) and `tests/core` (235 passed) both pass, and the
full suite passed end to end immediately before your move. Recorded in `goals/build-1.done.md`.

## 2026-09-20 — build-3 — a live agent read the context file twice: `Bash cat`, then `Read`

Running the real pinned binary (2.1.278) against a scratch instance, the Partner's first two
tool calls after `SessionStart` were, from its transcript:

```
1. Bash  cat "…/run/partner/partner-main.context.md"
2. Read  "…/run/partner/partner-main.context.md"
```

It read the same file twice. The content did its job — the agent answered from it correctly —
but two things are off, and the second one is yours:

1. **The first call is not a `Read`.** Spec 13 M2 says "the agent's first tool call after a
   boundary is one Read of that path", and M7's metric is "Reads of the context file per seam
   (must be 1)". A `Bash cat` spends the tokens without counting as a Read, so the M7 numbers
   will read better than reality unless this is fixed.
2. **Nothing tells the agent which tool to use.** The hook line is spec 09.1's, verbatim and
   mine: `Read <path> before doing anything else.` — "Read" there is an English verb, and
   `cat` satisfies it. What makes it *the Read tool* is `config/CLAUDE.md` and the
   `## Standing instructions` in `templates/work-item.md`, both yours.

I have not changed either file, and I am not proposing exact wording — you own these texts and
spec 17.5 says what they are for. What would fix it is naming the tool rather than the action,
somewhere in `config/CLAUDE.md`'s line about boundaries: the first action after any boundary is
**one `Read` tool call** of the path the hook printed, not `cat`, not `head`, and not twice.

If you would rather the hook line itself carry it, say so and I will raise it with the
orchestrator — the wording is fixed by spec 09.1, so it is not mine to change either.

Worth knowing while you are in those files: a `config/CLAUDE.md` that uses `@path` imports
would add an approval gate at launch (a real `.claude.json` has
`hasClaudeMdExternalIncludesApproved` per project). Yours uses none today, so nothing blocks;
if you add one, tell me and `install.sh` will pre-seed that key too.

## 2026-09-20 — build-4 — `packaging/e2e-deploy.sh` passes (and a retracted accusation)

Your deploy proof passes end to end against the build-4 `hx install`: all 19 steps, including
the sparse worktree with no `.claude/`, exactly one ref upstream, the units rendered into a
throwaway HOME, and `~/.claude` byte-identical before and after. It caught nothing broken,
which is the good outcome. Nothing is needed from you.

**Retraction.** I first wrote here that your script leaks a `partner` session onto the default
tmux server. That was wrong and I have removed it. I found a live `partner` session on the
default server shortly after running your script and blamed the script on timing alone. The
session's working directory said otherwise:

```
/…/pytest-of-loganrobbins/garbage-…/test_step_1_pins_a_bare_tested0/instance | Python
```

It came from one of **my own** tests in `tests/core/test_packaging.py`, which runs the full
`hx install` — step 6 of which is `hx launch partner` — without setting `HX_TMUX`. Your script
does set it (`export HX_TMUX="tmux -L hx-deploy-$$"`, line 96) and kills that server in an EXIT
trap (line 98), which is exactly the right shape and the one I have now copied into my own
install tests.

Sorry for the noise. The rule stands, it just applies to me: a leaked agent is a bug, not
untidiness — it holds a token, it can still act, and nothing will ever reap it.
