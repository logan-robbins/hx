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


## 2026-09-20 — ui lane — ui-2 needs `hx show --json`, which build-2 has not landed yet

> Orchestrator: Ruling: close ui-2 without it; record the 503 and paste `/api/board`. The switch to real commands and functions is ui-3, which goes out after build-2 lands. DONE.

Goal ui-2 item 3 says to call "`.venv/bin/hx board --json` and `hx show <id> --json` via
subprocess" until `handoff/build-to-ui.md` arrives. `hx board --json` works. The other four
commands `InstanceSource` needs do not exist yet — `cli.py` answers them with
`hx: <cmd>: not implemented (build-N)` and exit 2:

| command | `InstanceSource` method | status |
|---|---|---|
| `hx board --json` | `board()` | **works today** |
| `hx show <id> --json` | `show(id)` | not implemented (build-2 item 9) |
| `hx orders --json` | `orders()` | not implemented (build-2 item 10) |
| `hx archive --json` | `archive()` | not implemented (build-2 item 10) |
| `hx wake partner <text>` | `wake_partner(text)` | not implemented (build-2 item 8) |

`goals/build-2.md` delivers all four, and `goals/build-2.done.md` does not exist yet, so
build-2 is still in flight. This is a sequencing conflict in ui-2, not a disagreement: the
work is all in build-2, it just has not landed.

**What the ui lane did, rather than guess.** `InstanceSource` is built and tested against the
subprocess seam exactly as ui-2 specifies, with one runner function behind the `Source`
interface. Each reader raises `SourceUnavailable` carrying hx's own message when the command
is not implemented, and the server renders that as a 503 naming the build goal. The parse path
is tested with a stub `hx` on `PATH` emitting the ui-1 fixtures, so the moment build-2 lands
these become live with no ui change — and switching from subprocess to the Python functions
named in `handoff/build-to-ui.md` is a change to one function.

I did **not** compose the `hx show --json` document from files inside the UI. Spec 16 says the
UI shows "a file under `HARNESS_ROOT` or a tmux pane, read through the same code as
`hx board --json` and `hx show <id> --json`", so a second implementation of that contract in
`src/hx/ui/` would be exactly the thing that rule forbids, and it would have to be deleted at
build-2 anyway.

**Consequence for ui-2's done condition**, which asks for "the `curl` output of
`/api/show/partner`" against a scratch instance: until build-2 lands that output is the 503.
The done file records whichever is true when the goal closes, and says which. `/api/board`
against a real scratch instance works now and is pasted there in full. No ruling needed if
build-2 lands first; if you would rather ui-2 close without it, say so and I will note it.

## 2026-09-20 — ui lane — spec 03 defines no pane log, so the capture fallback has nothing to read

> Orchestrator: Ruling: the first option. `start.sh` pipes the pane to `logs/<id>/<id>-pane.log`; added to spec 03 and 11 and to build-2 item 12. Your path name is the spec's now. DONE.

ui-2 item 3 asks for "log-file fallback when the session is dead", keeping autodev's pattern
(spec 16.4). In autodev the fallback read `logs/<agent>.log`, written because autodev started
its agents under a shell that tee'd the pane. Spec 03's `logs/<id>/` holds only the Companion's
JSONL raw streams (`<id>-main.jsonl`, `<id>-sNNN-<open|closed>.jsonl`), which are hook records,
not pane text, and nothing in spec 03 or 11 writes a pane transcript.

So the fallback is implemented (`hx.ui.pane.log_fallback`) and reads
`logs/<id>/<id>-pane.log`, but nothing writes that file today, and a dead session therefore
shows "no live tmux session <id>" rather than its last output — which is the one moment the
human most wants to see what it said.

Two ways to make it real, both the build lane's: have `adapters/claude/start.sh` add
`tmux pipe-pane -o -t <id>:main 'cat >> $HARNESS_ROOT/logs/<id>/<id>-pane.log'` at launch, or
declare that a dead pane simply has no history and drop the fallback from spec 16.4. The ui
lane has no preference; it needs to know which, and if it is the first, whether that path is
the right name for spec 03.

## 2026-09-20 — gtm lane — spec 12 orders `hx bench` before the Partner's own `hx complete done`

> Orchestrator: Option 1 adopted; spec 12 steps 5 and 8 reworded. DONE.

Found while building the M8 scenario pack (`tests/scenario/m8/`), which has to say what the
Partner does in what order.

Spec 12 step 5, for a worker that came back `done`:

> `done` → dependents were already promoted by hx; `hx bench <id>` once the digest is consumed,
> so the id is free for the next order.

Spec 12 step 8:

> **Goal met:** the Partner runs `hx complete done` on its own item. Its `### Checks`
> (`hx board --require-done …`) prove the plan is done.

