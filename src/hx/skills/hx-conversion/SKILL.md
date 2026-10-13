---
name: hx-conversion
description: How to run a code conversion (porting, migrating or rewriting an existing codebase into a new one) on the shared hx code graph. Index the source repo in its own namespace, register contracts before anyone builds, trace each source capability to its target counterpart with hx code contract, and keep the no-second-interface lint (--expect absent) in every Checks block. Use when a goal converts, ports, migrates or rewrites an existing system, or asks whether a capability has been converted yet.
metadata:
  hx-audience: partner worker
  hx-roles: "*"
  hx-loads-when: a goal converts, ports, migrates or rewrites an existing codebase
---

# hx-conversion

A conversion has two repositories. The **source** is the system that exists and already
behaves correctly. The **target** is the system being built to replace it. This instance's
code graph is one shared store with one namespace per repository, so the source graph and
the target graph sit side by side. Rows in the two namespaces are linked by contract
(interface) name. "Has `InvoiceService` been converted, and where is its counterpart?" is
then one lookup, not a wave of greps across two trees.

This skill is optional. It is installed only on instances whose `config/hx.json` turns it on:

```json
{"skills": ["hx-conversion"]}
```

The commands below come from the code graph's contract registry. If one answers with an
unknown-command usage error on this build, the registry has not landed yet. Say so in the
first line of `## Open decision` and stop. Do not reconstruct the answer by grepping both
trees: a grep cannot tell you what is registered, and a guess here is how second interfaces
get built.

## The order of a conversion

1. **Index both repositories, each under its own namespace.** The namespace is the repo's
   `.hx/map/manifest.json` `repo_id` when it has one, then its first git remote URL, then its
   resolved path. Indexing one repository never removes another's rows.
2. **Contracts first.** The Partner registers the published interfaces (wave 1) before any
   lane builds against them. Nobody implements a counterpart for an interface that is not
   registered.
3. **Trace, then build, one capability at a time.** Look the capability up by name, read the
   source implementation at the path the record gives, build the target counterpart, and
   commit it as one behaviour unit with its tests.
4. **Lint every unit for a second interface.** Every goal's `### Checks` asserts that the
   names a unit must not mint are still absent, so a duplicate fails completion
   mechanically instead of turning up at review.

## Index a repository

```bash
hx code index --repo /srv/legacy-billing
```

Run it once for the source when the conversion starts. The source is the reference and
rarely moves, so re-index it only when it does. Run the same command with your own workdir
(`--repo .`) to refresh the target after you change it. Indexing reads the repository and
writes only instance state: nothing appears in either checkout.

## Trace a capability to its counterpart

```bash
hx code contract InvoiceService
```

This lists every registered record of that name in every namespace. Each record carries its
path, version, producer unit, namespace and git SHA. Read the result as conversion status:

- A source record and no target record: not converted yet. The source path is where you read
  the behaviour you must reproduce. Read it once, then build.
- Records in both namespaces: converted. Extend the target counterpart. Do not write another.
- Two target records with the same name: a second interface already exists. Do not add a
  third. Report it (below), because the plan has to choose one.

The record's SHA tells you which source commit the behaviour was taken from. If the source
has moved since then, re-read the path at its current head before you trust the record.

## The no-second-interface lint

```bash
hx code contract LegacyInvoiceAdapter --expect absent
```

`--expect absent` exits 0 when no record of that name exists and 3 when one does. Exit 2 is
a usage error. Inside a goal's `### Checks` it refuses a duplicate mechanically. The goal
names the interface the unit must not create, because a counterpart already exists or
belongs to another unit, and `hx complete done` fails if that name appears. Workers never
remove or weaken this line. If it fails, either you minted the duplicate (rename onto the
existing contract and re-run the checks) or someone else did (report it).

## Workers: per-behaviour commits

- One capability per unit, one behaviour per commit, tests travelling with each commit. The
  target's tests should pin the behaviour the source exhibits, including its edge cases, not
  only the happy path.
- Name things the way the registered contract names them. A target type called
  `InvoiceServiceImpl2` next to `InvoiceService` is the second interface this skill exists to
  prevent.
- When the source behaviour is wrong (a bug you would be porting), do not silently fix or
  preserve it. Record the question and both options in `## Open decision`, then
  `hx complete decision`.
- When a capability you need has no registered contract, you cannot register one yourself:
  registration is the Partner's. Complete `blocked` and name the missing contract.

## Partner only: register the contracts first

A worker never runs the command in this section: it is refused for a worker caller. Workers
ask through `hx complete blocked` or `hx complete decision` instead.

```bash
hx code contracts --register contracts/billing.jsonl
```

The file holds the published contract records: interface name, path, version, producer unit,
repository namespace and git SHA. Register the source-side records when the source is
indexed, and register each target record as its producing unit lands. Then:

- Plan one unit per capability. Each goal names the contract it implements, the source path
  it converts, and the names it must not mint. Each of those names gets a
  `hx code contract NAME --expect absent` line in `### Checks`.
- A worker's "second interface already exists" report is a planning defect, not a worker
  failure. Choose the surviving interface, and plan the migration of the other one's callers
  as its own unit.
- Track progress with `hx code contract NAME` per capability, not with worker prose. A
  capability is converted when its target record exists at a SHA whose unit completed through
  its checks.
