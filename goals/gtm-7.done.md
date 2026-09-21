# gtm-7 done: docs and skills after M4, the doctor assertion, the Companion prompt for `claude-cli`

Lane `gtm`, goal 7. `tools/milestone-check.sh gtm` exits 0 — `tests/guard` 5,
`tests/packaging` 94, `tests/scenario` 70. Committed path-scoped.

## 1. The doctor assertion

`tests/packaging/test_docs_match_reality.py` builds a real instance — the two-phase `hx
install` with a seeded token, the fake `claude` for launches, a private tmux server it kills —
and compares `docs/deploy.md`'s `$ hx doctor` block against what `hx doctor` actually prints.
**23 rows**, and they match.

**What it compares, and why not the whole text.** Every doctor line is
`<status>  <name>  <detail>`, and the detail is absolute paths, versions and commit shas that
differ per machine and per run. Comparing those would fail for reasons that are not staleness,
and the steady pressure would be to loosen the test until it checked nothing. So it compares
the ordered `(status, name)` pairs. That catches a check being added, removed, renamed, or
flipping between `ok` and `warn` — which is every way this block has actually gone out of date
— and ignores what legitimately varies. The module docstring says so, because the next person
to see it fail will be deciding whether to weaken it.

Four smaller assertions beside it: the six numbered install steps are quoted in the doc, the
seed stop is documented as exit 4, the Companion is described as `claude -p` with no tools on
**every** page that introduces it rather than just one, and no doc still names the old
`hx/<id>` branch.

