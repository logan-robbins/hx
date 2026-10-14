"""Sealed, scoped delivery evidence for fixed candidates and work packets."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import change_delivery, delivery_validation, dependency_packets
from .continuity_store import canonical, digest
from .errors import HxError, ValidationError

MANIFEST = 'delivery-boundaries.json'


def snapshot(store, change_id, batch_id=None):
    with store.transaction() as tx:
        change = change_delivery._change(tx, change_id)
        task = dependency_packets.effective_task(tx, tx.task(change['payload']['evidence']['task_id']))
        packet = dependency_packets.summary(tx, task) if task['payload'].get('dependency_packet') else None
        coverage = {'covered': False, 'reasons': []}
        try:
            change_delivery._completion(store, change['payload']['run_id'], head=change['head'])
            coverage['covered'] = True
        except HxError as exc:
            coverage['reasons'].append(str(exc))
        review = {'approved': False, 'reasons': []}
        try:
            change_delivery._approved(store, change)
            review['approved'] = True
        except HxError as exc:
            review['reasons'].append(str(exc))
        candidate = delivery_validation.view(store, batch_id, _tx=tx) if batch_id else None
        if candidate and not delivery_validation.contains_change(store, batch_id, change_id):
            raise ValidationError('handoff candidate does not contain this change or its checkpoint')
    return {'change': change_id, 'revision': change['revision'], 'intent_hash': digest(change['payload']),
            'source_commit': change['head'], 'repository': change['payload']['repository'],
            'packet': packet, 'coverage': coverage, 'review': review, 'landing': change['status'],
            'candidate': candidate}


def render(capture):
    state = capture['boundaries']
    candidate = state['candidate']
    lines = ['# Delivery handoff', '',
             f"Authority: scoped evidence index, not deployment approval. Captured {capture['captured_at']}.", '',
             f"Change: `{state['change']}@{state['revision']}`; source `{state['source_commit']}`.",
             f"Packet covered: `{state['coverage']['covered']}`; review approved: `{state['review']['approved']}`; landing: `{state['landing']}`."]
    if state['packet']:
        lines.extend(['', 'Packet milestones and outstanding obligations:', '```json', canonical(state['packet']), '```'])
    if candidate:
        lines.extend(['', f"Candidate: `{candidate['batch']}`; boundary: `{candidate['boundary']}`; source: `{candidate['head']}`; tree: `{candidate['source_tree']}`.",
                      f"Validation status: `{candidate['status']}`; ready: `{candidate['ready']}`; deployable: `{candidate['deployable']}`.",
                      f"Tested build: `{candidate.get('build_id') or 'unavailable'}`; manifest: `{candidate.get('build_manifest') or 'unavailable'}`.",
                      'Required check identities and captured proof:', '```json',
                      canonical({'required_checks': candidate['required_checks'], 'proof': candidate['proof']}), '```'])
        if candidate.get('checkpoint') or candidate.get('source_coverage'):
            lines.extend(['Checkpoint and materialized source coverage:', '```json',
                          canonical({'checkpoint': candidate.get('checkpoint'), 'source_coverage': candidate.get('source_coverage')}), '```'])
    else:
        lines.extend(['', 'Integration and pre-deploy validation: unverified; no fixed candidate was captured.'])
    reasons = [*state['coverage']['reasons'], *state['review']['reasons'], *(candidate['reasons'] if candidate else [])]
    lines.extend(['', '## Open obligations', ''])
    lines.extend(f'{index}. {reason}' for index, reason in enumerate(reasons, 1))
    if not reasons:
        lines.append('No failed captured boundary. Unverified boundaries remain unverified.')
    lines.extend(['', 'These facts apply to the named source and artifact. Advancing main does not relabel this evidence.', ''])
    return '\n'.join(lines)


def capture(store, directory, change_id, batch_id=None):
    from .handoff import cmd_sums
    from argparse import Namespace
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    body = {'schema_version': 1, 'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'boundaries': snapshot(store, change_id, batch_id)}
    with store.transaction() as tx:
        artifact = tx.put_artifact(canonical(body).encode(), owner_type='delivery_handoff', owner_id=digest(body), slot='capture')
    (directory / MANIFEST).write_text(canonical({'artifact': artifact, 'capture': body}) + '\n')
    (directory / 'HANDOFF.md').write_text(render(body))
    cmd_sums(Namespace(dir=str(directory)))
    return {'artifact': artifact, **body}


def check(store, directory, *, live=False):
    from .handoff import SUMS, verify_sums
    directory = Path(directory)
    try:
        wrapper = json.loads((directory / MANIFEST).read_text())
        body = wrapper['capture']
        if body['schema_version'] != 1:
            raise ValidationError('unsupported delivery handoff schema')
        with store.transaction() as tx:
            if tx.read_artifact(wrapper['artifact']) != canonical(body).encode():
                raise ValidationError('handoff capture differs from its authoritative immutable artifact')
        if (directory / 'HANDOFF.md').read_text() != render(body):
            raise ValidationError('handoff text differs from its captured boundaries')
        if not (directory / SUMS).exists() or verify_sums(str(directory)):
            raise ValidationError('handoff checksums do not verify')
        if live:
            state = body['boundaries']
            current = snapshot(store, state['change'], state['candidate']['batch'] if state['candidate'] else None)
            if current != state:
                raise ValidationError('delivery boundaries changed; recapture before acting')
        return {'valid': True, 'captured_at': body['captured_at'], 'boundaries': body['boundaries'],
                'live': live, 'authority': 'evidence index'}
    except (OSError, ValueError, KeyError, HxError) as exc:
        return {'valid': False, 'reasons': [str(exc)]}
