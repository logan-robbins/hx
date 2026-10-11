# Learnings: one central file, written only through review

Every agent in an instance pays for the same lessons unless something carries them across:
the wrong assumption that cost an hour, the correction a reviewer had to make, the gate that
passed on a subset and failed in CI. hx has places for most of what an agent knows, and all of
them are deliberately sharded — the goal, the work item, the memory below the header, the
episode store are each one agent's, one dispatch's, one role's. A lesson owed to everyone does
not belong in any of them. It belongs in one file every agent is pointed at, and that file
must not grow with duplicates or it stops being read.

`LEARNINGS.md` is that file, and `hx learn` is the only way into it.

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

The prefix letter groups rules (`S` for a programme's category rules, `T` for fleet rules,
`L` by default); the number is assigned when the rule is accepted, never by the proposer.

## The process

```
agent, the moment it understands what tripped it:
    hx learn propose --signal "…" --check "…" --evidence "…"
        → learnings/proposals/<id>.json, printed next to the rules it most resembles
        → HX-LEARN-PROPOSED <id> nearest=L3:0.82
          (a near twin is named on stderr: the proposer can switch to --evidence-for L3)

a different session — the Partner on its next wake, a reviewer, the human:
    hx learn pending                       the worklist, each proposal beside its nearest rules
    hx learn accept <id>                   a new numbered rule, in the proposal's words
    hx learn accept <id> --signal … --check …     the reviewer's generalised wording
    hx learn accept <id> --merge-into L3   more evidence for the rule that already says it
    hx learn reject <id> --why "…"         not a category; one line for the proposer

anyone, any time:
    hx learn check                         shape, unique numbers, near-duplicates, pending count
    hx learn list [L3]                     the rules, or one in full
```

The session that proposed a learning cannot accept it: `HARNESS_ID` is compared, and only the
human (no `HARNESS_ID`) may decide on its own proposals. That is the whole mechanism against
drift — the author knows what happened, the reviewer knows what the file already says, and
the file is written only when both have looked. Every decision is one line in
`learnings/decisions.jsonl`; the proposal file moves to `accepted/` or `rejected/` with the
decision in it.

"Nearest" is lexical — token overlap and a sequence ratio over Signal and Check, no model, no
network — because its job is to make the reviewer's first question ("is this already a rule?")
unavoidable, not to answer it. Two accepted rules that score as twins are reported by
`hx learn check` as a warning until someone merges them.

## How agents find out

`hx compile` (which runs on every `hx launch` and `hx restart`) writes a fenced `learnings`
region into every agent's persona, after the global and role regions: the file's path, its
rule count and last rule, the instruction to read it before starting, and the `hx learn
propose` line. No persona has to mention it. The `hx-worker` skill says when to propose;
the `hx-partner` skill says to review on every wake; `hx heartbeat` prints
`learnings_pending=<n>` and wakes the Partner once per new batch of proposals;
`hx doctor` has a `learnings` line.

## One file for a fleet

An instance writes to `HX_LEARNINGS` if set, else the `learnings` path in `config/hx.json`,
else `$HARNESS_ROOT/LEARNINGS.md`. A fleet of instances (docs/multi-instance.md) points every
instance's `config/hx.json` at one shared file:

```json
{"hx_bin": "…", "hook_bin": "…", "python_bin": "…",
 "learnings": "/path/fleet/shared/LEARNINGS.md", "learnings_prefix": "T"}
```

Proposals, decisions and the lock then live in `/path/fleet/shared/learnings/`, so a rule
one area's agent paid for is reviewed by its own Partner against what every area has already
learned, and lands once. `hx learn path` prints where an instance writes.

## What it is not

- Not episode memory (`hx memory`): that is what one agent worked out, searchable, recency-
  weighted, sharded by role. Learnings are rules, few, and read whole.
- Not the agent's memory below the header: that is how *this* codebase is laid out for *this*
  agent. A learning is a category of mistake, with the one-off detail confined to Evidence.
- Not a journal. A proposal that is a story rather than a Signal/Check/Evidence rule is
  rejected with a `--why`, and the proposer is told.
