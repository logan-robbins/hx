"""Ledger projections never infer assignments from files or worker configuration."""
import json
from pathlib import Path
import pytest
from hx import board, show, read, task, goals
from hx.continuity_store import ContinuityStore, canonical
from hx.errors import NotFound, Refused


def seed(root):
    """Recorded packet/run/proof state, independent of native executables."""
    payload = {'plan_id': 'orders', 'dependency_packet': True, 'repository': 'repo',
               'workdir': str(root), 'goal': 'Return the order result', 'acceptance': ['Result matches input'],
               'write_paths': ['order.py'], 'write_scope': ['order.py'], 'map_inputs': [],
               'outputs': [{'id': 'source', 'version': 1, 'kind': 'source', 'paths': ['order.py']}],
               'prerequisites': [], 'resources': [], 'check_policy': {'reviewer': 'qa-001', 'required_checks': ['unit']}}
    with ContinuityStore(root) as store:
        db = store.db
        db.execute("INSERT INTO task_heads VALUES('orders.work',1)")
        db.execute('INSERT INTO tasks VALUES(?,1,NULL,?,1)', ('orders.work',canonical(payload)))
        definition={'schema_version':2,'plan_id':'orders','goal':'Order lookup','tasks':[payload]}
        db.execute("INSERT INTO plan_heads VALUES('orders',1)")
        db.execute('INSERT INTO plans VALUES(?,1,?,?)', ('orders','hash',canonical({'definition': definition})))
        db.execute("INSERT INTO plan_units VALUES('orders',1,'orders.work',1)")
        db.execute("INSERT INTO runs VALUES('run-1','orders.work',1,'be-001',0,'implementing',NULL,NULL,1,NULL)")
        db.execute('INSERT INTO unit_runs VALUES(?,?,?)', ('run-1',str(root),canonical({'workspace':{}})))
        db.execute('INSERT INTO leases VALUES(?,?,?,?)',('repo','order.py','run-1','{}'))
        db.execute("INSERT INTO artifacts VALUES('artifact',0,1)")
        db.execute("INSERT INTO checkpoints VALUES('checkpoint-1','run-1',1,0,1,'{}','artifact',1)")
        db.execute("INSERT INTO receipts VALUES('receipt-1','run-1','unit','v1','{}','i','e','s','s',0,'artifact',0.5,1)")
        db.execute("UPDATE ledger_meta SET revision=7")
        db.commit()
    return payload


@pytest.fixture
def packet_root(tmp_path):
    seed(tmp_path)
    return tmp_path


def test_empty_view_does_not_create_database_or_read_old_state(tmp_path):
    (tmp_path/'tasks.json').write_text('{broken old state')
    (tmp_path/'pods').mkdir()
    (tmp_path/'pods'/'be-999-working.md').write_text('old worker')
    result=board.collect(tmp_path)
    assert result['tasks']==[] and result['runs']==[]
    assert not (tmp_path/'state').exists()
    assert 'items' not in result and 'memory' not in result


def test_packet_run_ownership_and_proof_are_projected(packet_root):
    result=board.collect(packet_root)
    assert result['ledger_revision']==7
    assert result['tasks'][0]['task_id']=='orders.work'
    assert result['tasks'][0]['worker_id']=='be-001'
    assert result['tasks'][0]['status']=='implementing'
    assert result['plans'][0]['plan_id']=='orders'
    assert result['leases'][0]['canonical_path']=='order.py'
    assert result['checkpoints'][0]['acknowledged']==1
    assert result['checks'][0]['valid']==1
    assert 'orders.work@1' in board.render_text(result)


@pytest.mark.parametrize('identity',['orders.work','run-1','be-001','orders'])
def test_detail_resolves_ledger_identity(packet_root,identity):
    assert show.collect(packet_root,identity)['tasks'][0]['task_id']=='orders.work'
    assert 'orders.work' in read.read(packet_root,identity,detail=True)


def test_unknown_configured_worker_is_not_assignment(packet_root):
    (packet_root/'config'/'be-999').mkdir(parents=True)
    with pytest.raises(NotFound): show.collect(packet_root,'be-999')


def test_task_uses_run_frozen_revision_and_checks_owner(packet_root):
    assert json.loads(task.task(packet_root,'be-001',run_id='run-1'))['payload']['goal']=='Return the order result'
    with pytest.raises(Refused): task.task(packet_root,'be-002',run_id='run-1')
    with pytest.raises(NotFound): task.task(packet_root,'be-999')


