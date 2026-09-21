# Evaluating the Companion (M7)

The Companion is the unproven part of hx. Everything else in the design is mechanism —
filenames, locks, hooks, a `/goal` pointer — and either works or does not. The Companion is a
bet: that a small model reading an agent's tool-call stream can keep a bounded, structured
record good enough that cutting the agent's conversation and rebuilding it from one file costs
almost nothing.

M7 is where that bet is settled. This document is the plan for whoever builds it. No code here,
and nothing in it is a decision to be taken later — the metric, the pass bar, and the procedure
are all fixed by spec 13 (M7), 07.4, and decision D8 in spec 14.

## The claim under test

> After a seam, a HarnessAgent continues from its context file **without re-reading files its
> `working_set` already notes, and without repeating a dead end**.

A seam is `/clear` plus rehydration (spec 02, 09.3). The agent loses its conversation and is
handed one path. If the Companion did its job, the agent's next ten turns are work. If it did
not, they are the agent rediscovering what it already knew — which is both the cost this whole
design exists to avoid and, conveniently, something you can count.

## The corpus

**Where it comes from:** recorded raw streams from M6 runs. M6 is the first milestone against a
live Claude Code (spec 13), so it is the first point at which the streams contain real agent
behaviour rather than the fake `claude`'s scripted payloads. Nothing before M6 is admissible —
a corpus of fixtures would measure the fixtures.

**What a corpus entry is:** one completed task, captured whole:

- `logs/<id>/<id>-main.jsonl` and every subagent stream, from dispatch to `hx complete`;
- the `tasks.json` entry: order, addenda, outcome, timestamps;
- the final work item body, including `## Tasks` as the agent left it and its `## Digest`;
- `config/<id>/AGENTS.md` as it stood at each point (persona above the header, memory below);
- the workdir's `git log --oneline`, which is what `closed_steps[].commit` refers to.

**How it is captured:** `hx dispatch` already archives `logs/<id>/` and `state/<id>/` to
`archive/<id>/<ts>/` at the start of every dispatch (spec 08), so a completed task's evidence
survives the next one by default. Capture is therefore copying an `archive/<id>/<ts>/`
directory out of a scratch instance, plus the four items above. It needs no new hx command and
no instrumentation in the hot path.

**What makes a good corpus entry:** a task long enough to seam at least five times naturally,
with at least one dead end and at least one fact the agent had to read a file to learn. A task
that never re-reads anything cannot distinguish a good Companion from a broken one. Aim for a
spread: one long refactor, one debugging task with a wrong hypothesis, one task with three
parallel subagents, one that ended `decision` and was resumed.

**Size:** enough tasks that a change of one or two points is not noise. Start at ten and let
the variance between runs on the same corpus tell you whether that is enough — if replaying the
same entry twice moves the metric more than a prompt change does, the corpus is too small or
the replay is not deterministic enough to draw conclusions from.

## Five seam points per task

Each corpus entry is replayed with the seam taken at **five points**, spread across the task —
not five consecutive turns, and not five points in the first quarter. The useful spread is by
progress through the work, roughly evenly through the closed steps: an early seam has little
state to carry and a late one has a lot, and a Companion that only works at one end is not
working.

At each point:

1. Replay the stream into the Companion from the beginning up to that point, exactly as
   `hx companion` would, so the step state is what it would really have been. The Companion is
   stateless per call and takes the previous step state plus the records after its cursor, so a
   replay is faithful by construction.
2. Compose the context file with `hx compose <id> <id>-main`.
3. Start a fresh HarnessAgent, hand it that path the way the `context` hook does, and let it
   run ten turns under the same `/goal`.
4. Record the metric.

Step 3 is a live Claude Code session, which is why M7 comes after M6 and why this is not a
pull-request check. It costs real tokens per data point: ten tasks × five seams × ten turns.
Budget for it deliberately rather than discovering the cost mid-run.

## The metric

`hx metrics <id>` reads it back from the `seam` records hx appends to the stream at every
boundary (spec 07.4), which carry `prompt_version`, the `context_tokens` before the seam, and
the context file's size. Per seam (spec 13 M7, D8):

