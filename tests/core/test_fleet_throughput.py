"""Fleet throughput: hooks never deny for contention; checks and passes leave the tick."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx import (application_loop as loop, hook_spool, hooks, learning_companion, loop_jobs, native_companion,
                native_completion, native_producer, native_tools)
from hx.continuity_store import Conflict, ContinuityStore
from .conftest import wait_for
from .test_appmap import mapped
from .test_map_updates import active
from .test_native_tools import runtime, configured, assignment, fleet, submitted, payload, fire, installed_command
from .test_native_companion import planned
from .test_native_completion import classified


def quiet_companions(monkeypatch):
    # These tests measure scheduling, not paid Companion passes.
    monkeypatch.setattr(loop, '_context_pass', lambda *a, **kw: {'status': 'idle'})
    monkeypatch.setattr(learning_companion, 'select', lambda *a, **kw: None)


def profile(root, **sections):
    path = root / 'config' / 'hx.json'
    config = json.loads(path.read_text()) if path.is_file() else {}
    for section, values in sections.items():
        config[section] = {**config.get(section, {}), **values}
    path.write_text(json.dumps(config))


def test_concurrent_hooks_never_deny_beside_a_mutating_loop(runtime, monkeypatch, capsys):
    store, _, run, env = runtime
    row = submitted(runtime)
    quiet_companions(monkeypatch)
    stop, ticks = threading.Event(), []
    def mutate():
        with ContinuityStore(store.root) as ledger:
            while not stop.is_set():
                loop.tick(ledger, env=env)
                ticks.append(time.monotonic())
    def invoke(event, body):
        began = time.monotonic()
        # The installed command, as the native host runs it: one process per hook.
        result = subprocess.run(installed_command(row, 'claude', event), input=json.dumps(body), text=True,
                                capture_output=True, env={'PATH': env['PATH']}, timeout=60)
        return time.monotonic() - began, result
    thread = threading.Thread(target=mutate, daemon=True)
    thread.start()
    try:
        with ThreadPoolExecutor(64) as pool:
            starts = list(pool.map(lambda index: invoke('tool-start', payload(f'burst-{index}')), range(64)))
            results = list(pool.map(lambda index: invoke('log', payload(f'burst-{index}', tool_response='done')), range(64)))
        # Results that arrived behind another of the session's deliveries queue; the loop delivers them in order.
        wait_for(lambda: not native_tools.pending(store, row['launch_id'])
                 and not native_producer.status(store, run)['pending_deliveries'], what='settlement', limit=60)
    finally:
        stop.set()
        thread.join(60)
    outcomes = starts + results
    refused = [result.stderr for _, result in outcomes if result.returncode or result.stderr]
    assert not refused, refused[:3]
    assert len(ticks) > 1, 'the loop must keep committing throughout the burst'
    assert hook_spool.pending(store.root) == []
    assert native_tools.pending(store, row['launch_id']) == 0
    assert native_producer.status(store, run)['gap'] is None
    latencies = sorted(seconds for seconds, _ in outcomes)
    p95 = latencies[int(len(latencies) * .95) - 1]
    with capsys.disabled():
        print(f"\nhook latency over {len(latencies)} invocations in bursts of 64 beside {len(ticks)} loop ticks: "
              f"p50 {latencies[len(latencies) // 2] * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms, max {latencies[-1] * 1000:.0f} ms")
    assert p95 < hooks.LEDGER_BUDGET_S


def test_contended_hook_passes_through_and_the_loop_replays_it_in_order(runtime, monkeypatch):
    store, _, run, _ = runtime
    row = submitted(runtime)
    monkeypatch.setattr(hooks, 'LEDGER_BUDGET_S', 0.3)
    holder = sqlite3.connect(store.path, isolation_level=None)
    holder.execute('BEGIN IMMEDIATE')
    try:
        began = time.monotonic()
        assert fire(store, run, row, 'tool-start', payload('deferred')) == 0
        assert time.monotonic() - began < 5
        assert len(hook_spool.pending(store.root, run)) == 1
        # Later hooks of the run queue behind it without waiting for the writer.
        began = time.monotonic()
        assert fire(store, run, row, 'log', payload('deferred', tool_response='done')) == 0
        assert time.monotonic() - began < .3
        assert len(hook_spool.pending(store.root, run)) == 2
    finally:
        holder.rollback()
        holder.close()
    # Until the loop replays them, deferred hooks are capture lag: every drain and completion barrier waits.
    assert native_producer.status(store, run)['deferred_hooks'] == 2 and native_producer.status(store, run)['lag']
    with pytest.raises(Conflict, match='unacknowledged deliveries'), store.snapshot() as tx:
        native_producer.require_drained(tx, run)
    applied = hook_spool.ingest(store)
    assert [item['event'] for item in applied] == ['tool-start', 'log']
    assert not any('gap' in item for item in applied)
    assert hook_spool.pending(store.root) == []
    assert store.db.execute("SELECT status FROM native_tool_calls WHERE call_id='deferred'").fetchone()[0] == 'settled'
    assert native_producer.status(store, run)['gap'] is None
    # With nothing deferred, hooks admit directly again.
    assert fire(store, run, row, 'tool-start', payload('direct')) == 0
    assert native_tools.pending(store, row['launch_id']) == 1


def test_tick_stays_fast_while_a_long_check_runs(runtime, monkeypatch, capsys):
    store, _, run, env = runtime
    row = submitted(runtime)
    quiet_companions(monkeypatch)
    with store.transaction() as tx:
        for index in range(15):
            tx.put_task(f'side-{index}', {'goal': 'Work beside the completing unit.'}, expected_revision=0)
            side = tx.start_run(f'side-{index}', 1, f'eng-{index + 2:03}')
            tx.append_event(side, 'main', f'side-{index}', 'tool_result', {'text': 'Unclassified side evidence.'})
    assert store.db.execute('SELECT count(*) FROM runs WHERE ended_at IS NULL').fetchone()[0] == 16
    started, release = threading.Event(), threading.Event()
    def long_check(ledger, run_id, check_id, **kwargs):
        started.set()
        release.wait(300)  # Stands in for a 300 s acceptance check.
        return {'valid': True, 'receipt_id': 'receipt-' + check_id, 'event_id': 'event-' + check_id}
    monkeypatch.setattr(native_completion.checks, 'run_check', long_check)
    native_completion.request(store, run, 'eng-001')
    assert fire(store, run, row, 'stop', {'session_id': row['payload']['native_session']}) == 0
    classified(store, run)
    jobs = loop_jobs.Jobs(store.root, workers=4)
    try:
        durations = []
        for _ in range(4):
            began = time.monotonic()
            loop.tick(store, env=env, jobs=jobs)
            durations.append(time.monotonic() - began)
        assert started.is_set() and not release.is_set()
        assert loop._state(store, 'finish:' + run)['status'] == 'checking'
        with capsys.disabled():
            print(f"\ntick with 16 runs and a 300 s check in flight: max {max(durations) * 1000:.0f} ms over {len(durations)} ticks")
        assert max(durations) < 1, durations
        release.set()
        wait_for(lambda: not jobs.running, what='check job to finish', limit=10)
        reaped = loop.tick(store, env=env, jobs=jobs)
        assert reaped['status'] == 'advancing' and reaped['run_id'] == run
        assert set(loop._state(store, 'finish:' + run)['receipts']) == {reaped['check']}
    finally:
        release.set()
        jobs.close()


def test_four_companion_slots_run_four_passes_together(planned, monkeypatch):
    store, run, _ = planned
    profile(store.root, companion={'max_concurrent': 4}, loop={'workers': 4})
    with store.transaction() as tx:
        for stream in ('s2', 's3', 's4', 's5'):
            tx.append_event(run, stream, 'evidence-' + stream, 'tool_result', {'text': 'Separate stream evidence.'})
    monkeypatch.setattr(learning_companion, 'select', lambda *a, **kw: None)
    together, release = threading.Barrier(4, timeout=10), threading.Event()
    lock, live, peak, seen = threading.Lock(), set(), [0], []
    def paid_pass(ledger, candidate, *, env=None, own=None):
        with lock:
            live.add(candidate['stream_id'])
            peak[0] = max(peak[0], len(live))
            seen.append(candidate['stream_id'])
        if len(seen) <= 4:
            together.wait()
            release.wait(10)
        with lock:
            live.discard(candidate['stream_id'])
        return {'status': 'committed'}
    monkeypatch.setattr(loop, '_context_pass', paid_pass)
    jobs = loop_jobs.Jobs(store.root, workers=loop_jobs.workers(store.root))
    try:
        first = loop.tick(store, jobs=jobs)
        # Five streams are due; four slots fill and the fifth waits its turn.
        assert len(first['companions']) == 4 and first['status'] == 'waiting'
        assert len(loop.tick(store, jobs=jobs)['companions']) == 4
        wait_for(lambda: peak[0] == 4, what='four concurrent passes', limit=10)
        release.set()
        wait_for(lambda: not live, what='passes to finish', limit=10)
        reaped = loop.tick(store, jobs=jobs)
        assert reaped['status'] == 'committed'
        wait_for(lambda: len(seen) == 5 and not live, what='the fifth pass', limit=10)
    finally:
        release.set()
        jobs.close()
    assert peak[0] == 4 and sorted(seen) == ['main', 's2', 's3', 's4', 's5']


def test_counted_slots_refuse_only_beyond_capacity(planned):
    store, run, _ = planned
    profile(store.root, companion={'max_concurrent': 2})
    with store.transaction() as tx:
        tx.append_event(run, 's2', 'evidence-s2', 'tool_result', {'text': 'Second stream evidence.'})
        tx.append_event(run, 's3', 'evidence-s3', 'tool_result', {'text': 'Third stream evidence.'})
    jobs = [native_companion.prepare(store, 'eng-001', run, stream, 'slot-' + stream) for stream in ('main', 's2', 's3')]
    for job in jobs[:2]:
        store.db.execute("UPDATE companion_jobs SET status='running' WHERE job_id=?", (job['job_id'],))
    with pytest.raises(native_companion.SlotBusy, match='all 2'):
        native_companion.execute(store, jobs[2]['job_id'])
    assert native_companion.status(store, jobs[2]['job_id'])['status'] == 'prepared'
    store.db.execute("UPDATE companion_jobs SET status='unresolved' WHERE job_id=?", (jobs[0]['job_id'],))
    with store.transaction() as tx:
        native_companion.occupied(tx, 2)
