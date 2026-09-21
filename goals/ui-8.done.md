# ui-8 done — the autodev UI is the hx UI, and hx is the graph

autodev's `index.html`, `app.js` and `style.css` are back in `src/hx/ui/static/`, pointed at hx.
The stylesheet is autodev's file: **328 of its 353 selectors kept**, and the only 25 dropped are
the demo banner and controls, the settings editor and the contract grid — the three things this
goal named. The shell is unchanged; the model underneath it changed. Pillars are pods, agents
are HarnessAgents each with its Companion, ledger tasks are the work item's `## Tasks` and the
step state's open and closed steps, `currentTask`/`currentText` is the open step's next action,
`phase` is state and outcome, activity is the stream tails with their seam markers. **The home
page is the whole instance as
one graph**: the Partner at the root, every HarnessAgent below it as a demo-style card in a
labelled pod cluster, its Companion beside it, a live edge to every id `tasks.json` records a
dispatch for and a dashed one to every id it does not, zoom and fit, SSE for state. No pod pages
— the sidebar's pod and agent entries zoom that cluster or node and open the agent's drawer,
which is spec 16.2's Agent page in full (work item, step state, context file, streams with seam
markers, subagents, metrics, pane). Tabs: Graph, Task board, Harness Agents, Partner chat; no
Contract. Deleted: `demo.js` and every demo control, the settings page with
`loadConfig`/`saveConfig`, pillar editing, the mailbox chat mechanics, the 2 s poll, and
autodev's Launch/Send goal/Stop buttons — spec 16.3, the page does not operate agents.

Two decisions recorded. **Orders and Archive stay as pages** in the shell, beside Activity under
"Across pods": spec 16.2 lists both and a drawer would bury them. **Two rendering idioms, on
purpose**: the shell is autodev's HTML strings with every interpolation through `esc()`, while
anything a HarnessAgent wrote — work item, order, digest, pane, tool result — is still built as
DOM nodes with `textContent` and mounted through a `slot()`, so no markup an agent emits reaches
`innerHTML`, and every ui-2..ui-7 renderer survives intact. `server.py` and `data.py` are
untouched but for one stale docstring (`Source.orders` still promised an `after` graph).

## Tests

`tools/milestone-check.sh ui --all` passes; the advisory run over every other lane is green too,
including `tests/packaging`'s assertion that the packaged UI references no external URL.

    == required: tests/guard
    .....                                                                    [100%]
    == required:  tests/ui
    .........................                                                [100%]
    (385 counted from the run: 384 passed, 1 skipped — the deliberate SSE skip)
    == advisory: the rest of the suite (other lanes; never fatal here)
    advisory: green
    MILESTONE-CHECK PASSED for ui

385 tests, up from 364. The DOM harness was rebuilt, not replaced: `domshim.js` parses HTML now
(the restored `app.js` builds the shell with `innerHTML`) and has a selector engine, so `render.js`
drives the **real `static/index.html`** and walks every screen as a human does, by hash and
click. Every earlier assertion is re-pointed, none deleted: the board table's contract fields
are the agent table's columns and the drawer's stats now, and `views.agents[<id>]` is still the
agent, still carrying the work item, step state, evidence `seq`s, budget bar, digests, the
metrics table with its dirty-seam marking, and the pane. One behaviour changed on purpose: an
unreadable agent no longer blanks the page or raises a global banner — it is reported in its own
drawer, saying whether the board still lists the id.

## Verified

**Against a real instance**: `test_every_view_renders_against_a_real_instance` and
`tests/ui/test_m8_instance.py` render every screen from `hx.board.collect`, `hx.show.collect`,
`hx.orders.collect` and `hx.archive.collect` on roots built by `hx install --skeleton-only` and
by the gtm lane's M8 packs, at all eleven observation points — a fresh instance is nulls and
empties and the graph, the cards, the table and the drawer survive it. `python -m hx.ui
--fixtures tests/ui/fixtures` was served and checked by hand: `/` sets the HttpOnly cookie, and
`/static/{app.js,style.css}`, `/api/{board,orders,archive}` and `/api/show/<id>` all answer 200.

**Against fixtures**: the fleet had one `show` document, so four of five agents had nothing to
say on the new screens. `regen_fixtures.py` now writes `show-eng-000`, `-eng-002`, `-eng-003`
and `-res-001` too, each to `CONTRACTS.md` and each step state through the real
`hx.stepstate.validate` and `evict`. res-001 deliberately has none, so the one-line rule is
exercised on its fallback: the first unchecked `## Tasks` line.

**Not verified live against Claude Code.** This lane launches no session; the pane is
`tmux capture-pane` through `hx.ui.pane`, which the M8 packs exercise.

## Open questions

1. ~~`turn` missing from `hx show --json`.~~ Closed: build-8 landed it while I was committing.
   Applied in a follow-up commit — the page already preferred `show.turn`, so what changed is
   the evidence: the marker is now asserted off a real instance, not off the contract shape.
2. `state_budget_tokens` is still not in `hx show --json`, so the budget bar reads against the
   `templates/worker` default and is labelled as such. Open since ui-7; needs a CONTRACTS.md
   line, so it is your call as much as the build lane's.
3. The home page reads `hx show` once per board id to get each agent's open step — five reads on
   the fixtures, sixty on a sixty-agent fleet, each with a pane capture. A `next` field on the
   board item would make it one read. Flagged, not asked for.
4. Spec 16.2 puts the board on the Partner view; ui-8 made it a tab. I kept a fleet table on the
   Partner page as well, so it still shows what the human and the Partner are talking about.
   Say if you would rather it went.

## Handoff entries

- `handoff/build-to-ui.md` — build-7's v1 cut marked `DONE`, with the two things it asked for
  that landed here: an unknown `state` gets its own board column, and the after-graph docstring
  and `--queued` token are gone.
- `handoff/ui-to-build.md` — two new: `turn` missing from `hx show --json` (closed by build-8
  the same day); the cost of one `hx show` per agent, with the `next`-on-the-board fix.
- `handoff/build-to-ui.md` — build-8 read and marked `DONE`: `turn` applied and asserted
  against real output, and `hx ui` as a session hx starts noted (nothing in `server.py` moved).
- `handoff/to-orchestrator.md` — one question: build-8's `hx.goal.input_box` would let the
  Partner page tell "the Partner is typing" from "the Partner has said it". Not done; it
  changes what that page means. A small ui-9 if you want it.
