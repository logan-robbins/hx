---
name: hx-memory
description: How to search this instance's episode memory with `hx memory search` — every step state every agent has ever written, indexed and recency-weighted. Use when picking up a work item, when hitting a blocker someone may already have hit, when asking how something was done in this repository before, or when you need what an earlier release or merge actually did.
---

# hx-memory

Every step state your Companion writes is also stored as an **episode**: a dense summary of
what an agent was doing, what it decided, what it closed, what it committed and what it gave
up on. Every agent in this instance writes to the same store, so the memory is the fleet's,
not yours alone. `hx memory search` is how you read it.

## Before you search: the file you already have

Your context file has a **Memory episodes** section, already filtered to your role and already
excluding your own state. Read that first. It is free — you have it in front of you — and it
is the answer often enough that a search is a wasted tool call.

Search when that section is empty, when it does not cover what you need, or when the thing you
need is older than the few episodes that fit there.

## The search

```bash
hx memory search "conditional GET 304 on the media API"
```

That is the whole of the normal case. Facts about it:

- **Your own role is the default filter.** A backend engineer searches backend episodes. That
  is usually what you want: the same kind of work, the same parts of the tree, the same
  commands.
- **Results are recency-weighted.** A recent episode beats an older one of equal similarity,
  because the repository moved. What you get back is not "the best match ever recorded", it is
  "the best match that is still likely to be true".
- **Query in your own words.** It is a semantic search over the episode text, not `grep`. Give
  it the problem, the symbol, the error, the area — `"alembic revision for the assets table"`,
  `"npm publish 403"`, `"where the redis client is constructed"`.

Widen only when the own-role result is empty, or when what comes back is clearly about a
different area than the one you asked about:

```bash
hx memory search "npm publish 403 on the private registry" --all-roles
```

`--all-roles` drops the role filter and nothing else. Reach for it deliberately: a frontend
episode about a release step is often exactly right, and a frontend episode about components
is noise in a backend search.

Other flags, when you know what you are narrowing to:

```
--role R          episodes written for that role instead of yours
--pod P           one pod
--id ID           one agent
--kind K          pass | seam | compact | complete   (complete = an agent's final Digest)
--k N             how many hits (default 5)
--half-life-h H   recency half-life in hours (default 24)
--json            machine-readable, for when you are processing rather than reading
```

Each hit prints a header line — timestamp, agent id, role, kind, `seq`, score — and then the
episode text.

## The other subcommands

```bash
hx memory list [--id ID] [--role R] [--limit N]   # newest first, metadata only
hx memory stats                                   # counts by role and kind, queue length
hx memory index                                   # force-drain the write queue into the index
```

`search` indexes what is pending before it queries, so `index` is only for when you want the
work done now rather than on the next search.

## When to reach for it

- **Picking up a work item.** Search the area before you start: someone may have built half of
  it, or learned why the obvious approach does not work here.
- **You hit a blocker.** Search the error text or the thing that is in your way. A blocker that
  stopped another agent is usually recorded with what would lift it.
- **"How is X done in this repository?"** Conventions, commands that actually work here, where
  a thing lives. Cheaper than reading the tree.
- **A release, a merge, or anything already shipped.** `--kind complete` gets the Digests: what
  was delivered and which commits carry it.

## What it is not

It is not authoritative about the present. An episode is what was true when an agent wrote it;
the repository is what is true now. Use memory to know where to look and what not to repeat —
then verify against the tree before you act on it.

It is also not your own memory. What you want to carry into your *next* task goes below
`## UPDATES BELOW ONLY` in your `AGENTS.md`, as always.
