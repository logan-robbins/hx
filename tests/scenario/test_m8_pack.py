"""A dependency graph advances through admitted runs and actual check receipts."""
from hx import board, planning, unit_execution as units
from core.test_appmap import mapped
from core.test_map_updates import active
from core.test_unit_execution import fleet, finish, statuses
from core.test_planning import unit, plan, dependency, bind


def test_provider_receipt_unlocks_consumer_and_views_follow_the_same_authority(fleet):
    store,repo=fleet[:2]
    definition=plan(fleet,[unit(repo,'provider'),unit(repo,'consumer',prerequisites=[dependency('provider')])])
    planning.apply(store,definition)
    view=board.collect(store.root)
    assert {t['task_id'] for t in view['tasks']}=={'provider','consumer'}
    assert statuses(store)['consumer']['status']=='blocked'
    producer=units.assign(store,'provider',1,'be-001')['run_id']
    bind(store,producer)
    assert board.collect(store.root)['leases']
    finish(store,producer)
    assert statuses(store)['consumer']['status']=='ready'
    consumer=units.assign(store,'consumer',1,'be-002')['run_id']
    bind(store,consumer)
    finish(store,consumer)
    final=board.collect(store.root)
    assert {t['status'] for t in final['tasks']}=={'packet_covered'}
    assert not final['leases']
    assert all(c['valid'] for c in final['checks'])
    assert not (store.root/'tasks.json').exists()
    assert not (store.root/'pods').exists()
