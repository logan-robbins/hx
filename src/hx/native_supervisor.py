"""Record process ownership before starting any native executable."""
import os
import subprocess
import sys
from pathlib import Path

from .continuity_store import ContinuityStore, Conflict, canonical
from .native_processes import probe


def main(argv=None):
    root, run_id, launch_id, *command = sys.argv[1:] if argv is None else argv
    if not command:
        raise Conflict('supervisor requires a native command')
    identity = probe(os.getpid())['identity']
    with ContinuityStore(Path(root)) as store, store.transaction() as tx:
        from .native_launch import _row
        row = _row(tx, run_id)
        if not row or row['launch_id'] != launch_id or row['status'] not in {'starting', 'start_uncertain'}:
            raise Conflict('supervisor is not authorized by the original launch')
        previous = row['payload'].get('supervisor_process')
        if previous and previous != identity:
            raise Conflict('launch already has another supervisor instance')
        tx._change()
        tx.db.execute("UPDATE native_launches SET payload=? WHERE run_id=? AND status<>'archived'",
            (canonical({**row['payload'], 'supervisor_process': identity, 'spawn_returned': True}), run_id))
    return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
