# Learnings: one directory of category files, written only through review, in every system prompt

Every agent in an instance pays for the same lessons unless something carries them across:
the wrong assumption that cost an hour, the correction a reviewer had to make, the gate that
passed on a subset and failed in CI. hx has places for most of what an agent knows, and all of
them are deliberately sharded — the goal, the work item, the step state, the episode store are
each one agent's, one dispatch's, one role's. A lesson owed to everyone does
not belong in any of them. It belongs in one place every agent carries, and that place must
not grow with duplicates or it stops being read.

`LEARNINGS/` is that place — one file per category of mistake — and `hx learn` is the only
way into it. Every rule in it is in the system prompt of every agent it applies to, whole: a
global rule in every agent's, a rule scoped to a role in every agent of that role.

## The taxonomy

The categories are fixed in hx (`hx.learn.CATEGORIES`, `hx learn categories`). A rule is
filed by the kind of mistake it prevents, the file name says which, and nobody — agent,
Partner or human — invents a category. A closed set is the point: a category nobody can
invent is a category every agent can find, and a file that holds one kind of thing can be
read, diffed and compacted as that kind of thing.

| File | Holds rules about |
|---|---|
| `orientation.md` | where answers live and what to open first: named entry files, what never to read, status from a tool run and never from memory |
| `ownership.md` | claims, selection and boundaries: what may be worked on or written, by whom, under which authorization |
| `planning.md` | shaping a work item: lanes, contracts and stubs, gates to define, sizing, sequencing |
| `domain.md` | the product under work: semantics decided from source, data access, what fixtures and seeds mean |
| `build.md` | the local build and test loop and the tools in it |
| `testing.md` | what a test must prove: fixtures, coverage scope, regressions from captures, never weakening a test |
| `runtime.md` | the running application: journeys, browser proof, captures, services, ports, live status |
| `evidence.md` | what a claim must carry: receipts, counts with scope, raw versus reconstructed, CONFIRMED versus HYPOTHESIS, timing and failure logs |
| `delivery.md` | definition of done, CI gates, git and PR safety, delivery status, merge, handoff contents |
| `harness.md` | hx and fleet mechanics: claim, complete, wake, boards, goals, handoff, compaction, seats and quotas |
| `environment.md` | the box and the shell: paths, binaries, versions, disks, process control, footguns |

The skeleton ships all eleven, empty. Each file is a fixed header and a `## Rules` list;
`hx learn check` fails any rule line in a file that is not one of the eleven.

## The rule shape

One line per rule, three cells, invariant wording:

```
**Rule L7** · Signal: what you see when it is about to bite · Check: the mechanical test that decides it · Evidence: where it happened (path, run, PR, date)
```

- **Signal** is what the next agent will actually see — a symptom, not a diagnosis.
- **Check** is something a script or a reviewer can decide. A rule whose check is a grep
  becomes a lint in the goal's `### Checks`; the rule stays as its explanation.
- **Evidence** is the only cell that may name a file, a run, a PR or a date. The rule itself
  must still hold for a program nobody has seen yet.

The prefix letter groups rules by programme (`S` for a programme's inherited rules, `T` for
fleet rules, `L` by default); the number is unique per prefix across the whole directory and
is assigned when the rule is accepted, never by the proposer. The category is the file; the
key does not encode it, so a rule can be refiled without renumbering and a citation (`S93`)
never goes stale.

## Scope: every agent, or every agent of one role

A rule is global unless it names a role. A scoped rule carries `Role: <role>` first after its
key, the role as `harness.json` names it:

```
**Rule L9** · Role: backend-engineer · Signal: … · Check: … · Evidence: …
```

The scope is the reason per-agent memory is gone: a lesson one backend engineer paid for is
owed to every backend engineer, from its next launch, and to nobody else. The proposer says
who it is for (`hx learn propose --role backend-engineer`; without `--role` it is global);
the reviewer decides (`hx learn accept ID --role R` to scope it, `--global` to widen it), the
same way it decides the category. A text proposal (below) is scoped to the role of the agent
it came from. A merge keeps the scope of the rule it merges into. `hx learn list` prints the
scope beside each rule, and `--role R` (or `--role global`) filters by it.