Followed literally, the Partner benches eng-001 and eng-002 in step 5 and then completes itself
in step 8. But `hx board --require-done` requires each listed id to be **`complete` with
outcome `done`** (spec 08, and `hx.board.require_done` implements exactly that), and `hx bench`
resets a completed item to `idle`. So the Partner's own checks fail on work that is genuinely
finished, it gets `HX-CHECK-FAILED`, and there is nothing it can do about it — benching is not
reversible.

The pack completes first and benches second, and `orders/partner.md` states the reason inline.
Three ways to settle it properly, in my order of preference:

1. **Reword spec 12** so step 5's bench is explicitly deferred until after step 8 — the
   Partner reads the digest and updates `PARTNER.md` when the item lands, and benches only once
   its own item is complete. This is the smallest change and it keeps `require_done` reading
   the work item, which is the thing `hx board` is about.
2. **Have `require_done` read `tasks.json`** rather than the work item state. `hx bench` does
   not touch `tasks.json`, so a benched item would still satisfy it. But then `--require-done`
   stops being a statement about the board and starts being a statement about history, and an
   id re-dispatched for something else would still read as satisfying an old plan until it
   completes again.
3. Leave it and rely on every Partner reading the ordering note in its order. Fragile: it is
   exactly the kind of thing that works until the one time it does not.

Not editing `spec/**` or `CONTRACTS.md`; flagged to the build lane in `handoff/gtm-to-build.md`
so nobody implements around it in the meantime.

## 2026-09-20 — gtm lane — two smaller things from the same pack

> Orchestrator: Both settled in spec 08 (`hx goal` writes the marker in both cases; `hx bench` leaves the outcome by design). DONE.

**1. `hx goal` and the `goal` marker when delivery defers.** Spec 08 says `hx goal` writes
`run/<id>/goal` with a timestamp, and separately that a mid-turn pane gets `run/<id>/goal-pending`
instead. It does not say whether the `goal` marker is written in that second case. It must be:
spec 06's invariant is that every `working` item has one, and M8 requires `hx board` to exit 0
*throughout*, including the turn in which the Partner dispatches or resumes itself. The pack
assumes it is written (assumption A1 in `tests/scenario/m8/README.md`); two expected board
files change if that is wrong. Worth one clarifying sentence in spec 08.

**2. A benched item keeps its outcome on the board.** `hx bench` does not touch `tasks.json`
(spec 08), and the board's outcome column prefers the tasks entry, so after benching an id
reads as `idle` with outcome `done` until its next dispatch. That is consistent and I have
documented it as the pack's assumption A2, but it reads oddly enough that someone will
eventually "fix" it. If it is intended, spec 08's `hx bench` row could say so.

## 2026-09-20 — gtm lane — `goals/build-2.done.md` does not exist, but M1 has landed

> Orchestrator: Right call. DONE.

`goals/gtm-3.md` step 2 branches on whether `goals/build-2.done.md` exists. It does not, so by
the letter I should reconcile against build-1's three commands only. But `hx dispatch`,
`complete`, `resume`, `bench`, `read`, `show`, `launch`, `restart`, `wake`, `orders`, `archive`
and `heartbeat` all have real argparse surfaces and real behaviour in the shared tree today.

I reconciled against what is actually there rather than against the done-file marker, since the
point of the step is that the skills match reality. What I could not do is run a full dispatch
→ complete → resume cycle: `hx dispatch` requires a live tmux session per id, which requires
`hx launch`, which runs `install.sh` and `start.sh` and needs the pinned `claude` binary and
seeded credentials. That is M6+ territory and stays for gtm-4 or for M8 itself. Recorded in the
done file as what remains.

## 2026-09-20 — build lane — the live check is blocked on macOS: there is no `~/.claude/.credentials.json`

> Orchestrator: decided. Auth is a seed token (`claude setup-token` → `seed/token` → `CLAUDE_CODE_OAUTH_TOKEN`); no Keychain, no `~/.claude` read, `--from-user-config` removed. Spec, CONTRACTS, build-3/4 updated; see `handoff/orchestrator-to-build.md`. DONE.

`goals/build-3.md` item 6 asks for a live Claude Code session against a scratch root, seeded by
"`hx install --from-user-config`-equivalent copying of your own session's credentials
**read-only**". That cannot be done on this machine, and the reason is a gap in spec 11 and
17.2, not in the goal.

**What I found.** On macOS, Claude Code keeps no credentials file at all:

```
$ ls ~/.claude/.credentials.json
ls: /Users/loganrobbins/.claude/.credentials.json: No such file or directory

$ security find-generic-password -s "Claude Code-credentials"
keychain: "/Users/loganrobbins/Library/Keychains/login.keychain-db"
class: "genp"   "svce"<blob>="Claude Code-credentials"
```

The credential is a login-Keychain item. There is nothing to copy read-only.

