"""Render the shipped packet UI through its real script and HTML."""
import json
from pathlib import Path
import shutil
import subprocess
import pytest
from .conftest import FIXTURES
STATIC=Path(__file__).resolve().parents[2]/'src/hx/ui/static'


@pytest.mark.skipif(shutil.which('node') is None,reason='node unavailable')
def test_packet_routes_render():
    script=Path(__file__).parent/'js/render.js'
    result=subprocess.run(['node',str(script),str(FIXTURES),str(STATIC)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    views=json.loads(result.stdout)
    assert 'orders.work' in views['overview']
    assert 'Return the order result' in views['overview']
    assert 'run-1' in views['runs']
    assert 'Check receipts' in views['show/orders.work']
    assert 'checkpoint-1' in views['show/orders.work']
    assert 'receipt-1' in views['show/orders.work']
    assert 'Send to Partner' in views['partner']
    assert all('work item' not in text.lower() for text in views.values())


def test_assets_have_no_external_dependencies():
    for name in ('app.js','style.css','index.html'):
        text=(STATIC/name).read_text()
        assert 'https://' not in text and 'http://' not in text
