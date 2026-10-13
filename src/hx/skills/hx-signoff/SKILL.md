---
name: hx-signoff
description: Sign-off procedure for release and QA engineers in hx. Covers the per-unit loop (focused tests, hx discipline commit, commit, hx discipline pull, push when the goal allows it, required PR checks with gh pr checks) and what to do when the granularity gate says split. Use when a goal names push, PR, CI, release or sign-off, when checks are pending or failing, or when completion refuses an oversized unit.
metadata:
  hx-audience: worker
  hx-roles: release-engineer qa-engineer
  hx-loads-when: a goal names push, PR, CI, release or sign-off; a unit too large to sign off
---

# hx-signoff

Sign-off consumes receipts, not claims. A receipt is a check result tied to the exact commit it
ran on. "Tests pass on my machine" is a claim. "The required checks on PR 42 at head 3f9c1e2
are green" is a receipt. This skill is the loop that produces receipts one behaviour unit at a
time, so each sign-off is small and cheap.

Your persona's rules still apply. Never push, tag or publish unless the goal says so in words.
Never weaken a check to make it pass.

## The loop, once per behaviour unit

### 1. Focused tests first

Run the tests for the unit you just changed, at the tier the goal names. Do not run the whole
suite yet. A unit is one behaviour: a function and its tests, a module and its tests. It is not
"everything since lunch".

```bash
python3 -m pytest tests/test_release_notes.py -q
```

### 2. Ask whether the diff is one unit

```bash
hx discipline commit --repo .
```

```json
{"files": 2, "changed_lines": 74, "source_modules": 1, "has_tests": true,
 "docs_only": false, "verdict": "committable", "reasons": []}
```

`committable` means commit it. `finish-slice` means it can land, but `reasons` names what is
missing (usually a test that travels with the change), so add that first. `split` means the
diff is several units: commit the parts separately, each with its own tests.

### 3. Commit

One behaviour per commit, with a message that says what changed and why. `hx complete done`
refuses a dirty tree, so nothing is left uncommitted at the end.

### 4. Stay current with the principal branch

```bash
git fetch origin && hx discipline pull --repo . --expect fresh
```

Exit 0 means you are current. Exit 3 means the base has commits you lack. The JSON lists
`behind` and `diverged_files`, and the line on stderr names the base. Pull or merge it now while
the drift is small, re-run step 1, and resolve any conflict in this small pull. Never leave it
for one large merge at sign-off. Do this at assignment start, before each new unit and before
sign-off.

### 5. Push and open the PR, only when the goal says so

If the goal does not say "push", "open a PR" or "release" in words, skip to step 7. The checks
in `### Checks` are your receipt. When it does say so, push the branch you are on and open or
update the PR the goal names. Record the PR number and the head SHA in `## Deliverables`.

### 6. Read the required checks

```bash
gh pr checks 42 --required --json bucket,name,link
```

```json
[{"bucket": "pass", "name": "tests (3.14)", "link": "https://github.com/acme/app/actions/runs/1"},
 {"bucket": "pending", "name": "integration", "link": "https://github.com/acme/app/actions/runs/2"}]
```

- Exit 0 with every `bucket` `pass`: the receipt exists for this head.
- Exit 8 means checks are still pending. Wait, then run the same command again. Never complete
  while a required check is pending.
- Any `bucket` of `fail` is a stop. Open its `link`, fix the cause in a new commit, and go back
  to step 1. Do not re-run a failed check in the hope that it flips. A flaky check is a finding:
  write it in `## Deliverables`.
- `gh` missing or not authenticated: CI status is **unverified**, never a pass. Say so in
  `## Deliverables`. If the goal's definition of done needs green CI, you are blocked (see
  below).

Before you finish, check that the PR's head is your last commit:

```bash
test "$(gh pr view 42 --json headRefOid --jq .headRefOid)" = "$(git rev-parse HEAD)"
```

### 7. Finish

Run the goal's `### Checks` yourself first, then complete:

```bash
hx complete done
```

`HX-COMPLETE <id> done` is the only proof that counts. On `HX-CHECK-FAILED`, fix the cause and
run it again. The Partner captures the receipt (PR numbers, head SHA and required checks) after
you complete. Your part is that the PR exists, its head is your last commit, its required checks
are green, and `## Deliverables` names all three.

## When the granularity gate says split

Sign-off measures your whole committed range since the fork point against the repo's own size
norms. It does not measure only the last commit. An oversized unit is
refused with "unit too large to sign off as one behaviour unit … split this unit". That refusal
is an escalation path, not an obstacle to route around:

- Do not squash, reorder or rewrite history to fit under the bound. The gate measures the
  change, not how the commits look.
- Do not drop tests to shrink the diff.
- Name the split yourself: which units, which files each, and in what order they land. Then
  hand it to the Partner. Outside a planned run, use `hx complete decision` with the proposed
  split as the question. Inside a planned run (`hx complete` accepts only `done` there), submit
  the same proposal as a blocker:

```bash
hx progress --file /tmp/split.json
```

where `/tmp/split.json` is your usual progress update with `blocker` set to the proposed split.
The Partner revises the plan and the units land separately.

## When you are blocked

Required checks failing for a reason outside your workdir, `gh` unavailable when the definition
of done needs CI, or a release step the goal did not authorise: say exactly which one in the
first line of `## Open decision`, then

```bash
hx complete blocked
```

Never declare a release done in chat. The checks and the receipt are the record.
