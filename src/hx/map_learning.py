"""Publish current application knowledge; no completed-task narrative memory."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import appmap, map_updates
from .continuity_store import Conflict, canonical
from .errors import ValidationError

LIMIT = 64


def publish(tx, run_id, task):
    """Pin useful map heads in the same transaction as successful completion."""
    refs = {(r['repository'], r['snapshot'], r['id']) for r in task.get('map_inputs', [])}
    refs.update(tuple(row) for row in tx.db.execute('''SELECT DISTINCT m.repository,m.snapshot,m.record_id
        FROM map_evidence m JOIN events e USING(event_id) WHERE e.run_id=? LIMIT ?''', (run_id, LIMIT + 1)))
    if len(refs) > LIMIT:
        raise ValidationError('completion knowledge exceeds 64 records; split the behavior unit')
    from .retrieval import STOP
    terms = list(dict.fromkeys(t for t in re.findall(r'[\w-]+', task['goal'].lower())
                               if 2 < len(t) and len(t.encode()) <= 128 and t not in STOP))[:12]
    published = []
    for key in sorted(refs):
        row = map_updates._record(tx.db, key[:2], key[2])
        if row is None or row['applicability'] != 'current':
            continue
        record = json.loads(row['payload'])
        if record['claim'] == 'hypothesis' or tx.db.execute(
                'SELECT 1 FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?', key).fetchone():
            continue
        # Completion never promotes an unverified source into reusable knowledge.
        from .map_dependencies import validate_refs
        ref = dict(repository=key[0], snapshot=key[1], id=key[2], version=row['version'])
        validate_refs(tx, [ref], task)
        tx.db.execute('INSERT OR REPLACE INTO map_publications VALUES(?,?,?,?,?)', (run_id, *key, row['version']))
        if record['kind'] in {'behavior', 'component', 'interface'}:
            # These terms are retrieval hints, never assertions or task instructions.
            previous = [item[0] for item in tx.db.execute('SELECT term FROM map_aliases WHERE repository=? AND snapshot=? AND record_id=? AND version=? ORDER BY term LIMIT 32', (*key, row['version']))]
            vocabulary = list(dict.fromkeys([*terms, *previous]))[:32]
            tx.db.execute('DELETE FROM map_aliases WHERE repository=? AND snapshot=? AND record_id=?', key)
            tx.db.executemany('INSERT INTO map_aliases VALUES(?,?,?,?,?)',
                             ((*key, row['version'], term) for term in vocabulary))
        published.append(ref)
    return published


def transfer(store, repository, snapshot, run_id):
    """Bring completed knowledge into an integration overlay after source installation.

    Existing divergent records are conflicts, not last-writer-wins updates. No task
    memory is read. Only this producer's bounded, published record set is inspected.
    """
    repository = Path(repository).resolve()
    repo_id = appmap.manifest(repository)['repo_id']
    rows = store.db.execute('''SELECT p.*,r.payload FROM map_publications p JOIN map_records r
        USING(repository,snapshot,record_id,version) WHERE p.run_id=? AND p.repository=?
        ORDER BY p.record_id LIMIT ?''', (run_id, repo_id, LIMIT + 1)).fetchall()
    if len(rows) > LIMIT:
        raise ValidationError('publication exceeds its record bound')
    proposed, stamps = {}, {}
    for row in rows:
        if row['snapshot'] == snapshot:
            continue
        # A later update supersedes this publication; old findings never reappear.
        head = map_updates._record(store.db, (repo_id, row['snapshot']), row['record_id'])
        if head is None or head['version'] != row['version'] or head['applicability'] != 'current':
            continue
        record = json.loads(row['payload'])
        for anchor in record['anchors']:
            actual = appmap.source_anchor(repository, anchor['path'], anchor['symbol'], with_stamp=True)
            if actual['anchor']['sha256'] != anchor['sha256']:
                raise Conflict('published source is not installed: ' + anchor['path'])
            stamps[anchor['path']] = actual['source_stamp']
        proposed[record['id']] = (row, record)
    with store.transaction() as tx:
        map_updates.require_worktree(tx.db, repository, repo_id, snapshot)
        selected = {}
        for identity, (source, record) in proposed.items():
            head = map_updates._record(tx.db, (repo_id, source['snapshot']), identity)
            if head is None or head['version'] != source['version'] or head['applicability'] != 'current' or tx.db.execute('SELECT 1 FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?', (repo_id, source['snapshot'], identity)).fetchone():
                raise Conflict('published knowledge changed during integration: ' + identity)
            current = map_updates._record(tx.db, (repo_id, snapshot), identity)
            if current and current['applicability'] == 'current' and appmap.semantic_identity(json.loads(current['payload'])) == appmap.semantic_identity(record):
                continue
            if current:
                # Only replace a version that was actually an ancestor of this finding.
                ancestor = any(appmap.semantic_identity(json.loads(old[0])) == appmap.semantic_identity(json.loads(current['payload']))
                    for old in tx.db.execute('SELECT payload FROM map_records WHERE repository=? AND snapshot=? AND record_id=? AND version<? ORDER BY version DESC LIMIT 64',
                        (repo_id, source['snapshot'], identity, source['version'])))
                if not ancestor:
                    raise Conflict('integration map has a divergent finding: ' + identity)
            selected[identity] = {**record, 'version': (current['version'] if current else 0) + 1}
        for record in selected.values():
            appmap.validate_record(record)
            for incoming in tx.db.execute('SELECT source,kind FROM map_relations WHERE repository=? AND snapshot=? AND target=?', (repo_id, snapshot, record['id'])):
                owner = selected.get(incoming['source'])
                if owner is None:
                    owner = json.loads(map_updates._record(tx.db, (repo_id, snapshot), incoming['source'])['payload'])
                appmap.validate_relation(owner['kind'], incoming['kind'], record['kind'])
            for edge in record['edges']:
                target = selected.get(edge['to'])
                if target is None:
                    existing = map_updates._record(tx.db, (repo_id, snapshot), edge['to'])
                    if existing is None or existing['applicability'] != 'current':
                        raise Conflict('published dependency is not available: ' + edge['to'])
                    target = json.loads(existing['payload'])
                appmap.validate_relation(record['kind'], edge['kind'], target['kind'])
        for path, stamp in stamps.items():
            if appmap._source_stamp(repository, path) != stamp:
                raise Conflict('source changed while transferring knowledge')
        for identity, record in selected.items():
            tx._change()
            # Column names keep this path independent of schema column order.
            tx.db.execute('INSERT INTO map_records VALUES(?,?,?,?,?,?,?,?,?)',
                (repo_id, snapshot, identity, record['version'], record['kind'], canonical(record),
                 canonical([]), canonical({'publication_run': run_id, 'anchors': record['anchors']}), 'current'))
            tx.db.execute('INSERT OR REPLACE INTO map_heads VALUES(?,?,?,?)', (repo_id, snapshot, identity, record['version']))
            appmap.index_relations(tx.db, repo_id, snapshot, record)
            tx.db.execute('DELETE FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?', (repo_id, snapshot, identity))
            # Existing consumers must revalidate their declarations against a
            # changed contract; copying an endpoint is not proof of compatibility.
            for edge in tx.db.execute("SELECT source,kind FROM map_relations WHERE repository=? AND snapshot=? AND target=? AND status='validated'", (repo_id, snapshot, identity)):
                if edge['source'] not in selected:
                    tx.db.execute("UPDATE map_relations SET status='stale' WHERE repository=? AND snapshot=? AND source=? AND kind=? AND target=?", (repo_id, snapshot, edge['source'], edge['kind'], identity))
            from .map_dependencies import invalidate_consumers
            invalidate_consumers(tx, (repo_id, snapshot), identity, record['version'], 'Integrated current application finding.')
        for identity, (source, record) in proposed.items():
            current = map_updates._record(tx.db, (repo_id, snapshot), identity)
            previous = [row[0] for row in tx.db.execute('SELECT term FROM map_aliases WHERE repository=? AND snapshot=? AND record_id=? AND version=? ORDER BY term LIMIT 32', (repo_id, snapshot, identity, current['version']))]
            incoming = [row[0] for row in tx.db.execute('SELECT term FROM map_aliases WHERE repository=? AND snapshot=? AND record_id=? AND version=? ORDER BY term LIMIT 32', (repo_id, source['snapshot'], identity, source['version']))]
            terms = list(dict.fromkeys([*incoming, *previous]))[:32]
            if set(terms) != set(previous):
                tx._change()
                tx.db.execute('DELETE FROM map_aliases WHERE repository=? AND snapshot=? AND record_id=?', (repo_id, snapshot, identity))
                tx.db.executemany('INSERT INTO map_aliases VALUES(?,?,?,?,?)', ((repo_id, snapshot, identity, current['version'], term) for term in terms))
        return {'records': {key: value['version'] for key, value in selected.items()}}


def integrate(store, repository, run_id, *, snapshot=None):
    """Every source-materialization path can carry its published application map."""
    repository = Path(repository).resolve()
    source = store.db.execute('''SELECT p.repository,o.baseline FROM map_publications p
        JOIN map_overlays o USING(repository,snapshot) WHERE p.run_id=? LIMIT 1''', (run_id,)).fetchone()
    if source is None:
        return {'records': {}}
    if snapshot is None:
        # Overlay creation uses the common committed baseline and does not write
        # repository files. Transfer validates the installed current sources.
        snapshot = map_updates.create_overlay(store, repository, source['baseline'])['snapshot']
    map_updates.advance_head(store, repository, source['repository'], snapshot)
    return {**transfer(store, repository, snapshot, run_id), 'snapshot': snapshot}


def vocabulary(repository):
    path = Path(repository) / '.hx/map/vocabulary.json'
    if not path.exists():
        return None
    body = appmap.read_json(path, 65536)
    if body.keys() != {'schema_version', 'records'} or body['schema_version'] != 1 or not isinstance(body['records'], dict) or len(body['records']) > 128:
        raise ValidationError('invalid portable application vocabulary')
    for identity, item in body['records'].items():
        appmap.identifier(identity)
        if not isinstance(item, dict) or item.keys() != {'identity', 'terms'} or not isinstance(item['identity'], str) or len(item['identity']) != 64:
            raise ValidationError('vocabulary requires a source-bound record identity')
        if not isinstance(item['terms'], list) or len(item['terms']) > 32 or any(not isinstance(t, str) or not t or len(t.encode()) > 128 for t in item['terms']):
            raise ValidationError('vocabulary terms exceed their bound')
    return body


def portable_vocabulary(store, repository, snapshot, records):
    """Prepare a bounded metadata update before any map publication writes."""
    repository = Path(repository)
    path = repository / '.hx/map/vocabulary.json'
    body = vocabulary(repository) or {'schema_version': 1, 'records': {}}
    repo_id = appmap.manifest(repository)['repo_id']
    for record in records:
        terms = [row[0] for row in store.db.execute('SELECT term FROM map_aliases WHERE repository=? AND snapshot=? AND record_id=? AND version=? ORDER BY term LIMIT 32',
                                                  (repo_id, snapshot, record['id'], record['version']))]
        body['records'].pop(record['id'], None)
        if terms:
            body['records'][record['id']] = {'identity': appmap.semantic_identity(record), 'terms': terms}
    # Optional query hints have a fixed portable budget; oldest entries go first.
    while len(body['records']) > 128 or len(canonical(body).encode()) > 65535:
        body['records'].pop(next(iter(body['records'])))
    if not path.exists() and not body['records']:
        return None
    if path.exists() and vocabulary(repository) == body:
        return None
    if appmap._dirty(repository, '.hx/map/vocabulary.json'):
        raise Conflict('publication would overwrite local application vocabulary edits')
    return path, canonical(body) + '\n'


def import_vocabulary(tx, repository, repo_id, snapshot, body):
    if body is None:
        return
    for identity, item in body['records'].items():
        row = map_updates._record(tx.db, (repo_id, snapshot), identity)
        if row and row['applicability'] == 'current' and appmap.semantic_identity(json.loads(row['payload'])) == item['identity']:
            tx.db.executemany('INSERT OR REPLACE INTO map_aliases VALUES(?,?,?,?,?)',
                             ((repo_id, snapshot, identity, row['version'], term) for term in item['terms']))
