"""Admission check at the packaged native executable boundary."""
from __future__ import annotations
import os
from pathlib import Path
import sys
from .board import ledger_reader, rows
from .errors import HxError, Refused
from . import native_processes


def verify(worker, adapter, mode, role='agent', *, env=None):
    child=os.environ if env is None else env
    if worker=='partner':
        return
    if mode!='exec' or role!='agent':
        raise Refused('workers start only through hx launch ID --run RUN --request REQUEST')
    keys=('AUTHORITY','RUN','LAUNCH','CAPSULE','ADAPTER')
    if any(not child.get('HX_CONTINUITY_'+key) for key in keys):
        raise Refused('worker execution requires an admitted native run and launch capsule')
    authority=Path(child['HX_CONTINUITY_AUTHORITY']).resolve()
    capsule=Path(child['HX_CONTINUITY_CAPSULE']).resolve()
    if adapter!=child['HX_CONTINUITY_ADAPTER'] or Path(child.get('HARNESS_ROOT','')).resolve()!=capsule:
        raise Refused('worker adapter or capsule does not match its native launch')
    with ledger_reader(authority) as db:
        if db is None:
            raise Refused('worker execution requires the packet ledger')
        found=rows(db,'''SELECT l.*,r.worker_id,r.ended_at,t.payload AS assignment
            FROM native_launches l JOIN runs r USING(run_id) JOIN unit_runs u USING(run_id)
            JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision
            WHERE l.launch_id=? AND l.run_id=?''',(child['HX_CONTINUITY_LAUNCH'],child['HX_CONTINUITY_RUN']))
    if not found:
        raise Refused('worker execution has no admitted native launch')
    launch=found[0]
    if launch['worker_id']!=worker or launch['ended_at'] is not None or launch['status'] not in {'starting','start_uncertain'} or not launch['assignment'].get('dependency_packet'):
        raise Refused('worker native launch is not authorized to start')
    if Path(launch['payload']['capsule']).resolve()!=capsule:
        raise Refused('worker native launch names another capsule')
    supervisor=launch['payload'].get('supervisor_process')
    pid=os.getppid()
    for _ in range(8):
        if pid<=1:
            break
        process=native_processes.probe(pid)
        if process is None:
            break
        if supervisor and process['identity']==supervisor:
            return
        pid=process['ppid']
    raise Refused('worker executable must descend from its original launch supervisor')


def main(argv=None):
    try:
        verify(*(sys.argv[1:] if argv is None else argv))
    except HxError as exc:
        print(f'worker admission: {exc}',file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
