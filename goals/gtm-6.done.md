# gtm-6 done: the deploy proof for real, docs in the present tense, `hx upgrade` documented

Lane `gtm`, goal 6. `tools/milestone-check.sh gtm` exits 0. `packaging/e2e-deploy.sh` runs to
`PASS` in 19 steps against what build-4 built, with nothing loosened. Committed path-scoped.

## 0. Already done

Step 0 asked for `packaging/e2e-deploy.sh` and `tests/packaging/test_e2e_deploy.py` to be
changed together off the old `--from-user-config` seeding. That happened in gtm-5: build-4
landed mid-goal, the gate stopped firing, the proof ran against the real install and failed,
and leaving a red suite in my own lane was not something to hand on. Both were committed there
(`0c12cb7`). This goal starts from a passing 16-step script and tightens it.

## 1. The deploy proof, tightened

Three steps added, all passing:

**16. `hx upgrade` refuses a version the suite has not passed on.** Two one-line fake binaries,
one reporting `9.9.9` and one reporting the first entry of the package's tested list. The
refusal is asserted to exit non-zero, to explain itself, and to **name a version that would be
accepted** — an error that only says no costs the reader a search. Then the crucial part:
`config/claude.json` is compared byte for byte before and after, because a refused upgrade that
quietly disturbed a working pin would be worse than one that succeeded wrongly.

```
   ok  refused 9.9.9 (exit 5), naming 2.1.278 as the way out
   ok  config/claude.json unchanged by the refusal
```

**17. And accepts one that is in the list**, moving the pin to the new binary and version.

**18. `hx push` lands one branch upstream and moves no other ref.** A second local bare repo is
created, the mirror's `upstream` is re-pointed at it, an empty commit is made in the worktree,
and the upstream's refs are compared before and after. The assertion is the strong form: the
upstream holds **exactly** `refs/heads/<branch>` and nothing else, it matches the mirror, and
the source checkout gains no remote ref. `hx push` is the one command in the system that
reaches a user's remote, so "it pushed the right thing" is not enough — it also has to have
pushed *only* that.

The exit-4 seed-token stop, the `CLAUDE_CODE_OAUTH_TOKEN`-in-env check and the
no-credentials-file check were already in from gtm-5.

### Last 15 lines

```
   | HX-UPGRADE unchanged 2.1.278
   ok  pinned 2.1.278 at …/g6final/bin/claude-tested

== 18. hx push lands one branch upstream and moves no other ref
   | HX-PUSH eng-001 agent/eng-001 -> …/g6final/product.git
   ok  exactly one ref upstream: refs/heads/agent/eng-001
   ok  it matches the mirror, and the source checkout was never contacted

== 19. the real ~/.claude is unchanged
   ok  /Users/loganrobbins/.claude manifest identical before and after

== PASS  full install, mirror, sparse worktree, launch, units — no Claude home touched
   instance …/g6final/hx
   worktree …/g6final/hx/wt/eng-001 (no .claude/)
   units    …/g6final/home/Library/LaunchAgents
```

`EXIT=0`. (Scratch prefix elided as `…`; nothing else changed.)

### Discrepancies found — reported, not worked around

Both are in `handoff/gtm-to-build.md`, the first also in `handoff/to-orchestrator.md`.

**The agent branch has three names.** `repo.py`'s `branch_for` falls back to `hx/<id>`; my
`templates/worker/harness.json` and spec 17.2/17.3 say `agent/<id>`; `goals/gtm-6.md` step 1
says `hx/<id>`. `branch_for` prefers the config, so every worker made from the template gets
`agent/<id>` and the code default is the one nobody sees.

I did not pick a side and did not loosen the script. It reads the branch from
`config/<id>/harness.json` and asserts the substantive thing — exactly one ref upstream,
matching the mirror — so it stays correct whichever way this settles. Worth settling because
`hx push` is the one command that reaches a real remote, and three sources disagreeing about
which branch it sends is how work lands on a ref nobody is watching.

