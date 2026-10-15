"""Packaged execution boundaries require admitted worker runs; Partner boot is explicit."""
import json
import os
import subprocess
import sys
import pytest
from hx import native_launch, adapter_guard
from hx.errors import Refused
from .conftest import clean_env, wait_for
from .test_native_launch import configured
from .test_context_packets import assignment
from .test_map_updates import active
from .test_appmap import mapped
from .test_unit_execution import fleet


@pytest.mark.parametrize('adapter',['claude','codex','pi','grok','meta'])
@pytest.mark.parametrize('mode',[[],['--exec']])
def test_installed_adapter_refuses_unassigned_worker_before_execution(instance,adapter,mode):
    script=instance/'adapters'/adapter/'start.sh'
    result=subprocess.run(['bash',str(script),*mode,'eng-001'],env=clean_env(HARNESS_ROOT=str(instance),HX_PYTHON=sys.executable),text=True,capture_output=True)
    assert result.returncode!=0
    assert 'admission refused' in result.stderr
    assert not (instance/'run'/'eng-001'/'fake-argv.json').exists()


def test_forged_continuity_environment_does_not_authorize_worker(configured):
    store,repo,run=configured
    row=native_launch.prepare(store,'eng-001',run,'guard-proof',env={})
    env=native_launch.environment(store.root,row,env={})
    with pytest.raises(Refused,match='not authorized'):
        adapter_guard.verify('eng-001','claude','exec',env=env)
    with store.transaction() as tx:
        tx.db.execute("UPDATE native_launches SET status='starting' WHERE run_id=?",(run,))
    with pytest.raises(Refused,match='original launch supervisor'):
        adapter_guard.verify('eng-001','claude','exec',env=env)


def test_partner_bootstrap_preserves_native_auth_and_settings(instance,tmux_server):
    env=clean_env(HARNESS_ROOT=str(instance),HX_TMUX=' '.join(tmux_server),HX_PYTHON=sys.executable)
    adapters=instance/'adapters'/'claude'
    installed=subprocess.run(['bash',str(adapters/'install.sh'),'partner'],env=env,text=True,capture_output=True)
    assert installed.returncode==0,installed.stderr
    launched=subprocess.run(['bash',str(adapters/'start.sh'),'partner'],env=env,text=True,capture_output=True)
    assert launched.returncode==0,launched.stderr
    recorded=instance/'run'/'partner'/'fake-argv.json'
    wait_for(recorded.exists,what='fake Partner boot')
    value=json.loads(recorded.read_text())
    argv=value['argv']
    assert '--dangerously-skip-permissions' in argv
    assert argv[argv.index('--setting-sources')+1]=='user'
    assert value['env']['CLAUDE_CODE_OAUTH_TOKEN']
    assert value['env']['CLAUDE_CODE_OAUTH_TOKEN'] not in ' '.join(argv)
