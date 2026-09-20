# Handoff to the orchestrator

> Orchestrator, 2026-09-20: all items below are answered in `handoff/orchestrator-to-build.md`. DONE.


## 2026-09-20 — build lane — `tests/guard/test_user_home_untouched.py` fails on a file hx never touched

`tests/guard/test_user_home_untouched.py` fails at the start of goal build-1, before the build
lane wrote anything:

```
+ e03b9b7ccfed665ce24190c52acaf55d3b5860a7a8494f5151c63aef714fee04  plugins/known_marketplaces.json
- fa598acef3e7d9aa3d136f30c709249de79371b949a460ff08cb9429b0a0f81b  plugins/known_marketplaces.json
```

`~/.claude/plugins/known_marketplaces.json` has mtime 13:07:41, the baseline was recorded at
13:06. The only difference in the file is the `lastUpdated` timestamp of the
`claude-plugins-official` marketplace entry (`"lastUpdated": "2026-09-20T20:07:41.893Z"`):
Claude Code's own marketplace refresh, run by one of the three lane sessions starting up. No hx
code existed at that point and no lane ran `/plugin`.

Per ORCHESTRATION.md I am not editing `.baseline/`, `tools/claude-home-hash.sh`, or
`tests/guard/**`. Two options for you:

1. Re-record `.baseline/` now that the marketplace refresh has happened (it is once-per-day-ish,
   so it will recur), or
2. add `plugins/known_marketplaces.json` to the exclusion list in `tools/claude-home-hash.sh`
   with a comment saying why — it is Claude Code's own cache bookkeeping, like
   `plugins/cache/` and `plugins/marketplaces/` which are already excluded, and nothing hx does
   can write it (hx never touches `~/.claude`).

The other guard file, `tests/guard/test_harness_root_refusal.py`, passes as of this goal.

## 2026-09-20 — build lane — questions raised by `goals/build-1.md`

1. **`hx: <cmd>: not implemented (build-N)`** — `N` is a placeholder. I resolved it to the
   *build-lane goal number* that is expected to deliver the command, derived from spec 13
   (goal `build-1` = M0, so `build-<milestone+1>`); e.g. `hx: dispatch: not implemented
   (build-2)`. The mapping lives in one dict, `hx.cli.NOT_IMPLEMENTED`, and is trivial to
   change. Tell me if you meant a literal `build-N`, or a different numbering.

2. **`hx install` step numbering** — build-1 says "step 3 in 17.2 only: the skeleton under a
   given `HARNESS_ROOT`; steps 1, 2, 4–6 come later", but the skeleton is 17.2 **step 2**
   ("Create `HARNESS_ROOT` from the skeleton"); step 3 is the seed login. I built the prose
   ("the skeleton under a given HARNESS_ROOT. Copy `src/hx/skeleton/**` into the root"), i.e.
   17.2 step 2, and left steps 1 and 3–6 unimplemented. `hx install` requires
   `--skeleton-only` until the rest lands.

3. **`crossSessionInbound` scope** — build-1 item 5 lists `crossSessionInbound: accept` among
   the keys `adapters/claude/install.sh` writes, without qualification; spec 05 and 11 and
   17.3 scope it to the Partner ("for the Partner, `crossSessionInbound: accept`"). I followed
   the spec: `run/partner/home/settings.json` gets it, worker homes do not. Say so if every
   home should carry it.

4. **`config/hx.json`** — spec 17.1 says hook commands reference "the absolute path `hx install`
   recorded in `config/hx.json`" but no section defines the file's keys. `install.sh` reads
   `{"hook_bin": "<abs path>"}` and falls back to `$HARNESS_ROOT/bin/hx-hook` (the literal path
   in spec 09) when the file is absent. If you want different keys, say so and I will change
   `install.sh` and the future `hx install`.

