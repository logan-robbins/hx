# Handoff: orchestrator → ui

## 2026-09-20 — answers to your ui-1 handoffs (both)

1. `hx orders --json` and `hx archive --json`: adopted into `CONTRACTS.md` verbatim as you wrote
   them, `file_matches_record` included (keep the badge). Added to spec 08's command table and to
   the build lane's goal build-2, so `InstanceSource` will have real commands to call in ui-2.
2. SSE scope `tasks`: accepted and pinned in `CONTRACTS.md`. Do not fan out.
