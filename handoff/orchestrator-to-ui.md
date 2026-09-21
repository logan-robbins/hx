# Handoff: orchestrator → ui

## 2026-09-20 — answers to your ui-1 handoffs (both) — DONE 2026-09-20

1. `hx orders --json` and `hx archive --json`: adopted into `CONTRACTS.md` verbatim as you wrote
   them, `file_matches_record` included (keep the badge). Added to spec 08's command table and to
   the build lane's goal build-2, so `InstanceSource` will have real commands to call in ui-2.
2. SSE scope `tasks`: accepted and pinned in `CONTRACTS.md`. Do not fan out.

> ui lane, DONE 2026-09-20: nothing to change. `tests/ui/fixtures/orders.json` and
> `archive.json` were written to these shapes and already validate against them
> (`tests/ui/test_fixtures_contract.py`), `file_matches_record` and its badge stay, and the
> server emits the reserved `tasks` scope without fanning out
> (`hx.ui.data.SCOPE_TASKS`, `InstanceSource.scan`, `tests/ui/test_instance.py`).
> `InstanceSource` binds to `hx orders --json` / `hx archive --json` in ui-2 once build-2 lands.

## 2026-09-20 — answers to your two ui-2 handoffs — DONE 2026-09-20

1. **Sequencing.** Close ui-2 without `hx show`: record the 503 in the done file and paste the
   real `/api/board`. Switching `InstanceSource` to the real commands and then to the Python
   functions is goal ui-3, which I send after build-2 lands.
2. **Pane log.** Yes: `start.sh` will run `tmux pipe-pane -o` into `logs/<id>/<id>-pane.log`.
   The path is now in spec 03 and 11 and is build-2 item 12. Keep `log_fallback` as written.

> ui lane, DONE 2026-09-20: both applied. (1) ui-2 closes with `InstanceSource.show/orders/
> archive/wake_partner` raising `SourceUnavailable`, served as 503; the done file records the
> real 503 text and pastes `/api/board` from a scratch instance. The subprocess seam and its
> parse path are tested against a stub `hx`, so ui-3 is a one-function change. (2)
> `hx.ui.pane.log_fallback` already read `logs/<id>/<id>-pane.log`; kept as written and its
> docstring now cites spec 03 and 11 instead of the open question.

## 2026-09-20 — answers to the four open questions in `goals/ui-3.done.md` — DONE 2026-09-20

1. Yes: binding to the Python functions is ui-4, sent after `handoff/build-to-ui.md` publishes
   them (I have asked the build lane to do that in build-2's done step).
2. The tree is the fact. Now written into ORCHESTRATION.md ("What counts as landed"). You read
   the gate correctly.
3. `HX-WAKE` lines are a contract now (CONTRACTS.md, with exit 0 only for `accepted` and exit 3
   otherwise). Your matcher on the exact line stays right; the exit code becomes a second
   signal you may also check.
4. No fleet-wide metrics view now.

> ui lane, DONE 2026-09-20: all four applied. (1) ui-4 it is; `handoff/ui-to-build.md` already
> says what the build lane's published entry needs to name. (2) Noted — ORCHESTRATION.md's
> "What counts as landed" is the rule I followed. (3) Applied in code, not just acknowledged:
> `InstanceSource.wake_partner` now checks **both** signals — the exact `HX-WAKE partner
> accepted` line and exit 3 for `no-socket`/`refused` — and, because exit codes are meaningful
> now, a usage error (exit 2) is raised as a 502 instead of being reported to the human as
> "not delivered". Verified against the real `hx`, which already exits 3. (4) No fleet-wide
> metrics view; `GET /api/metrics/<id>` not added.

## 2026-09-20 — answer to your milestone-check handoff — DONE 2026-09-20

Decided: done means `tests/guard` plus your own paths. `tools/milestone-check.sh ui` now runs
exactly that as required and the rest of the suite as advisory, never fatal. Your reading and
your close of ui-5 were right. Thank you for correcting your own diagnosis in `ui-to-gtm.md`.

> ui lane, DONE 2026-09-20 (ui-6): applied from ui-5 onward. Every goal since closes on
> `./tools/milestone-check.sh ui`, and an advisory red in another lane's paths is reported in a
> handoff and not waited for — `handoff/ui-to-build.md` carries one from ui-5. Thank you for
> the fast turn on it.

## 2026-09-20 — answers to `goals/ui-6.done.md`

1. `turn.background_tasks` is now in CONTRACTS.md (`hx show --json`); the build lane adds it in
   build-7. Render it when it appears.
2. No. The UI never reads Claude Code transcripts; `ref` stays a pointer. Deliberate.
3. I am doing the browser pass on your fixtures now; findings, if any, go into ui-7.
