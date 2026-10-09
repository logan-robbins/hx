"""Compile reviewable assignments from current application records and task recipes."""
from __future__ import annotations

import json
from pathlib import Path

from . import appmap, planning, retrieval
from .continuity_store import Conflict, canonical
from .errors import ValidationError

RECIPES = {
    'change': 'Implement the requested behavior and preserve applicable interface invariants.',
    'repair': 'Add a regression check for the reported failure and verify the corrected behavior.',
    'api': 'Verify the requested contract, error behavior and affected callers.',
    'ui': 'Verify the requested interaction, state transitions and failure states.',
    'migration': 'Verify the declared data transition, compatibility and recovery requirements.',
    'qa': 'Check the requested behavior against the exact current source and environment.',
}


def draft(store, repository, snapshot, request):
    """Return a usable plan plus precise unknowns; never invent commands or paths."""
    if not isinstance(request, dict) or request.keys() - {'plan_id', 'task_id', 'goal', 'constraints', 'recipe', 'require', 'write_paths', 'acceptance'}:
        raise ValidationError('goal draft has unknown fields')
    for key in ('plan_id', 'goal'):
        if key not in request:
            raise ValidationError('goal draft requires plan_id and goal')
    recipe = request.get('recipe', 'change')
    if recipe not in RECIPES:
        raise ValidationError('unknown goal recipe')
    planning._id(request['plan_id'])
    task_id = request.get('task_id', request['plan_id'] + '.work')
    planning._id(task_id)
    if store.db.execute('SELECT 1 FROM task_heads WHERE task_id=?', (task_id,)).fetchone():
        raise Conflict('draft task already exists; use plan replan for amendments')
    brief = retrieval.plan_context(store, Path(repository), snapshot, request['goal'], required=request.get('require', ()))
    checks, paths, acceptance = {}, set(), list(request.get('acceptance', []))
    owners = set(request.get('require') or brief['coverage']['seed_ids'][:1])
    # Dependency/consumer context is read scope, not permission to edit every
    # neighboring component. Expand only implementation and declared check edges.
    for edge in brief['relations']:
        if edge['kind'] in {'implements', 'checked_by'} and edge['target'] in owners:
            owners.add(edge['source'])
    for edge in brief['relations']:
        if edge['source'] in owners and edge['kind'] in {'contains', 'checked_by'}:
            owners.add(edge['target'])

    if not acceptance:
        acceptance = [request['goal'], RECIPES[recipe]]
    ref_ids = owners | {edge['target'] for edge in brief['relations'] if edge['source'] in owners and edge['kind'] in {'implements', 'provides', 'consumes', 'depends_on'}}
    refs = []
    for node in brief['nodes']:
        if node['id'] in ref_ids:
            refs.append(dict(repository=brief['repo_id'], snapshot=snapshot, id=node['id'], version=node['version']))
        if node['kind'] == 'check' and node['id'] in owners:
            checks[node['id']] = node['data']['recipe']
        if node['id'] in owners:
            for anchor in node['anchors']:
                paths.add(anchor['path'])
        if node['id'] in ref_ids:
            acceptance.extend(node['data'].get('invariants', []))
    writes = request.get('write_paths', sorted(paths)) if recipe != 'qa' else request.get('write_paths', [])
    acceptance = list(dict.fromkeys(acceptance))
    unit = dict(id=task_id, expected_revision=0, behavior=request['goal'], acceptance=acceptance,
                workdir=str(Path(repository).resolve()), write_paths=writes, map_inputs=refs,
                checks=checks, outputs=[dict(id='check.' + name, version=1, kind='receipt', check_id=name) for name in checks],
                prerequisites=[])
    if writes:
        unit['outputs'].append(dict(id='source', version=1, kind='source', paths=writes))
    plan = dict(schema_version=1, plan_id=request['plan_id'], expected_revision=0, repository=brief['repo_id'],
                goal=request['goal'], constraints=request.get('constraints', []), tasks=[unit])
    unknowns = list(brief['discovery'])
    if not checks:
        unknowns.append('Select a declared check for this implementation boundary.')
    if not writes and recipe != 'qa':
        unknowns.append('Name the exact implementation and test paths; no current source anchors were selected.')
    if len(refs) > 8:
        unknowns.append('Split this behavior: its required map context exceeds eight records.')
    valid = bool(checks) and len(refs) <= 8 and (bool(writes) or recipe == 'qa')
    if valid:
        planning.validate(store, plan)
    commands = [{'id': name, 'argv': value['argv'], 'cwd': value['cwd']} for name, value in checks.items()]
    return {'plan': plan, 'ready': valid and not unknowns, 'unknowns': unknowns,
            'known': [{'id': node['id'], 'summary': node['summary']} for node in brief['nodes']],
            'commands': commands, 'first_action': ('Run the supplied reproduction/check command.' if recipe in {'repair', 'qa'} and commands
                else 'Inspect the supplied source anchors for the requested edit.' if paths else 'Resolve the named unknowns.'),
            'input_hash': brief['input_hash']}


