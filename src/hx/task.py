"""Read the caller's exact assigned packet from the ledger."""
import argparse
import json
import os
from .board import ledger_reader, rows
from .caller import require_agent_caller
from .errors import NotFound, Refused


def task(root, item_id, *, run_id=None):
    with ledger_reader(root) as db:
        if db is None:
            raise NotFound(f'{item_id}: no assigned packet')
        where = 'r.run_id=?' if run_id else 'r.worker_id=? AND r.ended_at IS NULL'
        found = rows(db, f'''SELECT r.run_id,r.worker_id,r.task_id,r.task_revision,r.phase,r.ended_at,t.payload
            FROM runs r JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision WHERE ({where}) AND json_extract(t.payload,'$.dependency_packet')=1''', (run_id or item_id,))
        if not found:
            raise NotFound(f'{item_id}: no assigned packet')
        result = found[0]
        if result['worker_id'] != item_id:
            raise Refused('packet belongs to another worker')
        binding = rows(db, 'SELECT * FROM unit_check_bindings WHERE run_id=?', (result['run_id'],))
        result['binding'] = binding[0] if binding else None
        from .search_receipts import areas, listing
        # Where a prior implementation would live, and the receipts a binding can cite.
        result['search_areas'] = areas(db, result['payload'])
        result['searches'] = listing(db, result['run_id'])
        return json.dumps(result, indent=2)


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog='hx task', epilog='example: hx task')
    parser.add_argument('--root', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    child = os.environ if env is None else env
    print(task(root, require_agent_caller('task', child), run_id=child.get('HX_CONTINUITY_RUN')))
    return 0
