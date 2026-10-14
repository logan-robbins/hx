"""Derive a sparse read set from the frozen assignment, preserving closure gaps."""

from __future__ import annotations

import json
from pathlib import Path

from . import dependency_packets
from .caller import require_partner_caller
from .errors import ValidationError


def request_body(store, task_id, destination, at='HEAD', *, env=None):
    require_partner_caller('repo assignment', env)
    from .code_store import namespace
    with store.transaction() as tx:
        task = tx.task(task_id)
        if task is None or not task['payload'].get('plan_id'):
            raise ValidationError('sparse assignment requires a current planned task')
        task = dependency_packets.effective_task(tx, task)
        payload = task['payload']
        workdir = Path(payload['workdir']).resolve()
        paths = set(payload['write_paths'])
        paths.add('.hx/map/manifest.json')
        gaps = []
        for recipe in payload['checks'].values():
            closure = recipe.get('source', {})
            if closure.get('complete') is not True:
                gaps.append('unknown source closure for check ' + recipe['id'])
            cwd = (workdir / recipe['cwd']).resolve()
            for value in [*recipe['inputs'], *closure.get('inputs', []), *closure.get('dependencies', [])]:
                target = (cwd / value).resolve()
                if not target.is_relative_to(workdir):
                    gaps.append('external check input requires separate provisioning')
                    continue
                paths.add(target.relative_to(workdir).as_posix())
        if not payload['checks']:
            gaps.append('worker checks are not bound; sparse read set is provisional')
        for ref in payload['map_inputs']:
            row = tx.db.execute('''SELECT payload FROM map_records
                WHERE repository=? AND snapshot=? AND record_id=? AND version=?''',
                (ref['repository'], ref['snapshot'], ref['id'], ref['version'])).fetchone()
            if row:
                record = json.loads(row[0])
                paths.update(anchor['path'] for anchor in record.get('anchors', []) if 'path' in anchor)
        contracts = []
        required_files = {}
        for need in payload['prerequisites']:
            if need.get('need', 'hard') != 'hard':
                contracts.append(need)
            else:
                from .unit_execution import _output
                proof = _output(tx, need)
                paths.update(proof.get('files', {}))
                for path, identity in proof.get('files', {}).items():
                    if path in required_files and required_files[path] != identity:
                        raise ValidationError('hard prerequisites require conflicting source identities: ' + path)
                    required_files[path] = identity
        return {'method': 'materialize', 'namespace': namespace(workdir), 'at': at,
                'required_files': required_files,
                'prefixes': sorted(paths), 'out': str(destination), 'working_copy': True,
                'assignment': {'task_id': task_id, 'revision': task['revision'],
                               'closure_complete': not gaps, 'gaps': sorted(set(gaps)),
                               'early_contracts': contracts}}
