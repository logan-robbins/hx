# ui-3 done — metrics and seams rendered; the harness went live mid-goal

## Short version

Items 1–3 built and tested. **Item 4(a) happened without a code change**: build-2's commands
went live in the shared tree while this goal ran, and because ui-2 put every reader behind one
subprocess seam, the 503s turned into real documents on their own. Item 4(b) — binding the
Python functions directly — is **ui-4**: `handoff/build-to-ui.md` still names none, which is
the gate ui-3 set. `goals/build-2.done.md` does not exist yet either.

The switch is what found the one real bug in this goal: the UI was about to tell a human
"delivered" for a Partner message that reached nobody.

## What was built

### 1. Metrics as a table (`hx metrics --json`, CONTRACTS.md)

The Agent view rendered `metrics` as pretty-printed JSON in ui-2, because the shape had no
contract. It has one now, so it is a table: one row per seam with source, prompt version,
context tokens before, context file size, working-set size, and the next-10-turns window split
into tool calls, context-file reads, working-set reads and other — then a totals row.

A seam is marked red when `reads_of_context_file != 1` or `reads_of_working_set > 0`, which is
spec 07.4's pair of rules: exactly one handover read, and every re-read of a file already in
the working set is waste. **The offending number itself is marked**, not just the row, and the
row's `title` says why in words (`re-read 3 working-set files`, `never read its context file`,
`read the context file 2 times; re-read 1 working-set file`) so colour is not the only signal.
The totals row marks fleet-level waste the same way. Above the table, one line: `3 of 5 seams
did not hand over cleanly…`, or `every seam handed over cleanly.` when none are.

A window shorter than ten turns renders its real count, marked, rather than implying ten.

Fixture `tests/ui/fixtures/metrics-eng-001.json` covers every case the table marks: a clean
seam, a seam that never read its context file, one that read it twice, one that re-read
working-set files, a short window, and all five `source` forms.

`CONTRACTS.md` says "the `metrics` object of `hx show --json` is exactly this document", so
`show-eng-001.json` now embeds that document byte for byte and a test asserts the two never
drift. `show-partner.json` got a valid document of its own.

### 2. Seams in the stream tail (spec 16.2)

A `seam` record in a stream tail already showed its context file size. It now also shows the
tool calls of the ten turns that followed — `next 3 turns: 5 tool calls · 1 ctx-file ·
0 working-set · 4 other` — looked up in the metrics document by the record's `seq`, marked and
explained on hover when that seam was dirty. Records of every other type are untouched, which
is asserted rather than assumed.

Where the tail has a seam the metrics document does not cover yet, it says `next 10 turns: not
yet` rather than rendering zeroes as if they were measured.

### 3. Static file names settled

`handoff/ui-to-gtm.md`: `hx/ui/static/{index.html,app.js,style.css}`, why the list is
exhaustive by construction rather than convention, and why `style.css` is the one that fails
*silently* if a wheel drops it. They are fixed now; ui-4 is server-side only and adds none.

### 4(a). The switch to the real harness — already done by construction

Every command `InstanceSource` needs is live:

```
board --json             rc=0/1     show partner --json      rc=0
show eng-001 --json      rc=0       orders --json            rc=0
archive --json           rc=0       wake partner <text>      rc=0
```

`src/hx/ui/data.py` needed no change for this. That was the point of the ui-2 design, and it
is the strongest evidence for it: the 503s disappeared because the commands appeared.

## The bug this found: "delivered" for a message nobody got

`hx wake partner` exits **0 whether or not the Partner was there** and reports on stdout:
`HX-WAKE partner accepted`, or `HX-WAKE partner no-socket`. `CONTRACTS.md` specifies the `bool`
that `hx.wake.wake_partner(root, text)` returns but says nothing about how the CLI conveys it,
and `InstanceSource.wake_partner` was reading the exit code. Against the real binary it
returned `True` for a scratch instance with no socket file at all — the UI's chat box would
have told the human their message went through when it reached nobody. That is the one lie
this box must not tell.

