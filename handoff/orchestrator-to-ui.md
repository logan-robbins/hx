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