| Measure | What it counts | What it means |
|---|---|---|
| **`working_set` re-Reads** | Read tool calls in the ten turns after the seam whose path appears in `working_set.files` of the step state that produced the context file | **Waste.** The Companion recorded a note about that file and the agent went back to it anyway, so either the note was missing the fact the agent needed or it was not trusted |
| **Other tool calls** | Every other tool call in those ten turns | **Work**, roughly. Not free of waste, but not waste this metric can attribute |
| **Context-file Reads** | Reads of the context file path itself | **Must be exactly 1.** More than one means the agent could not hold it; zero means it never read it, and the seam failed outright |

Two further things to record per seam, not as pass/fail but because they are what you will
reach for when a number moves and you need to know why:

- **Repeated dead ends** — an approach in `dead_ends` attempted again in the ten turns. Harder
  to count mechanically than a re-Read, so score it by reading the transcripts of the seams
  whose other-tool-call count is an outlier, rather than trying to automate it up front.
- **`prompt_version`** — the shas of `companion/BASE.md` and `companion/roles/<role>.md`,
  stamped on every step-state write and carried in the seam record. This is what makes a
  before/after comparison meaningful, and it is why it exists.

## The pass bar

M7 passes when, across the corpus:

- **context-file Reads == 1 at every single seam.** Not a mean — every seam. A zero or a two is
  a mechanism failure, not a tuning problem, and it is `config/CLAUDE.md`, the `context` hook,
  or `hx compose` that is wrong, not the Companion.
- **`working_set` re-Reads are near zero, and every non-zero one has been looked at.** The
  target is zero. A re-Read that turns out to be legitimate — the agent needed a second fact
  from a file whose note recorded only the first — is a finding about the note-writing rule in
  `companion/BASE.md`, not an acceptable background rate. Write down which it was.
- **no dead end is repeated.**
- **step state stays inside `state_budget_tokens`** at every point, which `hx` already
  validates on write (spec 07.2) — an eval run that never trips it on the longest task in the
  corpus is evidence the eviction order is right.

A failure here changes the design, not the decision to have a Companion (spec 14: "A failed
test changes the design, not the decision's existence"). The likely changes, in the order worth
trying: the note-writing rule for `working_set.files`; the eviction order; the `next` field's
phrasing rule; the section order in the context file (spec 07.3); and only then the budget.

## Judging a prompt change

The Companion's prompts — `companion/BASE.md` and `companion/roles/<role>.md` — are the tuning
surface. Every change to either is judged the same way:

1. **Same corpus, same seam points.** Nothing about the replay changes. If the corpus changes,
   the comparison is void and both sides must be re-run.
2. **Run before and after.** The `prompt_version` in each seam record is the sha of the two
   files, so which side a data point belongs to is recorded rather than remembered.
3. **Compare per seam, not only in aggregate.** A change that halves re-Reads on long tasks and
   doubles them on short ones has a mean that says nothing. Look at the distribution.
4. **A change that does not move the metric is not an improvement**, however much better the
   prose reads. The prompts are long and each rule in them costs cache and attention; a rule
   that buys nothing measurable should be deleted rather than kept for tidiness.
5. **Record the result next to the change.** A prompt change lands with its before/after
   numbers in the commit message. Six months later, the numbers are the only reason anyone will
   be able to tell which rules in `BASE.md` are load-bearing.

Role files are judged on the tasks of that role only — an `engineer` change against engineer
tasks, `reviewer` against review tasks. A change to `BASE.md` is judged across the whole corpus,
because it applies to every Companion on every stream.

## What this does not measure

- **Subagent continuity.** No hook fires on a subagent's own compaction (spec 09.4), so there
  is nothing to rehydrate and nothing to score. The mitigations — scoping and commit-as-you-go
  — are asserted elsewhere.
- **Digest quality.** The `## Digest` is read by the Partner, and whether it was good enough is
  visible in what the Partner does next, which M8 exercises end to end. It is not part of this
  metric and should not be bolted on: a Companion tuned to write better prose for a human
  reader is not necessarily one that carries state better across a seam.
- **Whether the agent did good work.** This measures continuity across a cut, nothing else. An
  agent that continues smoothly in the wrong direction scores perfectly.
