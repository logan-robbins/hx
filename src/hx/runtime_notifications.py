"""Bounded durable milestone projection and at-least-once notification of the right party.

Each delivery is one pointer line, `HX-EVENT <operation> <kind> task=<task> identity=<id>`,
never a body. Most kinds wake the Partner. A proposed check binding wakes its configured
reviewer's live native session, or the Partner when the reviewer has none. A published
contract or a completed unit also clears its consumers' dispatch deferrals, so the
executor's next pass re-evaluates them. `notifications.per_tick` in config/hx.json bounds
one drain.
"""

from __future__ import annotations

import json

from .errors import HxError


NOTIFY = {"plan_changed", "unit_completed", "unit_scope_violation", "contract_published",
          "packet_covered", "integration_verified", "deployable", "native_launch_unresolved",
          "unit_check_failed", "progress_blocked"}
#: Kinds whose pointer goes to the reviewer the payload names.
REVIEWER = {"packet_binding_proposed"}
#: Kinds after which the executor re-evaluates the producer's consumers.
CONSUMERS = {"contract_published", "unit_completed"}
DELIVERY = {"change_landed", "candidate_verified", "candidate_deployable", "candidate_failed",
            "repair_packet_created", "change_reverted", "validation_interrupted"}
PER_TICK = 8
#: A reviewer's native session can take a pasted line in these states.
LIVE = ("ready", "submitted", "submission_unconfirmed", "continuation_required")


def per_tick(root):
    from .loop_jobs import setting
    return setting(root, "notifications", "per_tick", PER_TICK, low=1, high=256)


def _task(store, body):
    if body.get("task_id"):
        return body["task_id"]
    if body.get("run_id"):
        row = store.db.execute("SELECT task_id FROM runs WHERE run_id=?", (body["run_id"],)).fetchone()
        if row:
            return row[0]
    return body.get("change", "-")


def wake_seat(store, worker, text):
    """Paste one line into `worker`'s live native session: False when it has none."""
    from . import goal, native_controller, native_launch
    row = store.db.execute(f"""SELECT n.run_id FROM native_launches n JOIN runs r ON r.run_id=n.run_id
        WHERE r.worker_id=? AND r.ended_at IS NULL AND n.status IN ({','.join('?' * len(LIVE))})
        ORDER BY r.started_at DESC LIMIT 1""", (worker, *LIVE)).fetchone()
    if row is None:
        return False
    launch = native_launch._row(store, row[0])
    try:
        native_controller._owned_pane(launch)
        return goal.paste(launch["payload"]["session"], text, {"HX_TMUX": " ".join(launch["payload"]["tmux"])})
    except Exception:  # A pane that went away is no live session; the Partner gets the line.
        return False


def drain(store, *, wake=None, seat=None, limit=None):
    if wake is None:
        from .wake import wake_partner
        wake = wake_partner
    seat = wake_seat if seat is None else seat
    limit = per_tick(store.root) if limit is None else limit
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
        identity = (body.get('identity') or body.get('contract_hash') or body.get('binding_hash')
                    or body.get('commit') or item['operation_id'])
        message = f"HX-EVENT {item['operation_id']} {notify_kind} task={_task(store, body)} identity={identity}"
        try:
            if kind == 'learning_proposal':
                from .learning_protocol import publish
                publish(store.root, body)
            if kind in CONSUMERS and body.get('task_id'):
                from .plan_dispatch import wake_consumers
                with store.transaction() as tx:
                    wake_consumers(tx, body['task_id'])
            if kind in REVIEWER:
                reviewer = f" reviewer={body['reviewer']}"
                accepted = bool(seat(store, body['reviewer'], message + reviewer)) or bool(wake(store.root, message + reviewer))
            elif kind in NOTIFY or notify_kind in DELIVERY:
                accepted = bool(wake(store.root, message))
            else:
                accepted = True
            with store.transaction() as tx:
                tx.finish_outbox(item['operation_id'], item['lease_token'], error=None if accepted else 'notification endpoint unavailable', retry_after=5)
            delivered.append({'id': item['operation_id'], 'kind': notify_kind, 'accepted': accepted})
        except (OSError, HxError) as exc:
            with store.transaction() as tx:
                tx.finish_outbox(item['operation_id'], item['lease_token'], error=str(exc), retry_after=5)
            delivered.append({'id': item['operation_id'], 'kind': notify_kind, 'accepted': False})
    return delivered
