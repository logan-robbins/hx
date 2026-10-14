"""Milestones reach Partner through a durable bounded notification path."""

from hx.continuity_store import ContinuityStore
from hx.runtime_notifications import drain


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