**Settled during this goal: `agent/<id>` wins**, and the build lane changes `repo.py`'s
fallback. Nothing on my side needed editing — the template, both docs and the spec already say
it — and because the proof reads the branch from the config rather than hard-coding either
name, it will keep passing across that change without an edit too. Marked `DONE` in
`handoff/orchestrator-to-gtm.md`.

**`HX-PUSH` reports the wrong upstream when they differ.** `push.py` prints
`config/repo.json`'s recorded `upstream` while `git push` uses the remote's current URL. The
proof re-points the remote, so the run above shows the push going to the new repo while the
line names the old one. Cosmetic in every normal case — and misleading in precisely the case
where someone is reading that line carefully.

## 2. `docs/two-worlds.md`

The "What is built today" hedge is gone, because what it hedged is built. In its place is a
table of **nineteen claims and the file each is true in**: `install.sh` for the settings file
key by key and the skills copy, `start.sh` for the token export and the persona derivation and
the argv, `repo.py` for `SPARSE_RULES` and `keep_claude_dir`, `push.py` for the single
refspec, `upgrade.py` for `require_tested` running before `write_pin`, `dispatch.py` for
`HOME_WIPE`, and `e2e-deploy.sh` for the absence of a credentials file.

Each row was re-checked against the code in this goal, not carried forward from gtm-4. The two
that were still spec-only then — the sparse checkout and the auth path — are now code, and the
sparse row records something better than the spec says: `repo.py` does `--no-checkout` first
and applies the sparse rules before checking out, so the product's `.claude/` is never written
to disk at all rather than written and deleted.

The point of the table is that a page whose only job is to be trusted about isolation should
let a reader check it rather than asking for faith — and it is also what will make the next
drift obvious.

## 3. `docs/deploy.md`

Rewritten around the real transcript. Sections 2–5 are now output from an actual
`packaging/e2e-deploy.sh` run: the six numbered install steps as they print, the **exit-4 stop**
with its exact message and the two commands the human runs, the real rendered unit paths, and
the `launchctl bootstrap` / `systemctl --user enable --now` lines hx itself prints.

Two things the transcript made worth saying explicitly, which I had not known to write before:

- **hx writes the units and deliberately does not enable them.** Starting a fleet at every
  login is the human's decision, not the installer's — the doc now says so rather than leaving
  the reader to wonder why they must run two more commands.
- **`hx install` is idempotent**, so the exit-4 stop is a pause rather than a failure: paste the
  token, run the same command again, it picks up at step 3.

The `hx upgrade` section is new and carries the real refusal and acceptance output, plus the
part that is easy to get wrong: **it does not restart anything.** Running sessions keep the
binary they launched with until `hx restart <id>`, at a boundary, one id at a time, because a
restart mid-turn throws that turn away.

The `hx doctor` sample is now a *healthy* instance — every line `ok`, exit 0 — rather than the
half-installed one the old doc showed, with one sentence on what the `token` line looks like
before the token is pasted.

## 4. `README.md` and `CHANGELOG.md`

README leads with what the deploy proof proves and shows the end of a real run. It also says
plainly what is still unproven: everything the agents do once running — the Companion, seams,
the metrics that judge them — which needs a live Claude Code rather than a packaging script.

CHANGELOG's **Changed** section is rewritten around the auth model, since a reader tracking
credentials needs to know the answer changed from "one file copied from your home" to "nothing
of yours is read at all".

## 5. `.github/workflows/ci.yml`

The `package` job runs `packaging/e2e-deploy.sh` as well as `e2e-install.sh`, and on the Linux
runner then runs:

```
systemd-analyze --user verify $RUNNER_TEMP/deploy/home/.config/systemd/user/{hx-up.service,hx-heartbeat.service,hx-heartbeat.timer}
```

That closes the last "parser-verified only" gap in the packaging story. Those units have never
been seen by a real systemd anywhere: this machine has no container runtime and macOS has no
systemd, so every check on them until now has been an INI parse of my own.

**A coverage regression fixed on the way.** The `test` job ran `tools/milestone-check.sh` with
no argument. That script had just gained a lane parameter, and with no lane it ran
`tests/guard` and exited 0 — so CI would have been green on a broken `tests/core` or
`tests/ui`. CI is nobody's lane and every test is required there, so it now runs `pytest -q`
over everything, and `test_ci_workflow.py` asserts both halves.

