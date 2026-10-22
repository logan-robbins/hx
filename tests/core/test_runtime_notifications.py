"""Milestones reach the right party through a durable bounded notification path."""

import pytest

from hx.continuity_store import ContinuityStore
from hx.runtime_notifications import drain
from .test_appmap import git, mapped  # noqa: F401 — fixture chain
from .test_map_updates import active  # noqa: F401 — fixture chain
from .test_unit_execution import fleet  # noqa: F401 — fixture


def test_compact_notification_and_projection_acknowledgement(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.enqueue('contract_published', 'contract:T:1', {'task_id': 'T', 'output_id': 'api', 'implementation_private_details': 'omit'})
            tx.enqueue('projection', 'projection:T', {'task_id': 'T'})
        messages = []
        results = drain(store, wake=lambda root, message: messages.append(message) or True)
        assert len(messages) == 1 and 'contract_published task=T' in messages[0]
        assert 'omit' not in messages[0] and results[0]['accepted']
        assert drain(store, wake=lambda *a: False) == []
        assert store.db.execute("SELECT count(*) FROM outbox WHERE status='delivered'").fetchone()[0] == 2


def test_unavailable_partner_keeps_notification_durable_and_limit_bounded(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            for index in range(3):
                tx.enqueue('packet_covered', f'covered:{index}', {'task_id': f'T{index}'})
        assert len(drain(store, wake=lambda *a: False, limit=1)) == 1
        assert store.db.execute("SELECT count(*) FROM outbox WHERE status='pending'").fetchone()[0] == 3
        store.db.execute('UPDATE outbox SET available_at=0')
        assert len(drain(store, wake=lambda *a: True)) == 3


def test_failure_repair_and_revert_wake_scheduler_with_only_identity(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.enqueue('projection', 'repair:R', {'delivery': 'repair_packet_created', 'identity': 'R', 'diagnostic': 'large details omitted'})
        messages = []
        drain(store, wake=lambda root, message: messages.append(message) or True)
        assert len(messages) == 1 and 'repair_packet_created' in messages[0] and 'identity=R' in messages[0]
        assert 'diagnostic' not in messages[0]


def recorder(accepted=True):
    calls = []
    return calls, lambda *args: calls.append(args) or accepted


def test_a_progress_blocker_or_blocked_step_wakes_the_partner_once_with_a_pointer(tmp_path):
    from hx.progress import update
    from .test_progress import request
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Keep the current execution state."}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
        update(store, request(run))
        update(store, request(run, 1, blocker="Which schema version should the report read?"))
        update(store, request(run, 2, blocker="Which schema version should the report read?", next="Wait for the decision."))
        update(store, request(run, 3, step_updates=[{"step_id": "edit", "status": "blocked"}]))
        messages, wake = recorder()
        drain(store, wake=wake)
        lines = [message for _, message in messages]
        assert len(lines) == 2 and all(" progress_blocked task=T identity=" in line for line in lines)
        assert not any("schema" in line for line in lines)


def test_a_proposed_binding_wakes_its_reviewer_and_falls_back_to_the_partner(fleet):
    from hx import planning, unit_execution
    from hx.runtime_notifications import wake_seat
    from .test_planning import bind, plan
    store = fleet[0]
    planning.apply(store, plan(fleet))
    run = unit_execution.assign(store, "implement", 1, "eng-001")["run_id"]
    bind(store, run)
    seats, seat = recorder()
    partner, wake = recorder()
    with store.transaction() as tx:
        tx.db.execute("UPDATE outbox SET status='delivered' WHERE kind<>'packet_binding_proposed'")
    drain(store, wake=wake, seat=seat)
    ((_, reviewer, line),) = seats
    assert reviewer == "qa-001" and partner == []
    assert " packet_binding_proposed task=implement identity=" in line and line.endswith(" reviewer=qa-001")
    # The reviewer has no live native session: the Partner gets the same pointer.
    assert wake_seat(store, "qa-001", line) is False
    with store.transaction() as tx:
        tx.db.execute("UPDATE outbox SET status='pending',available_at=0 WHERE kind='packet_binding_proposed'")
    drain(store, wake=wake)
    assert [message for _, message in partner] == [line]


def test_a_failed_acceptance_check_wakes_the_partner_with_the_evidence_identity(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Pass the acceptance check."}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
            tx.enqueue("unit_check_failed", "finish:request", {"status": "failed", "run_id": run, "request_id": "request",
                       "failed_check": "unit", "evidence": "hx evidence E1", "identity": "E1"})
        messages, wake = recorder()
        drain(store, wake=wake)
        ((_, line),) = messages
        assert " unit_check_failed task=T identity=E1" in line and "hx evidence" not in line


@pytest.mark.parametrize("kind", ["contract_published", "unit_completed"])
def test_a_published_contract_or_completion_wakes_the_partner_and_requeues_its_consumers(fleet, kind):
    from hx import planning
    from .test_planning import dependency, plan, unit
    store, repo = fleet[:2]
    planning.apply(store, plan(fleet, [unit(repo, "schema"), unit(repo, "report", paths=["src/report.py"], prerequisites=[dependency("schema")])]))
    with store.transaction() as tx:
        tx.db.execute("INSERT INTO dispatch_launches VALUES('deferred-key','continuation','report',1,'head','eng-002',NULL,'deferred','{}')")
        tx.enqueue(kind, f"{kind}:schema", {"task_id": "schema", "revision": 1, "contract_hash": "C1"})
        tx.db.execute("UPDATE outbox SET status='delivered' WHERE kind<>?", (kind,))
    messages, wake = recorder()
    drain(store, wake=wake)
    assert [line.split(" ", 2)[2] for _, line in messages] == [f"{kind} task=schema identity=C1"]
    assert store.db.execute("SELECT count(*) FROM dispatch_launches").fetchone()[0] == 0


def test_the_drain_budget_comes_from_config(tmp_path):
    import json
    from hx.errors import ValidationError
    with ContinuityStore(tmp_path) as store:
        (store.root / "config").mkdir(exist_ok=True)
        (store.root / "config" / "hx.json").write_text(json.dumps({"notifications": {"per_tick": 2}}))
        with store.transaction() as tx:
            for index in range(3):
                tx.enqueue("packet_covered", f"covered:{index}", {"task_id": f"T{index}"})
        assert len(drain(store, wake=lambda *a: True)) == 2
        (store.root / "config" / "hx.json").write_text(json.dumps({"notifications": {"per_tick": 0}}))
        with pytest.raises(ValidationError, match="notifications.per_tick"):
            drain(store, wake=lambda *a: True)