Fixed: `wake_partner` matches the exact line `HX-WAKE partner accepted`. Tested both ways
against the real `hx`, including **end to end through a real unix socket** that asserts the
auth line went first and the text arrived verbatim. Asked the build lane to treat that line as
a contract now that the UI's only write path depends on its wording.

**It is one now.** While this goal was closing the orchestrator put the three `HX-WAKE` lines
into `CONTRACTS.md` — `accepted`, `no-socket`, `refused` — with **exit 0 only for `accepted`**,
exit 3 for the other two, and exit 2 left to usage errors. So `wake_partner` now checks both
signals rather than the line alone, and the new exit codes bought something the old
always-zero behaviour could not: a usage error (exit 2) means *the UI called hx wrong*, which
is raised as a 502 instead of being reported to the human as an undelivered message. Being
wrong about "your message did not arrive" is bad; silently swallowing "the UI is broken" is
worse. The real `hx` already exits 3, asserted directly.

Fixing it surfaced a second, quieter problem. `run_hx` treated "exit 1 with output on stdout"
as data, because `hx board --json` exits 1 whenever `errors` is non-empty and the board must
still render. That rule silently applied to `hx wake` too, so a *failing* wake that printed an
accepted line would have counted as success. The rule is now explicit — `run_hx(..., document=True)`
marks the commands whose exit code reports the **instance** rather than the call — and every
other command is judged on its exit code.

## How it was verified

```
$ ./tools/milestone-check.sh
milestone-check: PASS (exit 0)

$ .venv/bin/python -m pytest tests/guard
5 passed in 1.40s

$ .venv/bin/python -m pytest tests/ui
249 passed, 1 skipped in 9.00s

$ .venv/bin/python -m pytest
676 passed, 1 skipped in 82.97s
```

`tests/ui` is 249 passed / 1 skipped, up from 213 at the close of ui-2. New coverage:

- **Metrics table** — a column per contract field; one row per seam in order with every cell
  checked against the fixture; the three dirty seams marked and the two clean ones not; the
  exact offending cells marked; the hover text for each; the short window; the totals row and
  its fleet-level mark; the "3 of 5" line, the clean-run line, and the no-seams "not yet" case.
- **Seam markers** — the follow-up counts come from the metrics document by `seq`; a clean
  marker is unmarked; a dirty one is marked and explained; a seam with no metrics entry says
  "not yet"; non-seam records keep their rendering.
- **The metrics fixture as a contract** — shape, every seam's keys, `source` vocabulary, seams
  in stream order, each seam's `tool_calls` equal to its three parts, totals equal to the sum,
  and `show-eng-001.json`'s `metrics` identical to the document.
- **The real harness** — `board`, `show`, `orders`, `archive` and `wake` against a real
  `HARNESS_ROOT`; the pane overlaid live; `orders` reporting both `file_matches_record` states
  from real files; the server serving every view of that instance.
- **The wake contract** — the real `hx` exiting 3 with `HX-WAKE partner no-socket`; both
  undelivered lines returning False; a `no-socket` line with a zero exit still False (the line
  wins); a usage error raised rather than reported as undelivered; and delivery true end to end
  through a real unix socket.
- **The views against real data** — the renderer run against documents pulled from the live
  instance, not fixtures. This is the one that matters most: a fresh instance returns
  `metrics: null`, no streams, no step state and an empty context file, which is exactly the
  shape hand-written fixtures do not have. Every view renders with no error banner.

The `AF_UNIX` socket for the wake test lives in the OS temp dir, not `tmp_path`: the path is
capped near 104 bytes and pytest's per-test directory is already longer.

The session-scoped instance manifest check still holds — contents, mode and mtime of every file
under the scratch `HARNESS_ROOT`, unchanged across the whole run but for `run/ui-token`. The
wake test writes `run/partner/socket.json` and removes it in a `finally`.