**A bug this found in my own work.** My first version located the block with
`re.findall(r"```\n(.*?)\n```")`. That skips a ` ```bash ` opener and then pairs *that* block's
closing fence with the next opener, so it silently returned prose as if it were a code block —
which is why it first reported zero doctor blocks in a document that plainly has one. I spent a
few minutes convinced `deploy.md`'s fences were unbalanced before checking, and they were fine.
Replaced with an eight-line line-by-line scanner that also asserts the fences balance.

## 2. Docs after M4

`docs/two-worlds.md` gains two things.

**The Companion's session.** It is a Claude Code session too: `claude -p`, one shot per batch,
on the same pinned binary and the same instance token, in a config home of its own at
`run/<id>/companion-home` that is deliberately bare — no hooks, no skills, no CLAUDE.md. And
**no tools at all**: its whole input is a system prompt plus stdin and its whole output is one
JSON object, so it cannot read a file or run a command even by accident. That is worth saying
where a reader will look for it — the thing that watches every agent is the one participant
with no ability to act. `provider: claude-cli` also means an instance with no API key still
gets a Companion, which is the practical half.

**What M4's hooks write, and where.** A table: the main stream one line per tool call with
head excerpts and a `ref` into Claude Code's own transcript; a stream per subagent, opened on
start and renamed on stop; `run/<id>/subagents.json` holding the `agent_id → sNNN` map assigned
under a lock; `run/<id>/turn` after every turn; the Companion's step state. All inside
`HARNESS_ROOT`, none of it sent anywhere, the Companion the only reader of the streams and the
agent never reading them at all.

Six rows added to the claim table, each naming the file it is true in. Two are facts I would
not have written from the spec: `context_tokens` is **input plus cache reads, not output**
(`transcripts.py`), because what matters is what the next turn has to fit; and the seam marker
is touched **only on the main stream**, because a subagent's window is its own.

`docs/deploy.md` says the systemd units are verified by a real `systemd-analyze` in CI while
the launchd plists are not and cannot sensibly be — so the two `launchctl bootstrap` commands
it prints are the first time launchd itself sees them, and an error there is worth reporting
rather than working around. `README.md`'s instance sketch gains the subagent streams, the turn
marker, `seed/token`, and `branch agent/<id>`.

## 3. `companion/BASE.md`: the output contract first

It reaches the model as `--append-system-prompt-file` to `claude -p` with the state and records
on stdin, so a model may act on the top of the file before it has read the rest. The contract
is now the first section: **one JSON object, no prose, no fence**, the eleven keys of spec 07.2
in a table with their types, the empty forms spelled out (`[]`, `{}`, `""` — never `null`,
never a missing key), and the consequence stated plainly — an invalid or oversized answer is
discarded and the previous state kept, so a malformed reply loses a whole batch of evidence
silently.

Every rule from gtm-1 and gtm-5 is kept. What changed beyond the new section: the call is
described as what it is (one stateless `claude -p` whose prefix is identical call to call so
the binary's own caching pays for it, with no tools and nothing to call), and a subagent
stream's "task" is the message it was spawned with — which is what `compose.py` actually puts
there after build-5, not what spec 07.3 says.

Two choices in it are flagged to the build lane because build-6's validator has to agree: the
answer is **the whole object, never a patch**, and near the budget it **evicts rather than
truncating**. Both are offers, not assertions — I will change either if `hx` does something
else.

`roles/*.md` are unchanged; nothing in `handoff/build-to-gtm.md` asked.

`provider` is now `claude-cli` in both shipped `harness.json` files. They still said
`anthropic` from gtm-1, and `validate_harness` treats `provider` as a free string, so nothing
had caught it.

## 4. Skills, checked against the hooks rather than the spec

`hx-worker` gains, from `hook_subagent.py` and `compose.py`:

- **a subagent's task is the message it was spawned with**, already in its conversation, and
  the context file says so rather than repeating it. So that message is the whole of what it
  knows about the job — the skill now says to write it as you would write an order, not as a
  one-line handle. That is build-5's live finding, and it is not what spec 07.3 implies.
- handles assigned under a lock, so three subagents starting at once get three streams;
- **the digest is all that crosses back** — not the transcript, not the stream, not the step
  state — so anything the parent needs that the digest omits is gone;
- a new section on what happens at the end of every turn, because two of its consequences are
  the agent's to act on: **finishing the turn is what lets the harness act at all**, and
  background work keeps pushing a seam out.

`hx-partner` gains the `goal-pending` path stated as a rule rather than a mechanism — let the
turn end, do not wait inside it for a goal that only arrives after it — because that is the one
that bites: waiting would hang the Partner's own self-dispatch. And where the subagent picture
lives (`hx show`), including that "still has subagents running" is a normal state rather than a
stuck one, since `hx complete` refuses while a stream is open.

## 5. The M8 pack can be driven

`tests/scenario/m8/README.md` gains a **Driving it** section: setup (with `HX_TMUX` on a
private server, since a leaked agent holds a token), the eight steps with the `HX-` line each
prints — `HX-DISPATCH`, `HX-PROMOTED` inside step 3's output, `HX-COMPLETE` as the last line of
`hx complete`'s stdout, `HX-RESUME`, `HX-BENCH` — and what to assert beyond the board text:
`hx board` exiting 0 at every one of the eight points, the human running no hx command,
neither tripwire string appearing anywhere, `eng-002` reaching `done` by way of `decision` and
a resume rather than a re-dispatch, and at least one subagent stream existing.

The part I most wanted written down: **steps 3, 4, 6 and 7 only mean anything if the agents run
them.** They are `hx complete` calls from inside the agents' own sessions with `HARNESS_ID` set
by `start.sh`. A test that ran them on the agents' behalf would pass while proving nothing.

**`expected/` is unchanged.** M4 did not move the board text — all eight still match the real
`hx board`, which the pack test verifies by building each state and running it.

One test widened: the README now has two tables that both walk the sequence, so "every
`expected/` file is named exactly once" became set equality in both directions, with the reason
in the docstring.

## How it was verified

```
$ tools/milestone-check.sh gtm
MILESTONE-CHECK PASSED for gtm (own paths; add --all for the advisory run)
MC_EXIT=0        # tests/guard 5, tests/packaging 94, tests/scenario 70

$ .venv/bin/python -m pytest tests/packaging/test_docs_match_reality.py -q
6 passed         # including the 23-row doctor comparison against a real instance
```

Every skill and doc claim added in this goal was read out of the module it names —
`hook_stop.py`, `hook_subagent.py`, `compose.py`, `transcripts.py`, `hook_log.py`,
`config_harness.py` — rather than from the spec those implement.

## Live vs. asserted

**Verified:** the doctor block against a real instance built by the real `hx install`; that
`expected/` still matches the real `hx board` after M4; that both skills quote spec 09.1's hook
line verbatim, which still passes after the M4 edits.

**Not verified, and the honest limit of this goal:** none of the prompt text has been read by
the model it is written for. `BASE.md`'s output contract is written the way a system prompt
should be written; whether `claude -p` with it actually returns bare JSON on the first call is
build-6's to find out, and I would not be surprised by a fenced ```json wrapper on some
fraction of calls. The M8 drive sequence has never been run either — it is written from the
command surfaces and the `HX-` lines in the code, not from a run.

## Open questions

1. **Does the Companion return bare JSON?** If build-6 finds fences or preamble, the fix is
   probably one line in the contract section (naming the exact failure) rather than a parser
   that strips fences — a parser that tolerates it removes the pressure to fix it. Worth
   deciding which way before the workaround gets written.
2. **Should `provider` be a closed set?** `validate_harness` accepts any string, which is why
   `anthropic` survived in both shipped files after the decision moved to `claude-cli`. Raised
   with build; a one-line change, and it would have caught this.
3. **The skills are getting long.** `hx-worker` is eleven sections now. The docs cap
   `SKILL.md` at 500 lines and it is well under, but length is a running cost for a file loaded
   on demand into a working agent. If M8 shows the workers are not using the later sections, a
   pass that moves the reference material into a sibling file is the answer.

## Handoff entries

**Read.** `handoff/build-to-gtm.md` — their build-4 entry says the deploy proof passes and
needs nothing, and includes a retraction of an accusation that my script leaked a `partner`
session onto the default tmux server; it was one of their own tests, and they had already
corrected it before I read it. Their build-3 entry on the double read was applied in gtm-5.
`handoff/orchestrator-to-gtm.md` — all answers applied and marked `DONE` in gtm-6.
`handoff/ui-to-gtm.md` — closed in gtm-6. No new entries addressed to gtm arrived during this
goal.

**Written.** `handoff/gtm-to-build.md`, gtm-7 entry: the output contract and the two choices in
it that build-6's validator must agree with, `provider: claude-cli` in both shipped files with
a note that nothing rejected the old value, the M8 drive sequence with the warning about which
steps only mean something when the agents run them, and that the eight expected boards are
unchanged by M4.

## Other lanes

`tools/milestone-check.sh gtm` exits 0. The build lane's tree moved throughout — the M4 hook
modules and `transcripts.py` are what several of this goal's claims were read out of — and
nothing in their paths was touched. Every commit was `git add <paths> && git commit -- <the
same paths>`.

## Commits

```
ddf8443 gtm: handoff to build — the Companion prompt, claude-cli, and driving M8
5680b3c gtm: changelog for M4, the claude-cli Companion, and the docs assertion
9797a18 gtm: hold docs/deploy.md's doctor block to the real output; the M8 drive sequence
4506bac gtm: the skills carry M4's behaviours, checked against the hooks in the tree
5adf67b gtm: docs reflect M4 — the streams, the Companion's session, agent/<id>
73a116b gtm: the Companion's output contract, and provider claude-cli
```
