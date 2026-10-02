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
def sleeper():
    with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as child:
        try:
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
    code = "import subprocess,time,pathlib,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
    with subprocess.Popen([sys.executable, '-c', code, str(child_file)]) as parent:
        child_identity = None
        try:
            wait_for(child_file.exists, what='owned child pid', limit=5)
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
