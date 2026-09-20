# gtm-5 done: the boundary read names the Read tool

Lane `gtm`, goal 5. `tools/milestone-check.sh gtm` exits 0: `tests/guard` (5),
`tests/packaging` (87) and `tests/scenario` (70), nothing skipped. Committed path-scoped.

The four asked-for changes were small and immediate, as intended. Build-4 landed partway
through and turned a deferred gtm-6 item into a red suite in this lane, so the deploy proof is
rewritten here too; that is the larger half of what follows.

## Why this goal existed

The build lane ran the real pinned binary (2.1.278) against a scratch instance, and the
Partner's first two tool calls after `SessionStart` were, from its transcript:

```
1. Bash  cat "…/run/partner/partner-main.context.md"
2. Read  "…/run/partner/partner-main.context.md"
```

It read the same file twice. The content worked — the agent answered from it correctly — but
the first call was not a `Read`, and nothing in the texts told it which tool to use. "Read" in
the old hook line is an English verb, and `cat` satisfies it perfectly well.

That matters beyond tidiness. M7's metric is "Reads of the context file per seam (must be 1)"
and M2's criterion is "the agent's first tool call after a boundary is one Read of that path".
A `Bash cat` spends the tokens and does not appear in either count, so the numbers would read
better than the behaviour — and the whole point of M7 is to find out whether one composed file
is actually enough.

## What changed

### 1. `src/hx/skeleton/config/CLAUDE.md`

The boundary rule now says, in one paragraph: open the context file with the **Read tool**,
exactly once, before anything else; not `cat`, not `head`, not any Bash command, because those
spend the same tokens and do not count as the one read the harness measures; and not again
later in the same turn. Two short paragraphs follow it — the persona is already in the system
prompt so never read a file to find out who you are, and files the working set already covers
are not re-read unless they have changed.

The file stays short — 38 lines — and a new test caps it at 60. It is loaded into every turn of
every agent, so its length is a running cost, and the goal said to keep it that way.

### 2. Both skills

`hx-worker`'s boundary step and a **new** boundary section in `hx-partner` carry the same rule,
each quoting spec 09.1's line verbatim:

```
Use the Read tool once on <path> before anything else; do not cat it and do not read it twice.
```

`hx-partner` having no boundary section at all was a real gap, not just a missing paragraph:
the Partner is a HarnessAgent like any other and takes seams like any other. Its section also
makes a point that only applies to it — the Partner's context file already carries
`PARTNER.md` and the board, so **running `hx board` right after a boundary is the Partner's
version of re-reading a file the working set already covers.**

### 3. `src/hx/skeleton/companion/BASE.md`

A new section, "Reads, and reads that do not count": the metric counts the **tool**, so a
`Bash` read — `cat`, `head`, `less`, `sed -n`, `python -c 'open(...)'` — is invisible to it
while costing the same. The Companion therefore records one:

- a `Bash` read of the **context file** is a boundary failure, written to `dead_ends` as
  `read the context file with Bash <cmd> instead of the Read tool (seq N)`;
- a `Bash` read of a file already in `working_set.files` is the same waste a re-Read would be,
  and is recorded the same way.

Kept until the task completes even under budget pressure, because they are the evidence that
the context file was not enough or was not trusted — which is the question M7 is asking. And
explicitly: never withhold one because the agent got the right answer anyway. The run that
prompted this goal got the right answer.

### 4. `tests/packaging/test_skeleton_texts.py`

Nine new assertions. The two that matter most:

- **the hook line is extracted from `spec/09-hooks.md` at test time**, not hard-coded, and both
  skills must contain it verbatim. When the orchestrator rewords that line, these tests fail
  and the skills get updated — instead of quietly quoting text the hook no longer prints. A
  companion test asserts the spec line still names the Read tool at all, so a reword that drops
  it is caught rather than silently propagated.
- **`config/CLAUDE.md`'s boundary paragraph must name the Read *tool* and forbid `cat` by
  name.** The failure this goal fixes was precisely a text that said the right thing in words
  that admitted the wrong tool, so the test checks the wording, not the sentiment.

Plus: the CLAUDE.md length cap, that it mentions the working set, and that `BASE.md` says
where a Bash read gets recorded.

## Not asked for, but wrong to leave: the auth change