### Served against a scratch instance

`tests/scenario/m8/` does not exist, so the instance is `hx install --skeleton-only` plus
hand-placed work items, `orders/*.md` and `tasks.json`, per the goal. `orders/partner.md`
matches what was dispatched and `orders/eng-001.md` was edited afterwards, so both
`file_matches_record` states are exercised against real files.

`/api/show/partner`, real, in full (`partner_md` elided — it is 2.4 kB and renders in the view):

```json
{
  "archive": [],
  "bench": [],
  "context_file": {
    "path": "run/partner/partner-main.context.md",
    "seam_ts": null,
    "text": null
  },
  "file": "pods/partner/partner-working.md",
  "id": "partner",
  "metrics": null,
  "pane": {
    "alive": false,
    "error": "can't find session: partner",
    "lines": [],
    "session": "partner",
    "source": "none"
  },
  "partner_md": "# PARTNER.md\n\n\u2026PARTNER.md continues, 2.4 kB, rendered in full in the view\u2026",
  "persona_path": null,
  "pod": "partner",
  "role": "partner",
  "state": "working",
  "step_state": {},
  "streams": [],
  "subagents": {},
  "task": {
    "addenda": [],
    "after": [],
    "completed": null,
    "dispatched": "2026-09-20T12:00:00Z",
    "order": "## Order\nStand in for a dispatched order while the ui lane runs against a real instance.\n\n## Definition of done\n- The board renders this item.\n\n### Checks\n```bash\ntrue\n```\n",
    "outcome": null
  },
  "work_item": {
    "body": "## Order\nStand in for a dispatched order while the ui lane runs against a real instance.\n\n## Definition of done\n- The board renders this item.\n\n### Checks\n```bash\ntrue\n```\n\n## Tasks\n- [x] Exist on the board.\n- [ ] Be opened in the Agent view.\n\n## Deliverables\n\n## Commands\n\n## Open decision\n\n## Digest\n",
    "frontmatter": {
      "after": [],
      "dispatched": "2026-09-20T12:00:00Z",
      "id": "partner",
      "outcome": null,
      "pod": "partner"
    }
  }
}
```

`/api/orders`, real, in full:

```json
{
  "errors": [],
  "graph": {
    "edges": [],
    "nodes": [
      {
        "id": "partner",
        "outcome": null,
        "ready": true,
        "state": "working"
      },
      {
        "id": "eng-001",
        "outcome": null,
        "ready": true,
        "state": "working"
      }
    ]
  },
  "orders": [
    {
      "addenda": [],
      "after": [],
      "file_matches_record": true,
      "id": "partner",
      "order": "## Order\nStand in for a dispatched order while the ui lane runs against a real instance.\n\n## Definition of done\n- The board renders this item.\n\n### Checks\n```bash\ntrue\n```\n",
      "path": "orders/partner.md",
      "pod": "partner",
      "ready": true,
      "record": {
        "addenda": [],
        "after": [],
        "completed": null,
        "dispatched": "2026-09-20T12:00:00Z",
        "order": "## Order\nStand in for a dispatched order while the ui lane runs against a real instance.\n\n## Definition of done\n- The board renders this item.\n\n### Checks\n```bash\ntrue\n```\n",
        "outcome": null
      },
      "state": "working",
      "waiting_on": []
    },
    {
      "addenda": [],
      "after": [],
      "file_matches_record": false,
      "id": "eng-001",
      "order": "## Order\nStand in for a dispatched order while the ui lane runs against a real instance.\n\n## Definition of done\n- The board renders this item.\n\n### Checks\n```bash\ntrue\n```\n\n## Order addendum, typed into the file after dispatch\nRescope to the board only.\n",
      "path": "orders/eng-001.md",
      "pod": "engineers",
      "ready": true,
      "record": {
        "addenda": [],
        "after": [],
        "completed": null,
        "dispatched": "2026-09-20T12:00:00Z",
        "order": "## Order\nStand in for a dispatched order while the ui lane runs against a real instance.\n\n## Definition of done\n- The board renders this item.\n\n### Checks\n```bash\ntrue\n```\n",
        "outcome": null
      },
      "state": "working",
      "waiting_on": []
    }
  ],
  "root_abs": "/private/tmp/claude-501/-Users-loganrobbins-workspace-hx/cd595057-fbf9-4c6e-ac74-30de1b461e27/scratchpad/inst",
  "ts": "2026-09-20T21:02:50Z"
}
```

