"""The public CLI projects the same packet ledger as the UI."""
import json
import pytest
from .test_board import seed


@pytest.mark.parametrize('command',[['board','--json'],['show','orders.work','--json'],['show','run-1','--json'],['goals','--json']])
def test_cli_views_use_packet_projection(tmp_path,run_hx,command):
    seed(tmp_path)
    result=run_hx(*command,'--root',str(tmp_path))
    assert result.returncode==0,result.stderr
    document=json.loads(result.stdout)
    assert document['schema_version']==2
    assert document['tasks'][0]['task_id']=='orders.work'
    assert 'items' not in document and 'work_item' not in document


def test_read_defaults_to_status_and_full_is_explicit_packet_detail(tmp_path,run_hx):
    seed(tmp_path)
    status=run_hx('read','orders.work','--root',str(tmp_path))
    assert status.returncode==0,status.stderr
    assert 'orders.work@1' in status.stdout and 'receipt-1' not in status.stdout
    full=run_hx('read','orders.work','--full','--root',str(tmp_path))
    assert full.returncode==0,full.stderr
    assert json.loads(full.stdout)['checks'][0]['receipt_id']=='receipt-1'


def test_worker_task_is_the_exact_frozen_run_assignment(tmp_path,run_hx):
    seed(tmp_path)
    result=run_hx('task','--root',str(tmp_path),env_extra={'HARNESS_ID':'be-001','HX_CONTINUITY_RUN':'run-1'})
    assert result.returncode==0,result.stderr
    document=json.loads(result.stdout)
    assert document['task_id']=='orders.work' and document['task_revision']==1
    assert document['payload']['dependency_packet'] is True


def test_unknown_identity_has_no_file_fallback(tmp_path,run_hx):
    seed(tmp_path)
    (tmp_path/'tasks.json').write_text('{"be-999":{"goal":"obsolete"}}')
    result=run_hx('show','be-999','--json','--root',str(tmp_path))
    assert result.returncode!=0
    assert 'no packet' in result.stderr