While reading the adapters I found that `CONTRACTS.md` now says, in as many words,
**"`--from-user-config` no longer exists."** The build lane replaced credential seeding with a
single long-lived instance token (spec 11 Auth, 17.2 step 3, `CONTRACTS.md` "seed/token"): the
human runs `claude setup-token` once and pastes the result into `$HARNESS_ROOT/seed/token` at
mode 0600, and `start.sh` exports it as `CLAUDE_CODE_OAUTH_TOKEN`.

`docs/two-worlds.md` and `docs/deploy.md` — which I wrote in gtm-4 — still documented that
flag, a `.credentials.json` copied out of `~/.claude`, and a "seed login" step that no longer
exists. Those are the two pages whose entire purpose is the isolation guarantee, and they
described a read that no longer happens and a command that would fail.

Corrected, from the spec rather than from memory:

- **the headline claim**, which was the worst of it: it said hx reads your `~/.claude` at most
  once to copy one file. The truth is now better and simpler — hx reads it **never**, nor the
  macOS Keychain, where Claude Code actually stores the login.
- `deploy.md` step 3 is the two commands the human really runs.
- `two-worlds.md` says agent homes hold **no credentials file at all**, that the token is never
  an argv element so it cannot appear in `ps`, that both adapters refuse a missing or
  world-readable token, and that revoking the harness's access is one token in one place with
  nothing of the user's entangled in it.
- the dispatch-wipe sentence no longer lists `.credentials.json` among the files that survive
  in an agent home, because there is none.

**`packaging/e2e-deploy.sh` — rewritten here after all.** I had left it for gtm-6 as
sequenced. Then build-4 landed partway through this goal, the gate stopped firing, and the
proof ran against the real install and failed on the contract it was written for. That turned
a deferred item into a red suite in my own lane, which is not something to hand on.

Rewritten for token auth: no `--from-user-config`, a two-phase install instead — the first
stops with exit 4 and is asserted to name the setup-token command and the path to paste into,
then a token is written at 0600 and the install completes with `--repo`. The fake user home is
now planted at `$HOME/.claude` **inside the fresh HOME** rather than passed as a seed source,
which is a stronger assertion than the old one: hx reads no user Claude home at all, so all
four planted strings must be absent from the finished instance and the home must be
byte-identical afterwards.

Sixteen steps, `== PASS`, exit 0, against the real `hx install`.

Four things the first real run found, each a fix rather than a test tweak:

1. **`ok "… \`claude setup-token\` …"` ran the backticks as a command substitution** and hung
   on an interactive login. That is the second time this class of bug has bitten in this
   script; there are none left in it now, and it is the kind of thing that would have looked
   like a mysterious CI timeout.
2. **The fake `claude` cannot pass `hx install` step 1's version check, and should not** — that
   check is the point of the step. The real binary is pinned into `config/claude.json`, and
   `HX_CLAUDE_BIN` points every *launch* at the fake, so no real agent ever starts. A separate
   test asserts that ordering, because install step 6 starts the Partner and getting it wrong
   would silently run an unattended agent from a test.
3. **The token appeared under `run/`** — in `fake-argv.json`, the fake recording its own env,
   which a real binary never writes. Rather than widen the exclusion and lose the assertion,
   that file now carries the *positive* half: the token reached the session as
   `CLAUDE_CODE_OAUTH_TOKEN` and appears in no argv.
4. **The rendered units name `config/hx.json`'s `hx_bin`**, not the uv symlink I happened to
   invoke. The unit was right and my assertion was wrong — and the unit is right for a reason
   worth keeping: replacing the symlink must not silently break a booted fleet.

## One fixture repair

`hx board` now reports a missing or world-readable `seed/token` as an invariant error, which
broke four m8b board comparisons. Fixed in `packlib.build_instance`: it writes a `seed/token`
at 0600. That is the right fix rather than filtering the line out — a scratch instance without
one differs from a real instance in exactly the board text these fixtures exist to compare.

## How it was verified

```
$ tools/milestone-check.sh gtm
MILESTONE-CHECK PASSED for gtm (own paths; add --all for the advisory run)
MC_EXIT=0    # tests/guard 5, tests/packaging 87, tests/scenario 70

$ packaging/e2e-deploy.sh <scratch>
EXIT=0    # 16 steps, == PASS, against the real hx install

$ .venv/bin/python -m pytest tests/packaging/test_skeleton_texts.py -o addopts= -v
test_global_claude_md_names_the_read_tool_and_forbids_cat PASSED
test_global_claude_md_stays_short PASSED
test_skill_quotes_the_hook_line_verbatim[hx-partner/SKILL.md] PASSED
test_skill_quotes_the_hook_line_verbatim[hx-worker/SKILL.md] PASSED
test_skill_boundary_step_names_the_read_tool_and_forbids_cat[hx-partner] PASSED
test_skill_boundary_step_names_the_read_tool_and_forbids_cat[hx-worker] PASSED
test_companion_base_records_a_bash_read_as_waste PASSED
```