## The process

```
agent, the moment it understands what tripped it:
    hx learn propose --category evidence --signal "…" --check "…" --evidence "…" [--role R]
        → LEARNINGS/proposals/<id>.json, printed next to the rules it most resembles,
          whatever category they are in
        → HX-LEARN-PROPOSED <id> category=evidence scope=global nearest=L3:0.82
          (a near twin is named on stderr: the proposer can switch to --evidence-for L3)

a different session — the Partner on its next wake, a reviewer, the human:
    hx learn pending                       the worklist, each proposal beside its nearest rules
    hx learn accept <id>                   a new numbered rule, in the proposal's words and category
    hx learn accept <id> --category build  the reviewer's category: the proposer named the wrong kind of mistake
    hx learn accept <id> --role R | --global   the reviewer's scope: one role, or every agent
    hx learn accept <id> --signal … --check …     the reviewer's generalised wording
    hx learn accept <id> --merge-into L3   more evidence for the rule that already says it
    hx learn reject <id> --why "…"         not a category; one line for the proposer

anyone, any time:
    hx learn check                         categories, shape, unique numbers, near-duplicates across files, pending count
    hx learn list [L3] [--category c] [--role r|global]   the rules with their scope, or one in full
    hx learn categories                    the taxonomy
```

The session that proposed a learning cannot accept it: `HARNESS_ID` is compared, and only the
human (no `HARNESS_ID`) may decide on its own proposals. That is the whole mechanism against
drift — the author knows what happened, the reviewer knows what the directory already says,
and a file is written only when both have looked. The reviewer's second job is the category:
a proposal filed under the wrong kind of mistake is accepted with `--category`, not rejected.
Every decision is one line in `LEARNINGS/decisions.jsonl` (with the category); the proposal
file moves to `accepted/` or `rejected/` with the decision in it.

"Nearest" is lexical — token overlap and a sequence ratio over Signal and Check, no model, no
network — and runs across every category file, because its job is to make the reviewer's
first question ("is this already a rule?") unavoidable, not to answer it. Two accepted rules
that score as twins are reported by `hx learn check` as a warning, with both categories,
until someone merges them.

## How agents carry them

`hx persona` (which every `start.sh` runs immediately before it execs) renders a fenced
`learnings` region into every agent's persona: the directory's path and rule count, the propose line, and
then **every global rule, whole**, under a `### <category> — <definition>` heading per non-empty
category, in taxonomy order, then **every rule scoped to the agent's role**, under
`### <role> · <category>` headings. Rules scoped to another role are not in it. The adapters pass the persona as the system prompt
(`--append-system-prompt-file`), so the rules are present at every start and after every
compaction with no Read, and prompt-cached with the rest of the invariant prompt. No agent
is told to read the files; the persona tells it not to.

The Partner keeps its own persona file, `config/partner/AGENTS.md`, and gets the learnings
region after it like everyone else. A rule accepted today is in every agent's system prompt
from its next launch.

### Text proposals

Some lessons arrive as prose rather than as a Signal and a Check: what an agent wrote below
the old `## UPDATES BELOW ONLY` header before the learnings were the channel (`hx.persona`
migrates it), and the directives `hx distill` promotes out of goal addenda. Each becomes one
pending **text proposal** (`"kind": "text"`, the prose in `text`), authored as the agent it came
from so that agent cannot accept it. hx does not guess rules out of prose: `hx learn pending`
prints the text, and the reviewer accepts each lesson it holds as a rule with its own wording,
`hx learn accept ID --signal … --check … [--category …] --keep` while more remain (the last
accept drops `--keep`), or `--merge-into` an existing rule, or rejects it. `accept` refuses a
text proposal without `--signal` and `--check`.

The size is visible: `hx doctor` prints the rule count per category and warns
(`learnings-size`) when the largest region any configured role renders passes
`hx.learn.PERSONA_WARN_BYTES` (200 KiB, roughly 50k tokens). The answer to a large region is merging twins and refiling,
not trimming what agents see: the whole point is that nobody has to decide what to load.

The `hx-worker` skill says when to propose; the `hx-partner` skill says to review on every
wake; `hx heartbeat` prints `learnings_pending=<n>` and wakes the Partner once per new batch of
proposals; `hx doctor` has a `learnings` line.

