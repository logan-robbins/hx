"""Box-wide .NET build/test slots: limits from each root's config, reclamation by process identity."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from hx import dotnet_slots
from hx.continuity_store import Conflict
from hx.errors import ValidationError

from .conftest import SRC

HOLDER = r'''
import json, os, sys, time
from pathlib import Path
from hx import dotnet_slots
args = json.loads(sys.argv[1])
dotnet_slots.directory = lambda: Path(args['directory'])
granted = refused = 0
for attempt in range(args['attempts']):
    job = f"{args['name']}-{attempt}"
    try:
        dotnet_slots.acquire(job, args['pool'], Path(args['root']))
    except Exception:
        refused += 1
        time.sleep(0.002)
        continue
    granted += 1
    time.sleep(args['hold'])
    dotnet_slots.release(job)
print(json.dumps({'granted': granted, 'refused': refused}))
'''

CRASH = r'''
import json, os, subprocess, sys
from pathlib import Path
from hx import check_artifacts, dotnet_slots
args = json.loads(sys.argv[1])
dotnet_slots.directory = lambda: Path(args['directory'])
dotnet_slots.acquire(args['job'], 'build', Path(args['root']))
if args['child']:
    process = subprocess.Popen(['sleep', '60'], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    dotnet_slots.child(args['job'], check_artifacts.owned_child(process))
    print(process.pid, flush=True)
os._exit(0)
'''


def configured(path, **pools):
    (path / 'config').mkdir(parents=True, exist_ok=True)
    (path / 'config' / 'hx.json').write_text(json.dumps({'pools': pools}))
    return path


@pytest.fixture
def slots(tmp_path, monkeypatch):
    folder = tmp_path / 'host-slots'
    monkeypatch.setattr(dotnet_slots, 'directory', lambda: folder)
    return folder


def child_env():
    return {**os.environ, 'PYTHONPATH': str(SRC)}


def test_limits_come_from_each_roots_config(tmp_path, slots):
    narrow = configured(tmp_path / 'narrow', build=1, test=1)
    wide = configured(tmp_path / 'wide', build=3)
    bare = tmp_path / 'bare'
    assert dotnet_slots.limit(bare, 'build') == 2 and dotnet_slots.limit(bare, 'test') == 4
    assert dotnet_slots.limit(narrow, 'build') == 1 and dotnet_slots.limit(wide, 'test') == 4
    dotnet_slots.acquire('wide-1', 'build', wide)
    with pytest.raises(Conflict, match='build concurrency limit'):
        dotnet_slots.acquire('narrow-1', 'build', narrow)
    dotnet_slots.acquire('narrow-test', 'test', narrow)  # pools are counted apart
    dotnet_slots.acquire('wide-2', 'build', wide)
    dotnet_slots.acquire('wide-3', 'build', wide)
    with pytest.raises(Conflict, match='build concurrency limit'):
        dotnet_slots.acquire('wide-4', 'build', wide)
    assert dotnet_slots.held('build') == 3 and dotnet_slots.held('test') == 1
    with pytest.raises(Conflict, match='already owns'):
        dotnet_slots.acquire('wide-3', 'build', wide)
    configured(tmp_path / 'bad', build=65)
    with pytest.raises(ValidationError, match='pools.build must be an integer from 1 to 64'):
        dotnet_slots.acquire('bad', 'build', tmp_path / 'bad')


def test_two_roots_with_different_configs_never_exceed_the_box_limit(tmp_path, slots):
    """Sixteen processes in two roots (build 1 and build 3) race for slots; a sampler watches the box."""
    narrow = configured(tmp_path / 'narrow', build=1)
    wide = configured(tmp_path / 'wide', build=3)
    dotnet_slots.held('build')  # create the ledger before the race
    peak, stop = [0], threading.Event()

    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], dotnet_slots.held('build'))
            time.sleep(0.001)

    sampler = threading.Thread(target=sample)
    sampler.start()
    started = time.perf_counter()
    workers = [subprocess.Popen([sys.executable, '-c', HOLDER, json.dumps(
        {'directory': str(slots), 'root': str(root), 'pool': 'build', 'name': f'{root.name}-{index}',
         'attempts': 40, 'hold': 0.01})], stdout=subprocess.PIPE, text=True, env=child_env())
        for index in range(8) for root in (narrow, wide)]
    outcomes = [json.loads(worker.communicate(timeout=120)[0]) for worker in workers]
    elapsed = time.perf_counter() - started
    stop.set()
    sampler.join()
    narrow_granted = sum(item['granted'] for item in outcomes[0::2])
    wide_granted = sum(item['granted'] for item in outcomes[1::2])
    print(f'\nslots: 16 processes x 40 attempts in {elapsed:.2f}s; granted narrow={narrow_granted} '
          f'wide={wide_granted}; refused={sum(item["refused"] for item in outcomes)}; peak held={peak[0]} (limits 1 and 3)')
    assert all(worker.returncode == 0 for worker in workers)
    # The narrow root acquires only on an empty box, so the wide root may starve it.
    assert wide_granted > 0 and 1 <= peak[0] <= 3
    assert dotnet_slots.held('build') == 0


def test_a_crashed_owners_slot_is_reclaimed_only_once_its_children_are_gone(tmp_path, slots):
    root = configured(tmp_path / 'root', build=1)
    crashed = subprocess.run([sys.executable, '-c', CRASH, json.dumps(
        {'directory': str(slots), 'root': str(root), 'job': 'crashed', 'child': False})], env=child_env(), check=True)
    assert crashed.returncode == 0 and dotnet_slots.metadata('crashed') is not None
    dotnet_slots.acquire('after-crash', 'build', root)  # the dead owner's slot is reclaimed
    assert dotnet_slots.metadata('crashed') is None
    dotnet_slots.release('after-crash')

    orphaned = subprocess.run([sys.executable, '-c', CRASH, json.dumps(
        {'directory': str(slots), 'root': str(root), 'job': 'orphaned', 'child': True})],
        env=child_env(), check=True, capture_output=True, text=True)
    group = int(orphaned.stdout.strip())
    try:
        with pytest.raises(Conflict, match='concurrency limit'):
            dotnet_slots.acquire('while-child-lives', 'build', root)
        assert dotnet_slots.metadata('orphaned')['children'][0]['group'] == group
    finally:
        os.killpg(group, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while True:
        try:
            dotnet_slots.acquire('after-child', 'build', root)
            break
        except Conflict:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
    assert dotnet_slots.metadata('orphaned') is None