The orchestrator has since made the no-lane form mean "everything required", closing the trap
at its source, and confirmed CI should stay on plain pytest regardless. So the fix stands and
only its justification changed: CI states its own requirement rather than inheriting one from
a script whose default has already changed once. The comment in `ci.yml` and the test's
docstring were updated to say that, rather than continuing to cite a trap that no longer
exists.

Validated as before: `actionlint` when present, otherwise the minimal structural YAML parser
with its own negative tests.

## How it was verified

```
$ tools/milestone-check.sh gtm
MILESTONE-CHECK PASSED for gtm (own paths; add --all for the advisory run)
MC_EXIT=0

$ packaging/e2e-deploy.sh <scratch>
EXIT=0    # 19 steps, == PASS

$ packaging/e2e-install.sh <scratch>
E2E_EXIT=0
```

## Live vs. asserted

**Verified for real on this machine:** all 19 deploy steps against the real `hx install`,
`hx repo add`, `hx launch`, `hx upgrade` and `hx push`; the sparse worktree containing no
`.claude/` with the planted string absent; the rendered launchd plists; the token reaching the
session env and appearing in no argv; the real `~/.claude` byte-identical throughout. Every
row of the two-worlds table was read out of the code it names.

**Still not verified:** the systemd units have never been loaded. `systemd-analyze --user
verify` will see them on the next CI run, which has not happened — there is no remote and no
GitHub repository. `launchctl bootstrap` has likewise never been run on the rendered plists;
the proof checks their content, not that launchd accepts them. And `ci.yml` itself has still
never executed.

Everything in the deploy path used the **fake** `claude` for launches. No real agent has been
started by this script and none should be: `HX_CLAUDE_BIN` points every launch at the fake, and
a test asserts that export happens before the first `hx install`, because install step 6 starts
the Partner.

## Open questions

1. **`launchctl bootstrap` on the rendered plists** has no equivalent of the systemd check —
   `launchctl` on a CI macOS runner is in a different session context and bootstrapping a real
   agent there would be a poor idea. The plists are lint-clean and parse; whether launchd
   accepts them is first proven when a human runs the two commands `hx install` prints.
2. **`hx doctor` on a fully-installed instance is now in the docs**, taken from the deploy
   proof's own instance. It is not asserted anywhere — if doctor's output format changes, the
   doc goes stale silently. Worth an assertion in a later goal if the format settles.

## Handoff entries

**Read before starting.** `handoff/build-to-gtm.md` (their build-3 entry, applied in gtm-5),
`handoff/orchestrator-to-gtm.md` (all gtm-5 answers applied; its plan change is what this goal
is written against), `handoff/ui-to-gtm.md` (closed in gtm-5). No new entries addressed to gtm
arrived during this goal.

**Written by me:**

- `handoff/gtm-to-build.md`, gtm-6 entry: the proof passes against build-4 and what it will
  hold them to, plus the two discrepancies above — stated as observations with a preference,
  not as defects, because neither is breaking anything today.
- `handoff/to-orchestrator.md`: the three-way branch-name disagreement with two ways to settle
  it, and the CI under-run the lane-scoped `milestone-check.sh` made possible.

## Other lanes

`tools/milestone-check.sh gtm` exits 0. The build lane's tree was busy throughout — `cli.py`,
`dispatch.py`, `doctor.py`, `install.py`, `lifecycle.py` and a new `claude_bin.py` all moved
during this goal — and nothing in their paths was touched by me. Every commit was
`git add <paths> && git commit -- <the same paths>`.

## Commits

```
d6f7360 gtm: apply the orchestrator's answers — agent/<id> wins, CI stays on pytest
d6d0fd1 gtm: to orchestrator — the branch name disagrees three ways; CI under-ran
7225189 gtm: handoff to build — deploy proof passes; the branch name and the HX-PUSH line
6713e3a gtm: the docs are the real transcript, in the present tense
0ae6e4d gtm: tighten the deploy proof with upgrade and push; CI runs it and verifies the units
```
