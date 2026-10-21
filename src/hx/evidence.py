"""Whole source evidence and bounded non-file observations for recovery."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from .continuity_store import ContinuityStore, canonical, digest
from .errors import ValidationError


def source_evidence(payload: dict, kind=None) -> bool:
    """Classify before loading content; old opaque native artifacts are whole."""
    if payload.get('evidence_scope') == 'whole_file':
        return True
    if payload.get('evidence_scope') == 'observation':
        return False
    if payload.get('artifact_hash') and payload.get('observation_hash'):
        return True
    data = payload.get('observation', payload)
    data = data.get('data', data) if isinstance(data, dict) else {}
    if not isinstance(data, dict):
        return False
    from .file_reads import SHELL_TOOLS, read_input, tool_name
    try:
        if read_input(data) is not None:
            return True
        if tool_name(data) in SHELL_TOOLS:
            from .file_inspection import _commands, inspection
            args = data.get('tool_input', {})
            command = args.get('command', args.get('cmd'))
            if isinstance(command, str) and any(inspection(argv) for argv in _commands(command)):
                return True
    except ValidationError:
        return True
    # Transcript results may omit the call's tool name. Their content cannot be
    # assumed to be a non-file log until its admitted source identity is checked.
    return ('tool_response' in data and not tool_name(data)
            or data.get('event') == 'complete_file_read'
            or data.get('source') in {'instruction_file', 'instruction_material'})


def verified_source(tx, row, payload, data, *, admitted=False):
    """Return exact original bytes and the historical admitted source receipt."""
    from .file_reads import delivered, text_candidates
    try:
        observation = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError('complete source evidence lacks its native observation identity') from exc
    observed = observation.get('data', observation) if isinstance(observation, dict) else {}
    if not isinstance(observed, dict):
        raise ValidationError('complete source evidence lacks its native observation identity')
    call, session = observed.get('tool_use_id'), payload.get('session_id')
    actor = observed.get('agent_id') or ''
    frozen = payload.get('source_receipt')
    receipts = [frozen] if isinstance(frozen, dict) else []
    if not receipts:
        found = tx.db.execute('''SELECT r.source_stamp FROM native_reads r
            JOIN native_launches l USING(launch_id) WHERE l.run_id=?
            AND r.session_id=? AND r.actor_id=? AND r.call_id=? AND (r.success=1 OR ?)''',
            (row['run_id'], session, actor, call, int(admitted))).fetchall()
        if not found and not actor:
            # A child tool may identify only its distinct native session. Match
            # the same unique registered child used by native_tools._actor.
            found = tx.db.execute('''SELECT r.source_stamp FROM native_reads r
                JOIN native_launches l USING(launch_id)
                JOIN native_children c ON c.launch_id=r.launch_id AND c.actor_id=r.actor_id AND c.session_id=r.session_id
                WHERE l.run_id=? AND r.session_id=? AND r.call_id=? AND (r.success=1 OR ?)
                AND (SELECT count(*) FROM native_children sibling WHERE sibling.launch_id=r.launch_id AND sibling.session_id=r.session_id)=1''',
                (row['run_id'], session, call, int(admitted))).fetchall()
        receipts = [json.loads(item[0]) for item in found]
    for receipt in receipts:
        if delivered(receipt, observed):
            for text in text_candidates(observed.get('tool_response')):
                raw = text.encode('utf-8')
                import hashlib
                if len(raw) == receipt['bytes'] and hashlib.sha256(raw).hexdigest() == receipt['sha256']:
                    return raw, receipt
    raise ValidationError('complete original source evidence is unavailable or differs from its admitted receipt; leave it pending')


def describe(tx, event_id):
    row = tx.db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
    if row is None:
        raise ValidationError(f'unknown or retired evidence {event_id}')
    payload = json.loads(row['payload'])
    if payload.get('retired'):
        raise ValidationError(f'retired evidence {event_id}: its task is closed and no current record cites it')
    if digest(payload) != row['payload_hash']:
        raise ValidationError('evidence payload differs from its captured identity')
    if 'artifact_hash' in payload:
        artifact = tx.db.execute('SELECT size FROM artifacts WHERE hash=?', (payload['artifact_hash'],)).fetchone()
        if artifact is None:
            raise ValidationError('evidence artifact is missing')
        size = artifact[0]
    else:
        size = len(canonical(payload.get('observation', payload)).encode())
    return row, payload, source_evidence(payload, row['kind']), size


def read(store: ContinuityStore, event_id: str, *, offset=None, limit=None, whole_limit=262144, expected_hash=None) -> dict:
    supplied_slice = offset is not None or limit is not None
    offset, limit = 0 if offset is None else offset, 8000 if limit is None else limit
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 262144:
        raise ValidationError("evidence requires offset>=0 and 1<=limit<=262144 bytes")
    with store.transaction() as tx:
        row, payload, whole, size = describe(tx, event_id)
        if expected_hash is not None and row['payload_hash'] != expected_hash:
            raise ValidationError('evidence changed after its frozen identity was checked')
        receipt = None
        if whole:
            if supplied_slice:
                raise ValidationError('source and instruction evidence prohibit offsets and limits; request the complete event once')
            if type(whole_limit) is not int or whole_limit < 0 or size > whole_limit:
                raise ValidationError('complete source evidence exceeds the independent remaining context budget; leave it pending; no prefix was delivered')
            data = tx.read_artifact(payload['artifact_hash']) if 'artifact_hash' in payload else canonical(payload.get('observation', payload)).encode()
            data, receipt = verified_source(tx, row, payload, data)
            total = len(data)
            source_hash = payload.get('artifact_hash', row['payload_hash'])
        elif "artifact_hash" in payload:
            data, total = tx.read_artifact_slice(payload["artifact_hash"], offset=offset, limit=limit)
            source_hash = payload["artifact_hash"]
        else:
            full = canonical(payload.get("observation", payload)).encode()
            if offset > len(full):
                raise ValidationError("evidence offset is past the end")
            data, total = full[offset:offset + limit], len(full)
            source_hash = row["payload_hash"]
        try:
            text = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            # Byte offsets remain exact even when the requested slice splits UTF-8.
            text = base64.b64encode(data).decode("ascii")
            encoding = "base64"
        result = {"event_id": event_id, "kind": row["kind"], "source_hash": source_hash,
                "offset": offset, "bytes": len(data), "total_bytes": total,
                "next_offset": offset + len(data) if offset + len(data) < total else None,
                "encoding": encoding, "content": text}
        if whole:
            result.update(complete=True, receipt=receipt)
        return result


def main(argv: list[str], root: Path, *, env=None) -> int:
    import os

    parser = argparse.ArgumentParser(prog="hx evidence",
                                     epilog="example: hx evidence EVENT_ID --offset 8000 --limit 8000")
    parser.add_argument("event_id")
    parser.add_argument("--offset", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    caller = os.environ if env is None else env
    with ContinuityStore.reader(root) as store:
        with store.snapshot() as tx:
            row, _, whole, _ = describe(tx, args.event_id)
            if whole and (caller.get('HARNESS_ID') or caller.get('HX_CONTINUITY_RUN')):
                raise ValidationError('source and instruction evidence cannot use native shell output; '
                                      'use the native complete-file Read operation or standalone hx file-read PATH broker; '
                                      'no source content was delivered')
            expected_hash = row['payload_hash']
        print(canonical(read(store, args.event_id, offset=args.offset, limit=args.limit, expected_hash=expected_hash)))
    return 0