The spec extraction was checked by calling it directly:

```
extracted: 'Use the Read tool once on <path> before anything else; do not cat it and do not read it twice.'
```

`templates/work-item.md`'s seven standing-instruction bullets are still byte-identical to the
block in `spec/06-work-items.md` — checked programmatically, since decision D9 pins them and
this goal did not change spec 06.

## Live vs. asserted

**Verified:** that the texts now say what the goal asked, that both skills quote the current
spec line, and that the tests fail if either drifts.

**Not verified, and this is the whole point of the change:** that a live agent now uses the
Read tool. The old wording was also perfectly clear to a human reader and still produced a
`Bash cat`. Whether naming the tool fixes it is a question for the next live run — M2's
reworded criterion, and then M7's per-seam counts. If it does not, the next place to look is
the hook line itself (spec 09.1, the orchestrator's) rather than these texts.

## Open questions

1. **Does naming the tool actually work?** See above. Worth checking on the very next live run
   rather than waiting for M7, because the fix is cheap and the metric depends on it.
2. **The two M2 criteria still pull against each other.** "First tool call after a boundary is
   one Read of that path" and "asked who it is, the agent answers with zero Reads" cannot both
   hold in a first turn that happens to be a question. The build lane flagged it in
   `handoff/to-orchestrator.md`; spec 13 M2 has been reworded once already. Nothing in my texts
   depends on the resolution, but the skills say "before anything else", so if the answer is
   "answering a direct question first is fine" the wording needs a clause.
3. **`hx metrics` still does not exist** (build-8), so `BASE.md`'s new recording rule has no
   consumer yet. It is written so that M7 sees the waste in the step state even if the tool
   counts miss it; whether that is the shape M7 wants is for whoever builds it.

## Handoff entries

**Read and applied.** `handoff/build-to-gtm.md`, their build-3 entry on the double read — this
goal is that entry, and their diagnosis was exactly right. They also noted that a
`config/CLAUDE.md` using `@path` imports would add an approval gate at launch; mine uses none,
and I have not added one.

`handoff/ui-to-gtm.md` arrived during this goal and is marked `DONE` in place. Three notes,
none needing action, all worth having:

- they asked whether `packlib.build_instance` should carry the real order text in `tasks.json`.
  **Declined, with a reason in the reply:** `build_instance` is deliberately pack-agnostic — it
  takes a state table and does not know which pack it serves — and reading a pack's `orders/`
  to build a fixture is a dependency I would rather not add for one caller.
- the board's `after` column and the Orders view's graph differ on purpose, and a reader could
  reasonably expect `expected/` to match the graph. **Added the reason inline to
  `tests/scenario/m8/README.md` step 2** so the next reader does not have to rediscover it.
- a scratch instance sees this machine's real tmux sessions, so `hx board` reported the build
  lane's live `partner` as alive in their tests. `packlib.real_board` already sidesteps it by
  stripping those lines; recorded so that a pack asserting *positively* on liveness knows to go
  through `HX_TMUX`.

**Written:** none this goal. The build lane's entry asked for a change in my files and I made
it; there was nothing to ask back. The auth-driven doc staleness is recorded here and is
already sequenced as gtm-6, so it did not need a new handoff of its own.

## Other lanes

`tools/milestone-check.sh` gained a lane argument during this goal — `tools/milestone-check.sh
gtm` runs `tests/guard` plus this lane's own paths as required, and other lanes' suites only
with `--all`, advisory. This goal closed against that form, exit 0.

The ui lane filed an entry on `tests/packaging` being red, diagnosed it as a missing build-lane
flag, then **went back and rewrote their own diagnosis** an hour later when they worked out it
was my uncommitted rewrite mid-flight. That is the right instinct and worth recording: a wrong
cause left standing in a handoff costs the next reader more than no handoff would. Marked
`DONE` with what actually happened.

Nothing in another lane's path was touched; every commit was `git add <paths> && git commit --
<the same paths>`.

## Commits

```
0c12cb7 gtm: the deploy proof runs for real — build-4 landed mid-goal
3280fb5 gtm: docs follow the auth change — hx reads nothing of the user's, ever
50f9142 gtm: the boundary read names the Read tool, from the first live run
```