5. **`hx doctor` exit code** — spec 08 says doctor exits 1 with the list of failures (seed
   credentials, every home's settings, mirror reachability); build-1 item 1 says it "exits 0".
   For M0 doctor fails (exit 1) only on things M0 owns — missing `tmux`, `git`, Python < 3.12,
   a `config/claude.json` whose `bin` is missing or not executable — and reports everything
   else (missing skeleton files from the gtm lane, missing seed credentials, missing homes) as
   `warn` lines with exit 0. It tightens to the spec 08 rule when those milestones land.

## 2026-09-20 — gtm lane — three questions from goal gtm-1

> Orchestrator: answered in `handoff/orchestrator-to-gtm.md`. DONE.

**1. Does the example worker belong in the installed skeleton?**

`goals/gtm-1.md` item 3 asks for "an example worker `config/eng-001/{AGENTS.md,SUBAGENTS.md,
harness.json}`", and the ORCHESTRATION path table gives gtm
`src/hx/skeleton/config/<example-id>/**`. I built it there, so `hx install` creates
`config/eng-001/` in every fresh instance. But spec 17.2 step 2 lists only
`config/CLAUDE.md`, `config/models.json`, `config/partner/{AGENTS.md,SUBAGENTS.md,harness.json}`,
`companion/`, `templates/`, empty `orders/`, `pods/partner/` as what the instance is created
with — no worker.

Shipping it installed means a fresh root has an `eng-001` that `hx up` will launch, `hx board`
will hold to its invariants, and the human never asked for. Shipping it *not* installed means
the example is documentation only and the Partner has to author a worker config from scratch
on day one, with nothing to copy.

I have left it installed (the goal's wording is explicit), and told the build lane not to
depend on it. Say which you want; moving it is a one-line change on my side.

**2. `packaging/tested-claude-versions.json` entry format.**

The goal says the first entry is `<claude --version in your session>`. That command prints
`2.1.278 (Claude Code)`. I normalised to the bare version — `{"versions": ["2.1.278"]}` — so it
can be compared against the `version` in `config/claude.json` (spec 17.2 step 1: "The version
must be in the package's tested list"). If `config/claude.json` is meant to hold the full
string including the suffix, this file should too, and I will change it. Flagged to build as
well.

**3. `CONTRACTS.md` could pin the work-item template placeholders.**

Spec 06 fixes the content of `templates/work-item.md` but not how `hx dispatch` substitutes
into it. I chose `{{id}}`, `{{pod}}`, `{{after}}`, `{{dispatched}}`, `{{order}}` (details and
rendering rules in `handoff/gtm-to-build.md`), and my test asserts that exact token set. It is
a two-lane contract — gtm writes the template, build renders it — so it may belong in
`CONTRACTS.md` rather than in a handoff note. Not editing `CONTRACTS.md` myself.


## 2026-09-20 — ui lane — `CONTRACTS.md` has no shape for the Orders and Archive views

> Orchestrator: adopted verbatim into `CONTRACTS.md`; commands added to spec 08 and to `goals/build-2.md`. DONE.

Spec 16.2 gives the Orders and Archive views sources (`tasks.json`, `orders/`,
`pods/*/archive/`, `archive/`) but no command, and `CONTRACTS.md` defines only `hx board
--json`, `hx show <id> --json` and `hx wake partner`. Goal ui-1 requires both views, so the ui
lane wrote the two shapes below and built `tests/ui/fixtures/orders.json` and
`tests/ui/fixtures/archive.json` to them. Both reuse blocks `CONTRACTS.md` already fixes, so
there is little new to agree. Please rule: adopt into `CONTRACTS.md` as written, amend, or tell
the ui lane to render from `tasks.json` and the directory listings directly with no command.

Whatever you decide, the build lane needs to know whether `hx orders --json` and
`hx archive --json` exist at all — neither is in the spec 08 command table, so today there is
nothing for `InstanceSource` to call in ui-2.

**`hx orders --json`** — every `orders/*.md` and addendum with the `tasks.json` record it
produced, plus the `after` graph:

```json
{
  "root_abs": "/srv/hx",
  "ts": "2026-09-20T13:10:00Z",
  "orders": [
    {
      "id": "eng-002",
      "pod": "engineers",
      "path": "orders/eng-002.md",
      "after": ["eng-003"],
      "order": "…orders/eng-002.md verbatim…",
      "addenda": [{"ts": "…", "path": "orders/eng-002.addendum.md", "text": "…"}],
      "record": {"order": "…", "after": ["eng-003"], "addenda": [{"ts": "…", "text": "…"}],
                 "outcome": null, "dispatched": "…", "completed": null},
      "state": "queued",
      "ready": false,
      "waiting_on": ["eng-003"],
      "file_matches_record": true
    }
  ],
  "graph": {
    "nodes": [{"id": "eng-002", "state": "queued", "outcome": null, "ready": false}],
    "edges": [{"from": "eng-003", "to": "eng-002", "met": false}]
  },
  "errors": []
}
```

- `record` is the `tasks.json` entry for that id, which is exactly the `task` block of
  `hx show <id> --json` minus nothing — same six keys. `null` when the order file exists but
  was never dispatched, and then `state` and `file_matches_record` are `null` too.
- `waiting_on` is the subset of `after` whose `tasks.json` outcome is not `done`; `ready` is
  `waiting_on == []`, the same rule as the board. This is what spec 16.2 calls "which queued
  items wait on which ids".
- `file_matches_record` is `orders/<id>.md` on disk compared with the order recorded at
  dispatch. It is the one fact this view can show that nothing else can: the Partner edited the
  order file after dispatch, so what the agent is running is not what the file now says. If you
  would rather the UI not surface that, drop the key and we drop the badge.
- `edges` is one entry per `after` relation, in `orders` order; `met` mirrors `waiting_on`.

**`hx archive --json`** — benched bodies and archived dispatches per id:

```json
{
  "root_abs": "/srv/hx",
  "ts": "2026-09-20T13:10:00Z",
  "items": [
    {
      "id": "eng-001",
      "pod": "engineers",
      "bench":   [{"ts": "…", "path": "pods/engineers/archive/eng-001-<ts>.md", "digest": "…"}],
      "archive": [{"ts": "…", "path": "archive/eng-001/<ts>", "digest": "…"}]
    }
  ],
  "errors": []
}
```

- The `bench` and `archive` entries are the `hx show <id> --json` `bench` and `archive` entries
  verbatim — same three keys, same meaning — so this view is the whole-fleet form of what
  `hx show` already returns per id. An id with no history yet has two empty lists.

## 2026-09-20 — ui lane — the SSE `changed` list carries one scope that is not an id

> Orchestrator: accepted, `tasks` is the one reserved scope; pinned in `CONTRACTS.md`. DONE.

Spec 16.1 says the server pushes "the ids that changed", but one watched path is not
id-shaped: `tasks.json` is a single file whose every write can change any row of the board.
The ui lane emits it as the reserved scope name `tasks` alongside the ids:
`{"changed": ["tasks", "eng-001"]}`. `partner` and `[a-z]+-[0-9]{3}` are ids; `tasks` is the
only reserved name in instance mode. If you would rather `tasks.json` fan out to every id it
mentions, say so — it costs a parse of `tasks.json` inside the once-a-second sweep, which is
why it was not done that way.

The other four paths spec 16.1 lists all carry an id in the path and need no reserved name
(`pods/<pod>/<id>-<state>.md`, `orders/<id>.md`, `state/<id>/…`, `logs/<id>/…`,
`run/<id>/turn`, `run/<id>/goal`).


## 2026-09-20 — ui lane — `tests/guard/test_user_home_untouched.py` fails on `file-history/`

> Orchestrator: agreed, an oversight; pruned in commit 4a30e69 and the baseline re-recorded. Guard passes. DONE.

Second instance of the class the build lane reported above, different path. At the end of goal
ui-1 the guard test fails with three added files:

```
+ 1203af169bc049eec24a0bfd36c0e5ab9413e45f7d0e4979d16abc8a51dd8e04  file-history/0bd6d45e-b525-4cb5-8154-62e4ac2dc927/0402e04f721b8628@v1
+ 140235e0e99af0942ecf16a818ea1255860ffd183aac99a91ef0dc1ab9fd4b54  file-history/0bd6d45e-b525-4cb5-8154-62e4ac2dc927/b13d97ffe4c4751f@v1
+ 367c435323354e5cea4b553c370ca2523e4afd56e815d8c460c67fbc053af5b7  file-history/0bd6d45e-b525-4cb5-8154-62e4ac2dc927/e4abdd7a06a5740a@v1
```

`~/.claude/file-history/` is Claude Code's own pre-edit snapshot cache: one copy of each file a
session's Edit/Write tool touched, before the edit. The three snapshots are of
`config/eng-001/SUBAGENTS.md`, `config/eng-001/harness.json` and `config/eng-001/AGENTS.md`, so
they were written by whichever lane session is building the skeleton — session
`0bd6d45e-b525-4cb5-8154-62e4ac2dc927`, which is not the ui session
(`cd595057-fbf9-4c6e-ac74-30de1b461e27`). No hx code wrote them: `file-history/` is written by
the editing session itself, and the ui lane contains no reference to `~/.claude`, `Path.home()`
or `expanduser` at all (`src/hx/ui/**`, `tests/ui/**`).

This will recur on every goal in every lane, because every lane edits files with Claude Code.

`tools/claude-home-hash.sh` is yours and the guard test's own docstring says not to edit the
exclusion list to make it pass, so I have not. `file-history/` reads like an oversight rather
than a decision: it is exactly the "written by the user's own Claude sessions, never by hx"
category the script's comment describes, and its siblings `projects/`, `sessions/`,
`shell-snapshots/` and `paste-cache/` are already excluded. The comment block even lists
"`file-history/` (pre-edit snapshots)" when describing spec 08's `home/` wipe, so the name was
in view when the list was written.

Suggested: add `-not -path './file-history/*'` to both `find` invocations, alongside
`./projects/*`. I have not touched it. `tests/ui` is unaffected either way: 151 passed,
1 skipped.

> ui lane, 2026-09-20: you fixed this in `4a30e69` ("guard manifest: prune file-history") while
> this entry was being written — `tests/guard` is back to 5 passed. Left in place as the record
> of why; no action needed. DONE 2026-09-20.

## 2026-09-20 — build lane — a lane's `git commit` swallowed another lane's staged files

> Orchestrator: rule adopted in ORCHESTRATION.md "Git" (`git commit -- <paths>`), all lanes told. No history rewrite; the content is right and attribution in this repo is not worth a rewrite. DONE.

`238d01e gtm-1 done: …` contains the whole of the build lane's M0 (every `src/hx/*.py`,
`src/hx/skeleton/adapters/claude/*`, `tests/core/**`, `tests/fakeclaude/claude`) alongside the
gtm lane's own files. Nothing is lost and the content is exactly what the build lane wrote,
but the commit message and the attribution are wrong for 22 of the files in it.

Cause: one shared working tree and one shared index. The build lane ran `git add <explicit
paths>` and, in the seconds before its own `git commit`, the gtm lane ran a `git commit`
that took everything staged in the shared index, not just its own paths.

`git add <explicit paths>` — which ORCHESTRATION.md already requires — is not enough on its
own, because the index is shared. The fix is to make the commit itself path-scoped:

    git add <explicit paths> && git commit -m "…" -- <the same explicit paths>

The `-- <paths>` form commits the named paths from the working tree and ignores whatever else
is sitting in the shared index, so a concurrent lane's staged files cannot ride along. I have
switched to it for the rest of this goal. Suggest adding it to ORCHESTRATION.md "Git", since
every lane is exposed to this and it silently mis-attributes work rather than failing.

I have not rewritten history: `git reset` and `git rebase` are forbidden, and the tree content
is correct. Re-attribution, if you want it, is yours to decide.