**And a scratch config dir is not logged in**, so the Keychain item is not simply picked up by
any config dir:

```
$ CLAUDE_CONFIG_DIR=<scratch>/home DISABLE_AUTOUPDATER=1 claude -p "Reply with exactly: LIVE-OK"
Not logged in · Please run /login

$ ls -a <scratch>/home
.claude.json  backups  projects  sessions        # no .credentials.json
```

`~/.claude` was byte-identical before and after both probes (same
`tools/claude-home-hash.sh | shasum` value), and `tests/guard` passes.

**What this means for the spec, which is the part that needs your decision.**

1. **`hx install --from-user-config` (17.2 step 3) cannot work on macOS as written.** It says
   it "copies credentials from `~/.claude`". There is no such file to copy. Either the flag is
   Linux-only, or it has to read the Keychain, which is a different and much more sensitive
   operation than a file copy and one I would not implement without you saying so explicitly.
2. **The per-home credential model (11 Auth, 17.3) may not hold on macOS either.** After the
   interactive seed login, `seed/home/.credentials.json` may still never appear, because the
   login writes to the Keychain. If so, `install.sh`'s refusal and `start.sh`'s refusal on a
   missing `.credentials.json` are both checking for a file that will not exist, and
   "Credentials are per home ... a copy from `seed/home`" needs rewriting for macOS. I could
   not test this without performing an interactive login, which is the human's to do.
3. The Linux path is presumably fine — `.credentials.json` is the documented location there —
   so this may be a "macOS needs its own paragraph" fix rather than a design change.

**What I did instead**, so build-3 is not left without its live evidence: I verified
`hx.goal._REAL_PROMPT` against the real pinned binary by capturing live tmux panes read-only,
which needed no credentials and wrote nothing. It found a real bug; see
`goals/build-3.done.md`. The rest of item 6 — that SessionStart prints the context path, that
the first tool call is one Read of it, and that the agent answers "who are you" from the
persona with zero Reads — is **not done** and needs either a human `/login` into
`seed/home`, or your decision on (1).


## 2026-09-20 — build lane — the M2 "zero Reads" criterion is contradicted by the live run

> Orchestrator: agreed on both. M2 reworded (spec 13); the 09.1 hook line now names the Read tool and forbids `cat`; gtm changes `config/CLAUDE.md` in gtm-5. DONE.

Spec 13 M2 asks for two things that pull against each other in the same turn:

- "the agent's first tool call after a boundary is one Read of that path"
- "asked who it is in its first turn, the agent answers from the persona with zero Reads"

In the build-3 live run against 2.1.278 the Partner did the boundary read first and *then*
answered, so the first criterion is what actually governs a first turn. It answered from the
persona — the callsign was only ever in `config/partner/AGENTS.md` above the header, and the
context file provably does not carry the persona — but not with zero Reads.

What the "zero Reads" criterion is really testing, I think, is that the persona costs no tool
call: it is in the system prompt via `--append-system-prompt-file`, so the agent does not have
to go and find out who it is. That is true and is now demonstrated live. Suggest rewording M2
to something like "the persona is answered from the system prompt, with no read of any
identity file", which is testable and does not fight the boundary-read rule. Your call; I have
recorded the live evidence in `goals/build-3.done.md` either way.

Separately, the *form* of the boundary read is a real problem and I have raised it with the
gtm lane (`handoff/build-to-gtm.md`): the agent used `Bash cat` and then `Read`, reading the
file twice. M7's metric counts Reads, so it would under-count. The fix is wording in
`config/CLAUDE.md`, which is gtm's; if you would rather spec 09.1's hook line name the tool
instead, that line is yours to change and I will follow it.


## 2026-09-20 — ui lane — `tools/milestone-check.sh` cannot exit 0 for anyone while gtm's script is mid-rewrite

> Orchestrator: both decided. Done = guard + own paths; `tools/milestone-check.sh <lane>` now runs the rest as advisory and never fails on it. ORCHESTRATION.md updated. Your close of ui-5 was right. DONE.

Raising this to you because it is not fixable inside any one lane and it currently blocks the
*done condition of every lane*, which is phrased as "`tools/milestone-check.sh` passes".

**What is true right now.** `tests/packaging/test_e2e_deploy.py` is committed;
`packaging/e2e-deploy.sh` is **uncommitted and modified** in the shared working tree:

```
$ git status --short -- packaging/
 M packaging/e2e-deploy.sh                  # mtime 15:41
$ grep -c from-user-config packaging/e2e-deploy.sh
0
$ stat -f %Sm tests/packaging/test_e2e_deploy.py
14:17
```

`test_the_script_never_reads_the_real_claude_home_for_seeding` asserts `"--from-user-config" in
text`, where `text` is the contents of that script. The committed test and the half-rewritten
script disagree. `test_end_to_end_deploy` exits `-15` (SIGTERM), i.e. the script is being
killed rather than failing on its own.

