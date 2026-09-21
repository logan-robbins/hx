# gtm-8 done: the `hx-companion` skill and the Companion's prompt for a tmux session

Lane `gtm`, goal 8. `tools/milestone-check.sh gtm` exits 0 — `tests/guard` 6,
`tests/packaging` 103, `tests/scenario` 70. Committed path-scoped.

## What changed

**`src/hx/skills/hx-companion/SKILL.md`** — new. One pass, start to finish: read the pass file,
read the `state:` file if it exists (absent on the first pass is normal, not an error), read the
`log:` from `from_seq`, write one JSON object to the `write:` path, evaluate the seam policy,
say one line, stop. It quotes `CONTRACTS.md`'s pointer verbatim.

`retry_reason` has its own section, including that there is **one retry, not a loop** — a
Companion expecting to iterate will not treat the first failure as expensive, and a pass it
cannot get right silently loses a batch of evidence.

What it must not touch is a list rather than an inference: never write anywhere but the
`write:` path and `run/<id>/seam`; never read the agent's work item, `orders/`, `tasks.json` or
its worktree; never act on the world; never reach the agent. With the one documented exception
of the final pass inside `hx complete`.

**`src/hx/skeleton/companion/BASE.md`** — the contract section rewritten for a file write. The
object goes to the `write:` path with the Write tool, not to stdout. A fenced ```` ```json ````
block is named as *the* failure, because build's own `companion.py` records that a live run had
the model fencing on the first call every time; hx does not strip fences and the prompt says
why, since a tolerant parser would teach that they are acceptable. The seam policy now reads
three of its four inputs from the pass (`context_tokens`, `last_seam_ts`, `open_subagents`)
rather than deriving them. Every retention, evidence and digest rule from gtm-1 and gtm-5 is
unchanged; `roles/*.md` untouched, as the goal says.

**`tests/packaging/test_skeleton_texts.py`** — the assertions gtm-8 asks for: Read and Write as
the only tools with `Bash` absent entirely, the pointer verbatim, all nine pass fields, the
agent's files named as off-limits, `retry_reason` explained once. The pointer is **read out of
`CONTRACTS.md` at test time**, so a reword there fails the skill rather than letting it quote a
line the Companion will never see.

Two existing assertions are scoped to a new `AGENT_SKILL_FILES`: the boundary-read checks are
about an agent at a `/goal` boundary, and the Companion has no goal, takes no seams and never
sees spec 09.1's context line.

## Two things found

**`hx doctor`'s live-agent check races the launch.** It reported
`partner is running without --dangerously-skip-permissions`, which was false. `hx install` step
6 returns as soon as tmux has the session, but `start.sh` then runs its refusals and derives
`persona.md` before it `exec`s; in that window `pane_command` reads the launcher's argv. I
reproduced both outcomes on the same instance. My test now waits for the exec — right for a
test documenting a settled instance — but it is a workaround, and the fix belongs in `doctor`.
Reported with two options and a preference (`warn … starting` rather than `fail`).

**The documented doctor block had gone stale**, which is what the gtm-7 assertion exists for: it
caught two new `sandbox:partner` rows and a changed `repo` line the moment they landed, rather
than months later. `docs/deploy.md` now carries them, with a line saying why the sandbox rows
are the ones worth reading — they check the *running* session, not the configuration meant to
produce it.

## Verification

```
$ tools/milestone-check.sh gtm
MILESTONE-CHECK PASSED for gtm (own paths; add --all for the advisory run)
```

Not verified: no Companion session has read either text. Whether a real one writes a bare
object to the `write:` path on its first pass is build-6's to find out.

## Handoffs

**Written:** `handoff/gtm-to-build.md`, gtm-8 entry — the three pass keys with their exact
forms and two requests about the empty cases (write the key with an empty value rather than
omitting the line; an empty `last_seam_ts` must mean the interval condition *passes*, or an
instance that has never seamed could never take its first one), the two texts and the two
choices in them build-6 must agree with, and the racy doctor check.

**Read:** `handoff/build-to-gtm.md` and `handoff/orchestrator-to-gtm.md` — all prior entries
applied and marked `DONE` in earlier goals; nothing new addressed to gtm.

## Commits

```
8904242 gtm: handoff to build — the three pass keys, the two texts, and a racy doctor check
dea6150 gtm: assertions for the Companion skill; the doctor block gains the sandbox rows
7a8c8f9 gtm: the hx-companion skill, and BASE.md for a session that writes a file
```
