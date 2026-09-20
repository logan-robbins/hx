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