Nothing is wrong with either lane's *intent* — gtm is mid-goal. The problem is structural: we
share one working tree, and `milestone-check.sh` runs the whole suite, so **one lane's
uncommitted half-edit makes every other lane's completion gate red**, no matter how green that
lane's own paths are.

**I have changed nothing outside `src/hx/ui/**` and `tests/ui/**`.** ORCHESTRATION.md is
explicit that another lane's failing test is reported, not fixed, and "does not block you if
your own tests and the guard tests pass". Mine do: `tests/guard` 5 passed, `tests/ui` 338
passed 1 skipped, and goal ui-5's own wording is "`milestone-check.sh` has passed for
`tests/guard` and `tests/ui`", which holds. So I have closed ui-5 on that reading and said so
in `goals/ui-5.done.md`.

**What I think needs deciding, since it will recur every time two lanes are in flight:**

1. Whether the done condition means the whole suite or the lane's own paths plus `tests/guard`.
   ORCHESTRATION.md says the latter; the goal text says the former; they are read as
   contradictory by anything checking mechanically.
2. Whether `milestone-check.sh` should report other lanes' failures separately from the
   caller's own — e.g. run `tests/guard`, then the lane's paths, then the rest as advisory —
   so a lane can tell "I broke something" from "someone else is mid-commit".

I have not touched `tools/**`; that is yours.

**One correction on my own record**, since it is in `handoff/ui-to-gtm.md` and gtm may read it:
my first diagnosis of this failure said `packaging/e2e-deploy.sh` was calling an `hx install`
flag the build lane had not shipped. That was wrong — the flag is absent from the *script*,
which the *test* requires. I have corrected that entry in place rather than leaving a plausible
wrong cause for someone to chase.

## 2026-09-20 — gtm lane — the agent branch has three names

> Orchestrator: Decided: `agent/<id>` wins (option 1). The build lane changes `repo.py`'s fallback in build-4; gtm-6 text fixed; spec unchanged. DONE.

Found while adding the `hx push` assertion to `packaging/e2e-deploy.sh` (gtm-6 step 1, which
says "the `hx/<id>` branch").

| Source | Says |
|---|---|
| `src/hx/repo.py` `branch_for` fallback | `hx/<id>` |
| `src/hx/skeleton/templates/worker/harness.json` (gtm) | `agent/<id>` |
| spec 17.2 step 4, 17.3, and the `Working dir` row of 17.3's table | `agent/<id>` |
| `goals/gtm-6.md` step 1 | `hx/<id>` |

`branch_for` prefers `config/<id>/harness.json`'s `branch` field and falls back to `hx/<id>`.
Every worker the Partner creates from `templates/worker/` therefore gets `agent/<id>`, and the
fallback is reached only by a config that omits the field — so today the *spec* name wins in
practice and the *code* default is the one nobody sees.

Nothing is broken and I have changed nothing. The deploy proof reads the branch from the config
rather than hard-coding either name, and asserts what actually matters (exactly one ref
upstream, matching the mirror), so it stays correct however this is settled.

It is worth settling, though: `hx push` is the one command that reaches a user's remote, and
three sources disagreeing about which branch it sends is how work ends up on a ref nobody is
watching. Two ways to close it, both cheap:

1. **`agent/<id>` wins.** Change `repo.py`'s fallback to match the spec and the template. One
   line, and the spec needs no edit.
2. **`hx/<id>` wins.** Change spec 17.2/17.3, my `templates/worker/harness.json`, and the
   `agent/<id>` mentions in `docs/two-worlds.md` and `docs/deploy.md`.

My preference is weak and it is for (1), only because the spec and the shipped template already
say it, and `hx/` reads like an hx-internal ref rather than the agent's working branch. Either
way I will make the gtm-side changes as soon as you decide; raised with the build lane too.

## 2026-09-20 — gtm lane — CI was running only the guard tests after the lane change

> Orchestrator: Right fix on your side. Also closed on mine: `tools/milestone-check.sh` with no lane now runs the whole suite as required, so the trap is gone for any caller. DONE.

`tools/milestone-check.sh` taking a lane name is right for a lane finishing a goal, but
`.github/workflows/ci.yml` (mine) invoked it with no argument, and with no lane `own=""`, so the
full-suite step ran `tests/guard` and exited 0. CI would have been green on a broken
`tests/core` or `tests/ui`.

Fixed on my side: the `test` job now runs `.venv/bin/python -m pytest -q` over everything, with
a comment saying why it does not use the lane form — CI is nobody's lane and every test is
required there. `tests/packaging/test_ci_workflow.py` asserts both halves, so the lane script
cannot quietly come back.

No action needed; recorded because the same trap is available to anything else that calls the
script without an argument.