def changes(store, *, plan_id=None, limit=16, details=False):
    """Small Partner-facing impact summaries; no event or receipt body reads."""
    rows = store.db.execute('''SELECT q.*,json_extract(t.payload,'$.plan_id') AS plan_id,
        i.entity_version AS old_version FROM task_replan_queue q
        JOIN task_heads h ON h.task_id=q.task_id AND h.revision=q.task_revision
        JOIN tasks t ON t.task_id=h.task_id AND t.revision=h.revision
        LEFT JOIN task_map_inputs i ON i.task_id=q.task_id AND i.task_revision=q.task_revision
          AND i.repository=q.repository AND i.snapshot=q.snapshot AND i.entity_id=q.entity_id
        WHERE (? IS NULL OR json_extract(t.payload,'$.plan_id')=?)
        ORDER BY q.task_id,q.entity_id LIMIT ?''', (plan_id, plan_id, limit)).fetchall()
    results = []
    for row in rows:
        item = dict(task_id=row['task_id'], revision=row['task_revision'], plan_id=row['plan_id'],
                    record=row['entity_id'], old_version=row['old_version'], new_version=row['entity_version'],
                    reason=row['reason'][:240], next_action='Rebind this assignment to the current contract and checks.')
        for label, version in (('before', row['old_version']), ('after', row['entity_version'])):
            view = store.db.execute("""SELECT CASE WHEN length(CAST(json_extract(payload,'$.summary') AS BLOB))<=240
                THEN json_extract(payload,'$.summary') END AS summary,
                CASE WHEN ? AND length(CAST(json_extract(payload,'$.data') AS BLOB))<=512 THEN json_extract(payload,'$.data') END AS data
                FROM map_records WHERE repository=? AND snapshot=? AND record_id=? AND version=?""",
                (int(details), row['repository'], row['snapshot'], row['entity_id'], version)).fetchone()
            item[label] = {'summary': view['summary']} if view else None
            if details and view:
                item[label]['data'] = json.loads(view['data']) if view['data'] else None
        item['details_required'] = any(item[label] is None or item[label]['summary'] is None or (details and item[label]['data'] is None) for label in ('before', 'after'))
        results.append(item)
    return results


def replan(store, plan_id):
    row = store.db.execute('SELECT p.* FROM plans p JOIN plan_heads h USING(plan_id,revision) WHERE plan_id=?', (plan_id,)).fetchone()
    if row is None:
        raise ValidationError('unknown plan')
    body = json.loads(row['payload'])['definition']
    body['expected_revision'] = row['revision']
    impacted, unknowns = changes(store, plan_id=plan_id, limit=1024, details=True), []
    affected = {item['task_id'] for item in impacted}
    changed_outputs = {}
    for unit in body['tasks']:
        head = store.db.execute('SELECT revision FROM task_heads WHERE task_id=?', (unit['id'],)).fetchone()[0]
        unit['expected_revision'] = head
        if unit['id'] not in affected:
            continue
        for ref in unit['map_inputs']:
            current = appmap.get_record(store, Path(unit['workdir']), ref['snapshot'], ref['id'], refresh=True)
            if current['applicability'] != 'current' or current['record']['claim'] == 'hypothesis':
                unknowns.append('Refresh the changed application record: ' + ref['id'])
                continue
            ref['version'] = current['record']['version']
            if current['record']['kind'] == 'check' and ref['id'] in unit['checks']:
                unit['checks'][ref['id']] = current['record']['data']['recipe']
        # Facts may have introduced dependencies not originally pinned by the unit.
        pinned = {(r['repository'], r['snapshot'], r['id']) for r in unit['map_inputs']}
        for item in store.db.execute('SELECT * FROM task_replan_queue WHERE task_id=? AND task_revision=?', (unit['id'], head)):
            key = (item['repository'], item['snapshot'], item['entity_id'])
            if key not in pinned:
                current = appmap.get_record(store, Path(unit['workdir']), key[1], key[2], refresh=True)
                if current['applicability'] != 'current':
                    unknowns.append('Refresh the changed application record: ' + key[2])
                else:
                    unit['map_inputs'].append(dict(repository=key[0], snapshot=key[1], id=key[2], version=current['record']['version']))
    # Propagate revised output contracts through the DAG; leave unrelated units alone.
    while True:
        changed = False
        for unit in body['tasks']:
            if any(dep['task_id'] in affected for dep in unit['prerequisites']) and unit['id'] not in affected:
                affected.add(unit['id'])
                changed = True
        if not changed:
            break
    for unit in body['tasks']:
        if unit['id'] not in affected:
            continue
        active = store.db.execute('SELECT run_id FROM runs WHERE task_id=? AND ended_at IS NULL', (unit['id'],)).fetchone()
        if active:
            unknowns.append('Checkpoint and stop active run before applying its revised assignment: ' + active[0])
        for output in unit['outputs']:
            published = store.db.execute('SELECT max(version) FROM unit_outputs WHERE task_id=? AND output_id=?', (unit['id'], output['id'])).fetchone()[0]
            if published is not None:
                output['version'] = max(output['version'], published + 1)
            changed_outputs[(unit['id'], output['id'])] = output['version']
    for unit in body['tasks']:
        for dep in unit['prerequisites']:
            dep['version'] = changed_outputs.get((dep['task_id'], dep['output_id']), dep['version'])
    if not unknowns:
        planning.validate(store, body)
    return {'plan': body, 'changes': impacted, 'affected_tasks': sorted(affected),
            'ready': not unknowns, 'unknowns': list(dict.fromkeys(unknowns))}