## One directory for a fleet

An instance writes to `HX_LEARNINGS` if set, else the `learnings` path in `config/hx.json`,
else `$HARNESS_ROOT/LEARNINGS/`. A fleet of instances (docs/multi-instance.md) points every
instance's `config/hx.json` at one shared directory:

```json
{"hx_bin": "…", "hook_bin": "…", "python_bin": "…",
 "learnings": "/path/fleet/shared/LEARNINGS", "learnings_prefix": "T"}
```

Proposals, decisions and the lock then live in that directory, so a rule one area's agent
paid for is reviewed by its own Partner against what every area has already learned, and
lands once, in one file, for every instance's next launch. `hx learn path` prints where an
instance writes.

## Migration

hx before this layout kept one `LEARNINGS.md`. `hx learn` refuses a `learnings` value that
names a `.md` file, and `hx doctor` warns (`learnings-legacy`) while a `LEARNINGS.md` sits in
the root. To move: create `LEARNINGS/` (`hx install` ships the eleven empty files; copy them
from `src/hx/skeleton/LEARNINGS/`), put each `**Rule …**` line under the category of the
mistake it prevents — a second model is a reasonable classifier; the Partner is the reviewer
— keeping its key, move `learnings/{proposals,accepted,rejected,decisions.jsonl}` into the new
directory, point `learnings` at the directory, run `hx learn check`, and archive the old file
outside the directory. Prose that was not a rule (definitions, status tables, checklists)
either becomes a rule in Signal/Check/Evidence form or leaves the learnings for the place its
kind of information belongs.

## What it is not

- Not episode memory (`hx memory`): that is what one agent worked out, searchable, recency-
  weighted, sharded by role. Learnings are rules, few, and carried whole.
- Not a per-agent notebook. There is none: what one agent worked out about a codebase is a
  rule for every agent of its role (`--role`), with the one-off detail confined to Evidence,
  or it is step state, which the episode store already keeps.
- Not a journal. A proposal that is a story rather than a Signal/Check/Evidence rule is
  rejected with a `--why`, and the proposer is told.
- Not a place for status, definitions-as-prose or checklists. A definition becomes a rule
  (`Signal: the word "finished" used for a lane · Check: …`); a checklist lives with the
  cards or templates that use it and cites rules by key.

## The invariant cascade and never-twice

Context cascades from most invariant to most volatile: the base model,
global policy, the learnings (whole, in every system prompt), the persona
and role, the assignment brief, then the current task packet. Learnings sit
near the top because a paid-for mistake is the most valuable context any
later agent can receive — every agent must never fall for the same mistake
or flawed reasoning twice.

Three mechanisms keep that true. Classification happens at the moment of
learning, while the mistake is concrete: `hx learn classify` ranks the
fixed categories deterministically and the proposer chooses from that
shortlist. Repetition is detected, not hoped away: `hx learn repeat`
reports `repeat` when a signal already matches a rule, and the correct
action is merging new evidence into that rule — a repeat is also the
measurable failure of the cascade, worth counting. And the invariant has a
size law: `hx learn invariant` charges every rule byte-for-token against
the prompt invariant budget (`hx cache budget`); past the ceiling the most
valuable context starts evicting working room, so wording is compressed
before rules are added.

The repeat check also runs by itself at completion. When `hx complete done` fails its
`### Checks`, or a planned run's acceptance check fails in `native_completion`, the first
2,000 bytes of the failing output become the signal for `hx learn repeat`. A `repeat` or
`related` verdict appends `learnings: repeat of <Rule> (score)` to the `HX-CHECK-FAILED`
output (or to the pasted "Acceptance check … failed" line) and one record to
`state/learn/repeats.jsonl`; a `new` verdict writes nothing. It is advisory: the check alone
decides pass or fail, and a scoring error is silently ignored. `hx metrics` counts the records
per rule since that rule was last accepted or merged into (`summary.learn_repeats`). Raw check
output is noisier than a proposed signal, so the threshold is to be reviewed against
`repeats.jsonl` after the first live wave before a repeat is treated as a cascade failure.
