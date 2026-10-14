"""Bounded durable milestone projection and at-least-once Partner notification."""

from __future__ import annotations

import json


NOTIFY = {"plan_changed", "unit_completed", "unit_scope_violation", "contract_published",
          "packet_covered", "integration_verified", "deployable"}


def drain(store, *, wake=None, limit=8):
    if wake is None:
        from .wake import wake_partner
        wake = wake_partner
    delivered = []
    for _ in range(limit):
        with store.transaction() as tx:
            item = tx.claim_outbox()
        if item is None:
            break
        body = item['payload']
        if isinstance(body, str):
            body = json.loads(body)
        kind = item['kind']
        # A projection wakes no model. Indexed state is already committed; the
        # operation is its durable acknowledgement, never a second authority.
        notify_kind = body.get('delivery', kind)
        identity = body.get('identity') or body.get('contract_hash') or body.get('commit') or item['operation_id']
        message = f"HX-EVENT {item['operation_id']} {notify_kind} task={body.get('task_id', body.get('change', '-'))} identity={identity}"
        try:
            accepted = bool(wake(store.root, message)) if kind in NOTIFY or notify_kind in {'change_landed', 'candidate_verified', 'candidate_deployable', 'candidate_failed', 'repair_packet_created', 'change_reverted', 'validation_interrupted'} else True
            with store.transaction() as tx:
                tx.finish_outbox(item['operation_id'], item['lease_token'], error=None if accepted else 'Partner notification endpoint unavailable', retry_after=5)
            delivered.append({'id': item['operation_id'], 'kind': notify_kind, 'accepted': accepted})
        except OSError as exc:
            with store.transaction() as tx:
                tx.finish_outbox(item['operation_id'], item['lease_token'], error=str(exc), retry_after=5)
            delivered.append({'id': item['operation_id'], 'kind': notify_kind, 'accepted': False})
    return delivered