def test_goals_projects_plans_and_packet_dependencies(packet_root):
    result=goals.collect(packet_root)
    assert result['plans'][0]['payload']['definition']['goal']=='Order lookup'
    assert result['tasks'][0]['payload']['prerequisites']==[]
    assert 'goals' not in result


def test_reads_do_not_mutate_ledger(packet_root):
    database=packet_root/'state'/'continuity.sqlite'
    before=database.read_bytes()
    board.collect(packet_root); show.collect(packet_root,'run-1'); goals.collect(packet_root)
    assert database.read_bytes()==before


def test_overview_reports_cap_and_detail_selects_beyond_it(packet_root,monkeypatch):
    monkeypatch.setattr(board,'LIMIT',1)
    with ContinuityStore(packet_root) as store:
        payload=json.loads(store.db.execute('SELECT payload FROM tasks').fetchone()[0])
        payload={**payload,'goal':'Second packet'}
        store.db.execute("INSERT INTO task_heads VALUES('z.work',1)")
        store.db.execute('INSERT INTO tasks VALUES(?,1,NULL,?,2)',('z.work',canonical(payload)))
        store.db.execute("INSERT INTO plan_units VALUES('orders',1,'z.work',1)")
        store.db.commit()
    assert board.collect(packet_root)['truncated']
    assert show.collect(packet_root,'z.work')['tasks'][0]['payload']['goal']=='Second packet'


def test_frozen_run_assignment_and_current_packet_revision_are_distinct(packet_root):
    with ContinuityStore(packet_root) as store:
        payload=json.loads(store.db.execute('SELECT payload FROM tasks').fetchone()[0])
        store.db.execute('INSERT INTO tasks VALUES(?,2,NULL,?,2)',('orders.work',canonical({**payload,'goal':'Revised result'})))
        store.db.execute("UPDATE task_heads SET revision=2")
        store.db.execute("UPDATE plan_units SET task_revision=2")
        store.db.commit()
    detail=show.collect(packet_root,'run-1')
    assert detail['tasks'][0]['payload']['goal']=='Revised result'
    assert detail['runs'][0]['assignment']['goal']=='Return the order result'
    assert json.loads(task.task(packet_root,'be-001',run_id='run-1'))['task_revision']==1


def test_completion_and_delivery_are_recorded_proof_headers(packet_root):
    with ContinuityStore(packet_root) as store:
        db=store.db
        db.execute("INSERT INTO unit_completions VALUES('orders.work',1,'run-1',?)",(canonical({'state':'packet_covered','checks':{'unit':'receipt-1'}}),))
        db.execute("UPDATE runs SET outcome='completed',ended_at=2 WHERE run_id='run-1'")
        db.execute("DELETE FROM leases")
        db.execute("INSERT INTO unit_milestones VALUES('orders.work',1,'packet_covered','proof','{}')")
        db.execute("INSERT INTO delivery_changes VALUES('change-1',1,'head','reviewed',?)",(canonical({'run_id':'run-1'}),))
        db.execute("INSERT INTO delivery_validations VALUES('candidate-1','repo','head','integration','verified','{}')")
        db.commit()
    view=board.collect(packet_root)
    assert view['tasks'][0]['status']=='packet_covered'
    assert view['tasks'][0]['active_run_id'] is None
    assert view['leases']==[]
    assert view['milestones'][0]['kind']=='packet_covered'
    assert view['delivery']['changes'][0]['status']=='reviewed'
    assert view['delivery']['candidates'][0]['head']=='head'
    assert show.collect(packet_root,'orders.work')['delivery']['changes'][0]['change_id']=='change-1'


def test_public_views_exclude_nonpacket_continuity_records(packet_root):
    with ContinuityStore(packet_root) as store:
        store.db.execute("INSERT INTO task_heads VALUES('capture-only',1)")
        store.db.execute("INSERT INTO tasks VALUES('capture-only',1,NULL,'{}',1)")
        store.db.execute("INSERT INTO runs VALUES('capture-run','capture-only',1,'be-999',0,'assigned',NULL,NULL,1,NULL)")
        store.db.commit()
    assert [t['task_id'] for t in board.collect(packet_root)['tasks']]==['orders.work']
    with pytest.raises(NotFound): show.collect(packet_root,'capture-only')
    with pytest.raises(NotFound): task.task(packet_root,'be-999',run_id='capture-run')
