"""Execution metrics use ledger proofs, never work item text or transcripts."""
from hx import metrics
from .test_board import seed


def test_ledger_metrics(tmp_path):
    seed(tmp_path)
    result=metrics.collect_fleet(tmp_path)
    assert result['packets']==1
    assert result['active_runs']==1
    assert result['leases']==1
    assert result['valid_checks']==1
    assert result['failed_checks']==0
    assert result['check_seconds']==0.5
    assert result['acknowledged_checkpoints']==1
    assert metrics.per_id(tmp_path,'be-001')['packets']==1


def test_metrics_empty_without_ledger(tmp_path):
    (tmp_path/'tasks.json').write_text('obsolete')
    assert metrics.collect_fleet(tmp_path)['packets']==0


def test_json_cli(tmp_path,capsys):
    seed(tmp_path)
    assert metrics.main(['--json'],tmp_path)==0
    assert '"active_runs": 1' in capsys.readouterr().out