## Live against Claude Code vs. against the fake

Nothing in this goal ran against Claude Code or the fake `claude`; the UI launches no agent and
reads no transcript. What it ran against rather than a mock: the **real `hx` binary** as a
subprocess for all five readers, a **real `HARNESS_ROOT`** built by the build lane's own
`hx install --skeleton-only`, a **real unix socket** for the wake path, a **real
`http.server`** over TCP, and the **real `static/app.js`** under node 26.5.0. Pane capture
against real tmux is unchanged from ui-2 and still passing.

## Open questions

None outstanding. All four were raised and answered inside this goal
(`handoff/orchestrator-to-ui.md`, marked `DONE 2026-09-20`):

1. **Item 4(b) is ui-4** — confirmed, sent once `handoff/build-to-ui.md` publishes the Python
   functions, which the orchestrator has asked the build lane to do in build-2's done step.
   What ui-4 needs from them is already written in `handoff/ui-to-build.md`: module path,
   signature, return value, and what each raises for an unknown id, so the UI can tell 404 from
   502.
2. **The tree is the fact, not the done file** — confirmed, and now written into
   ORCHESTRATION.md as "What counts as landed". Reading the live commands as the gate was right.
3. **`hx wake`'s status line is a contract now** (`CONTRACTS.md`): `accepted`, `no-socket`,
   `refused`, with **exit 0 only for `accepted`**, exit 3 for the other two, and exit 2 left to
   usage errors. This arrived while ui-3 was closing and is **applied in code, not merely
   noted**: `wake_partner` checks both the exact line and the exit code, and — because exit
   codes now carry meaning — a usage error is raised as a 502 rather than reported to the human
   as an undelivered message, which is a different and much worse thing to be wrong about.
   Verified against the real `hx`, which already exits 3 with `HX-WAKE partner no-socket`.
4. **No fleet-wide metrics view** — confirmed; `GET /api/metrics/<id>` not added. The Agent
   view reads `metrics` out of `hx show --json`, which CONTRACTS says is the same document.

## Handoff entries written

- `handoff/ui-to-gtm.md` — **new file**: the three static files to pin in the wheel check, why
  the list is exhaustive, that the names are now stable, and an optional grep for `http(s)://`
  in the installed assets since "no CDN" matters most in the wheel.
- `handoff/build-to-ui.md` — read, applied, marked `DONE 2026-09-20`: their red-suite report was
  work in flight and is green; `hx board --json` exiting 1 was already handled and is now
  explicit; **keep `seams: null`** (it means "no main stream yet", which is a different fact
  from `0`, and the board renders them differently) — and back to them, the `HX-WAKE` line and
  the bug it caused.
- `handoff/gtm-to-ui.md` — their standing offer taken up and marked `DONE 2026-09-20`. They
  then pinned the three files in `packaging/e2e-install.sh` and asked whether to keep an
  explicit list or assert "everything present ships"; answered in `handoff/ui-to-gtm.md` —
  **keep the list**, because only a list notices a file that should ship and does not, and I
  will file there before a static file changes rather than after.
- `handoff/orchestrator-to-ui.md` — your four answers read, applied and marked
  `DONE 2026-09-20`, with a note recording what was done for each.
- Nothing new to `handoff/to-orchestrator.md`: the open items were questions for you in this
  file, and all four are now answered.
