from __future__ import annotations

import os
import signal
import subprocess
import sys
from copy import deepcopy

import pytest

from hx import native_processes as processes
from hx.continuity_store import Conflict
from .conftest import wait_for


@pytest.fixture
def sleeper(tmp_path):
    ready=tmp_path/'sleeper-ready'
    code="import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(30)"
    with subprocess.Popen([sys.executable, '-c',code,str(ready)]) as child:
        try:
            # Darwin exec changes audit-token pidversion. Capture the actual
            # interpreter image, rather than Popen's transient fork generation.
            wait_for(ready.exists,what='sleeper interpreter ready',limit=5)
            yield child
        finally:
            child.kill()
            child.wait(timeout=5)


def test_kernel_identity_stop_and_kill(sleeper):
    observed = processes.probe(sleeper.pid)
    assert observed['ppid'] == os.getpid() and observed['uid'] == os.geteuid()
    identity = observed['identity']
    assert processes.send(identity, signal.SIGSTOP)
    wait_for(lambda: processes.current(identity)['stopped'], what='kernel stopped state', limit=2)
    assert processes.send(identity, signal.SIGKILL)
    sleeper.wait(timeout=5)
    assert processes.current(identity) is None
    assert processes.send(identity, signal.SIGKILL) is False


def test_stale_generation_and_boot_cannot_signal_live_pid(sleeper):
    identity = processes.probe(sleeper.pid)['identity']
    stale = deepcopy(identity)
    if sys.platform == 'darwin':
        stale['token'][7] += 1
    else:
        stale['start'] += 1
    assert not processes.send(stale, signal.SIGKILL)
    stale = {**identity, 'boot': 'previous-boot'}
    assert not processes.send(stale, signal.SIGKILL)
    assert sleeper.poll() is None


def test_signal_uses_kernel_instance_even_when_precheck_is_stale(sleeper, monkeypatch):
    # Simulate reuse after a successful userspace identity check. The kernel
    # token/pidfd must still refuse the unrelated process instance.
    if sys.platform != 'darwin':
        pytest.skip('Darwin audit-token stale-generation check')
    actual = processes.probe(sleeper.pid)
    wrong = deepcopy(actual['identity'])
    wrong['token'][7] += 1
    monkeypatch.setattr(processes, 'current', lambda _: actual)
    assert processes.send(wrong, signal.SIGKILL) is False
    assert sleeper.poll() is None


def test_controller_cannot_signal_itself():
    identity = processes.probe(os.getpid())['identity']
    with pytest.raises(Conflict, match='controller'):
        processes.send(identity, signal.SIGSTOP)


def test_frozen_parent_census_keeps_unrelated_process_out(sleeper, tmp_path):
    child_file = tmp_path / 'child-pid'
    ready=tmp_path/'child-ready'
    child_code="import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(30)"
    code = "import subprocess,time,pathlib,sys; p=subprocess.Popen([sys.executable,'-c',sys.argv[3],sys.argv[2]]); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
    with subprocess.Popen([sys.executable, '-c', code, str(child_file),str(ready),child_code]) as parent:
        child_identity = None
        try:
            wait_for(child_file.exists, what='owned child pid', limit=5)
            wait_for(ready.exists,what='owned child interpreter ready',limit=5)
            root = processes.probe(parent.pid)['identity']
            child_identity = processes.probe(int(child_file.read_text()))['identity']
            with pytest.raises(Conflict, match='must be stopped'):
                processes.descendants([root])
            processes.send(root, signal.SIGSTOP)
            wait_for(lambda: processes.current(root)['stopped'], what='stopped parent', limit=2)
            descendants = processes.descendants([root])
            assert descendants == [child_identity]
            assert all(item['pid'] != sleeper.pid for item in descendants)
        finally:
            if child_identity:
                processes.send(child_identity, signal.SIGKILL)
            parent.kill()
            parent.wait(timeout=5)


@pytest.mark.skipif(sys.platform!='darwin',reason='Darwin audit-token exec generation')
def test_actual_exec_invalidates_old_token_without_changing_pid(tmp_path):
    first=tmp_path/'before-exec'; gate=tmp_path/'exec-gate'; second=tmp_path/'after-exec'
    after="import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(30)"
    before="""import os,pathlib,sys,time
pathlib.Path(sys.argv[1]).write_text('ready')
while not pathlib.Path(sys.argv[2]).exists(): time.sleep(.005)
os.execl(sys.executable,sys.executable,'-c',sys.argv[4],sys.argv[3])
"""
    with subprocess.Popen([sys.executable,'-c',before,str(first),str(gate),str(second),after]) as child:
        try:
            wait_for(first.exists,what='pre-exec readiness',limit=5)
            old=processes.probe(child.pid)['identity']
            gate.write_text('execute')
            wait_for(second.exists,what='post-exec readiness',limit=5)
            new=processes.probe(child.pid)['identity']
            assert new['pid']==old['pid']==child.pid
            assert new['token'][7]!=old['token'][7]
            assert processes.current(old) is None
            assert processes.send(old,signal.SIGKILL) is False
            assert child.poll() is None
            assert processes.send(new,signal.SIGSTOP)
            wait_for(lambda: processes.current(new)['stopped'],what='stopped new image',limit=2)
            assert processes.send(new,signal.SIGKILL)
            child.wait(timeout=5)
        finally:
            child.kill(); child.wait(timeout=5)
