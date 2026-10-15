"""A correction changes packet revision after the active run relinquishes ownership."""
import copy
from hx import board, planning, progress, show, unit_execution as units
from core.test_appmap import mapped
from core.test_map_updates import active
from core.test_unit_execution import fleet
from core.test_planning import unit, plan


def test_blocker_is_progress_then_correction_is_a_new_packet_revision(fleet):
    store,repo=fleet[:2]
    definition=plan(fleet,[unit(repo,'behavior')])
    planning.apply(store,definition)
    run=units.assign(store,'behavior',1,'be-001')['run_id']
    progress.update(store,{'task_id':'behavior','run_id':run,'expected_revision':0,'phase':'blocked',
        'step_updates':[],'next':'Resolve the conflicting acceptance statement.',
        'blocker':'The requested outputs conflict.','deliverables':[],'evidence_ids':[]},worker_id='be-001')
    blocked=board.collect(store.root)
    assert blocked['tasks'][0]['status']=='blocked'
    assert blocked['progress'][0]['payload']['blocker']=='The requested outputs conflict.'
    assert blocked['tasks'][0]['completion'] is None
    units.stop(store,run)
    revised=copy.deepcopy(definition)
    revised['expected_revision']=1
    revised['tasks'][0]['expected_revision']=1
    revised['tasks'][0]['behavior']='Return the clarified behavior.'
    planning.apply(store,revised)
    next_run=units.assign(store,'behavior',2,'be-001')['run_id']
    detail=show.collect(store.root,'behavior')
    assert detail['tasks'][0]['revision']==2
    assert detail['tasks'][0]['active_run_id']==next_run
    frozen={r['run_id']:r['assignment']['goal'] for r in detail['runs']}
    assert frozen[run]!=frozen[next_run]
