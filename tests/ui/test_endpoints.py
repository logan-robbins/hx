"""HTTP serves the single packet schema and retains guarded local assets."""
import pytest


@pytest.mark.parametrize('path',['/api/board','/api/goals','/api/show/orders','/api/show/orders.work','/api/show/run-1','/api/show/be-001'])
def test_ledger_endpoint(ui,path):
    status,document=ui.client.json(path)
    assert status==200
    assert document['tasks'][0]['task_id']=='orders.work'


@pytest.mark.parametrize('path',['/api/show/unknown','/api/show/..','/static/../data.py','/api/missing'])
def test_absent_paths_are_404(ui,path):
    assert ui.client.get(path).status==404


@pytest.mark.parametrize('path,kind',[('/static/app.js','javascript'),('/static/style.css','text/css')])
def test_assets_are_local_and_typed(ui,path,kind):
    response=ui.client.get(path)
    assert response.status==200
    assert kind in response.headers['Content-Type']
    assert 'no-store' in response.headers['Cache-Control']
