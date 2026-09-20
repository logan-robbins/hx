# ui-5: fixes from the orchestrator's live browser pass, and the M8 states as fixtures

The orchestrator ran `python -m hx.ui --root` on an M8 step-4 instance and walked every view in
a real browser at desktop and 375 px widths, including a live SSE update (a `## Tasks` line
appended to a work item appeared in the Agent view within 2 s without reload: correct) and the
pane-log fallback (a placed `logs/eng-002/eng-002-pane.log` rendered as "dead · 4 lines · from
the log": correct). Read `goals/ui-4.done.md`, `handoff/orchestrator-to-ui.md`, CONTRACTS.md.

## Fix

1. **Orders badge for a missing file.** With `orders/<id>.md` absent but a `tasks.json` record
   present, the view showed "file edited since dispatch". `order` is `null` in that case; render
   "order file missing" instead, and keep "file edited since dispatch" only when both texts
   exist and differ. Fixture and test for the three cases (match, edited, missing).
2. **Markdown tables.** `PARTNER.md`'s fleet table renders as raw pipes. Render GFM tables in
   the markdown renderer (PARTNER.md, work items, digests); test with the skeleton `PARTNER.md`.
3. **Pane source wording.** With no session and no log the caption read "from the none"; make it
   "no session, no log" and keep "from the session" / "from the log".
4. **Narrow width.** At 375 px the board table scrolls horizontally in its own container, which
   is right; make the id column sticky so the id stays visible while scrolling, and let the nav
   wrap without pushing `LIVE` onto its own line.
5. **Agent id switcher.** Once an agent is selected, the Agent view shows no way to pick another
   without going back to the Board; add the id list to the Agent view header.
6. **M8 states as fixtures.** Parametrise `tests/ui/test_m8_instance.py` over every step in
   `tests/scenario/test_m8_pack.py::STEPS` (and m8b), asserting the board and each agent view
   render without error and the `after` graph shows what the expected board says.
7. Do not send through the Partner chat against any live tmux session on this machine: the
   build lane runs real Claude sessions named `partner` for its live checks. Test wake only
   against your own fake socket.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard` and `tests/ui`.
- Committed path-scoped. `goals/ui-5.done.md` written, with handoffs.
