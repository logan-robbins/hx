import json
from pathlib import Path

import pytest

from hx import native_tools, runtime_policy
from hx.continuity_store import Conflict
from .test_appmap import mapped
from .test_map_updates import active
from .test_native_tools import runtime, configured, assignment, fleet, ready, submitted, payload, fire


def test_complete_read_duplicate_rejection_and_source_change(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'bounded.py'
    path.write_text('return 1\n')
    body = payload(tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    assert fire(store, run, row, 'log', {**body, 'tool_response': 'return 1'}) == 0
    assert fire(store, run, row, 'tool-start', {**body, 'tool_use_id': 'duplicate'}) == 2
    path.write_text('return 2\n')
    assert fire(store, run, row, 'tool-start', {**body, 'tool_use_id': 'changed'}) == 0


def test_error_result_does_not_poison_read_reuse(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'retry.py'
    path.write_text('return 1\n')
    body = payload(tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    with store.transaction() as tx:
        native_tools.settle(tx, run, body['session_id'], {**body, 'tool_response': 'transient error', 'is_error': True})
    assert fire(store, run, row, 'tool-start', {**body, 'tool_use_id': 'retry'}) == 0


def test_native_hooks_deny_sliced_reads_unbounded_searches_and_memory_growth(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'large.py'
    path.write_text('x' * 9000)
    requests = [('Read', {'file_path': str(path), 'offset': 1}),
                ('Read', {'file_path': str(path), 'limit': 10000}),
                ('Grep', {'path': str(repo), 'pattern': 'x'}),
                ('Write', {'file_path': str(Path(repo) / 'MEMORY.md'), 'content': 'x' * 5000}),
                ('Bash', {'command': 'cat large.py'}),
                ('Bash', {'command': 'rg x .'})]
    for index, (name, args) in enumerate(requests):
        assert fire(store, run, row, 'tool-start', payload(str(index), tool_name=name, tool_input=args)) == 2
    assert native_tools.pending(store, row['launch_id']) == 0


def test_bounded_search_remains_available(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    assert fire(store, run, row, 'tool-start', payload('search', tool_name='Grep',
        tool_input={'path': str(repo), 'pattern': 'Compiler', 'head_limit': 40})) == 0
    assert fire(store, run, row, 'tool-start', payload('shell-search', tool_input={'command': 'hx tool-exec --request search1 -- rg -m 20 Compiler compiler.py'})) == 0


def test_search_result_reused_after_reset_but_invalidated_by_source_change(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'coverage.py'
    path.write_text('Compiler = 1\n')
    body = payload('search1', tool_name='Grep', tool_input={'path': str(path), 'pattern': 'Compiler', 'head_limit': 40})
    assert fire(store, run, row, 'tool-start', body) == 0
    assert fire(store, run, row, 'log', {**body, 'tool_response': {'matches': ['Compiler = 1'], 'complete': True}}) == 0
    assert store.db.execute('SELECT count(*) FROM search_coverage').fetchone()[0] == 1
    # Cache identity depends on sources and query, not a worker's context checkpoint.
    assert fire(store, run, row, 'tool-start', {**body, 'tool_use_id': 'repeat'}) == 2
    path.write_text('Compiler = 2\n')
    assert fire(store, run, row, 'tool-start', {**body, 'tool_use_id': 'changed'}) == 0


def test_search_does_not_cache_errors_or_claim_truncated_absence(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'coverage.py'
    path.write_text('Compiler = 1\n')
    body = payload('error-search', tool_name='Grep', tool_input={'path': str(path), 'pattern': 'missing', 'head_limit': 1})
    assert fire(store, run, row, 'tool-start', body) == 0
    assert fire(store, run, row, 'log', {**body, 'tool_response': {'error': 'failed'}}) == 0
    assert store.db.execute('SELECT count(*) FROM search_coverage').fetchone()[0] == 0
    retry = {**body, 'tool_use_id': 'retry-search'}
    assert fire(store, run, row, 'tool-start', retry) == 0
    assert fire(store, run, row, 'log', {**retry, 'tool_response': {'matches': [], 'complete': True, 'truncated': True}}) == 0
    assert store.db.execute('SELECT complete FROM search_coverage').fetchone()[0] == 0


def test_search_cache_ignores_source_changed_during_search(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'coverage.py'
    path.write_text('before\n')
    body = payload('racing-search', tool_name='Grep', tool_input={'path': str(path), 'pattern': 'before', 'head_limit': 20})
    assert fire(store, run, row, 'tool-start', body) == 0
    path.write_text('after\n')
    assert fire(store, run, row, 'log', {**body, 'tool_response': 'before'}) == 0
    assert store.db.execute('SELECT count(*) FROM search_coverage').fetchone()[0] == 0


def test_required_full_instruction_material_can_be_read_without_excerpt_and_cannot_be_mutated(runtime):
    store, _, run, _ = runtime
    policy = store.root / 'continuity/policy/worker.md'
    policy.parent.mkdir(parents=True, exist_ok=True)
    full_policy = 'Preserve this exact required constraint and every subsequent paragraph.\n' * 250
    policy.write_text(full_policy)
    row = submitted(runtime)
    reference = row['payload']['required_files'][0]
    path = Path(reference['path'])
    complete = path.read_text()
    assert len(complete.encode()) > runtime_policy.CONTEXT_BYTES
    assert full_policy in complete
    # This is a verified exact material object, so the whole file is allowed.
    body = payload('material-full', tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    assert fire(store, run, row, 'log', {**body, 'tool_response': complete}) == 0
    assert fire(store, run, row, 'tool-start', payload('material-write', tool_name='Write',
        tool_input={'file_path': str(path), 'content': 'replacement'})) == 2
    assert path.read_text() == complete
    # An unrelated file at the same location gains no authority from its directory.
    other = path.parent / 'unregistered.md'
    other.write_text(complete)
    assert fire(store, run, row, 'tool-start', payload('unregistered', tool_name='Read',
        tool_input={'file_path': str(other)})) == 0
    path.write_text('damaged required instructions\n')
    assert fire(store, run, row, 'tool-start', payload('material-drift', tool_name='Read',
        tool_input={'file_path': str(path)})) == 2


def test_required_full_assignment_reference_is_verified_and_readable_after_startup(runtime):
    store, _, run, _ = runtime
    complete_constraint = '\n'.join(['This exact required acceptance condition survives every continuation.'] * 1000)
    with store.transaction() as tx:
        evidence = tx.db.execute('SELECT event_id FROM events WHERE run_id=? ORDER BY rowid LIMIT 1', (run,)).fetchone()[0]
        tx.put_record('required-full', expected_version=0, task_id='implement', kind='constraint',
            payload={'schema_version': 1, 'text': complete_constraint}, evidence=[evidence], inputs={},
            reason='All exact clauses are required by this assigned unit.', expires_when='The Partner replaces this constraint.')
    row = submitted(runtime)
    references = [item for item in row['payload']['required_files'] if item.get('kind') == 'assignment']
    reference = references[0]
    path = Path(reference['path'])
    from hx.instruction_materials import read_bundle
    complete = read_bundle(references).decode()
    constraint_line = next(line for line in complete.splitlines() if line.startswith('[required-full@1] '))
    assert json.JSONDecoder().raw_decode(constraint_line.split('] ', 1)[1])[0] == complete_constraint
    assert reference['bytes'] == path.stat().st_size <= 40000
    assert complete == Path(row['payload']['packet_path']).read_text()
    body = payload('full-assignment', tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    assert fire(store, run, row, 'log', {**body, 'tool_response': path.read_text()}) == 0
    assert fire(store, run, row, 'tool-start', payload('assignment-write', tool_name='Write',
        tool_input={'file_path': str(path), 'content': 'replacement'})) == 2
    path.write_text('damaged full assignment\n')
    assert fire(store, run, row, 'tool-start', payload('assignment-drift', tool_name='Read',
        tool_input={'file_path': str(path)})) == 2


@pytest.mark.parametrize('change', ['checkpoint_id', 'required_files', 'material'])
def test_material_read_rechecks_context_at_final_admission(runtime, monkeypatch, change):
    store, _, run, _ = runtime
    row = submitted(runtime)
    reference = row['payload']['required_files'][0]
    path = Path(reference['path'])
    inspect = runtime_policy.inspect_call

    def inspect_then_change(*args, **kwargs):
        reading = inspect(*args, **kwargs)
        if change == 'material':
            path.write_text('changed after initial inspection\n')
        else:
            current = {**row['payload'], change: 'retired-checkpoint' if change == 'checkpoint_id' else []}
            with store.transaction() as tx:
                tx._change()
                tx.db.execute('UPDATE native_launches SET payload=? WHERE launch_id=?',
                              (json.dumps(current), row['launch_id']))
        return reading

    monkeypatch.setattr(runtime_policy, 'inspect_call', inspect_then_change)
    assert fire(store, run, row, 'tool-start', payload('racing-material', tool_name='Read',
        tool_input={'file_path': str(path)})) == 2
    assert native_tools.pending(store, row['launch_id']) == 0


def test_assignment_read_revalidates_required_facts_inside_final_writer(runtime, monkeypatch):
    store, _, run, _ = runtime
    with store.transaction() as tx:
        evidence = tx.db.execute('SELECT event_id FROM events WHERE run_id=? ORDER BY rowid LIMIT 1', (run,)).fetchone()[0]
        tx.put_record('required-race', expected_version=0, task_id='implement', kind='constraint',
            payload={'schema_version': 1, 'text': 'Retain this exact acceptance condition.\n' * 1500},
            evidence=[evidence], inputs={}, reason='Required acceptance conditions.', expires_when='Partner replaces it.')
    row = submitted(runtime)
    references = [item for item in row['payload']['required_files'] if item.get('kind') == 'assignment']
    reference = references[0]
    inspect = runtime_policy.inspect_call

    def inspect_then_replace_fact(*args, **kwargs):
        reading = inspect(*args, **kwargs)
        with store.transaction() as tx:
            tx.put_record('required-race', expected_version=1, task_id='implement', kind='constraint',
                payload={'schema_version': 1, 'text': 'The required condition has changed.'},
                evidence=[evidence], inputs={}, reason='Partner replaced this constraint.', expires_when='Partner replaces it.')
        return reading

    monkeypatch.setattr(runtime_policy, 'inspect_call', inspect_then_replace_fact)
    assert fire(store, run, row, 'tool-start', payload('racing-fact', tool_name='Read',
        tool_input={'file_path': reference['path']})) == 2
    assert native_tools.pending(store, row['launch_id']) == 0


@pytest.mark.parametrize('target', ['packet', 'material'])
@pytest.mark.parametrize('operation', ['Add File', 'Update File', 'Delete File', 'Move to'])
def test_structured_patch_cannot_mutate_runtime_owned_context(runtime, target, operation):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = row['payload']['packet_path'] if target == 'packet' else row['payload']['required_files'][0]['path']
    patch = f'*** Begin Patch\n*** {operation}: {path}\n*** End Patch\n'
    assert fire(store, run, row, 'tool-start', payload('patch-material', tool_name='apply_patch', tool_input=patch)) == 2
    assert native_tools.pending(store, row['launch_id']) == 0


def test_large_product_instruction_file_does_not_block_other_tools(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    for relative in ['AGENTS.md', 'CLAUDE.md', '.claude/CLAUDE.md']:
        path = Path(repo) / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text('Product repository documentation.\n' * 400)
    assert fire(store, run, row, 'tool-start', payload('unrelated-tool')) == 0
    assert fire(store, run, row, 'tool-start', payload('ordinary-full-read', tool_name='Read',
        tool_input={'file_path': str(Path(repo) / 'AGENTS.md')})) == 0
    assert fire(store, run, row, 'tool-start', payload('ordinary-bounded-read', tool_name='Read',
        tool_input={'file_path': str(Path(repo) / 'AGENTS.md'), 'offset': 1, 'limit': 20})) == 2


def test_native_file_size_error_still_delivers_whole_source_in_one_logical_read(runtime):
    from hx import native_producer
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'native-too-large.py'
    path.write_text('# source\n' * 3000 + 'def required_tail():\n    pass\n')
    body = payload('native-limit', tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    assert fire(store, run, row, 'log-failure', {**body, 'tool_response': {'error': 'Native read token limit'}, 'is_error': True}) == 0
    assert store.db.execute('SELECT success FROM native_reads WHERE call_id=?', ('native-limit',)).fetchone()[0] == 1
    assert native_tools.pending(store, row['launch_id']) == 0
    assert native_producer.status(store, run)['gap'] is None
    assert fire(store, run, row, 'tool-start', payload('next-action')) == 0


def test_changed_file_delivery_fails_explicitly_and_can_retry_without_capture_gap(runtime):
    from hx import native_producer
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'changed-during-read.py'
    path.write_text('before\n')
    body = payload('raced-read', tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    path.write_text('after\n')
    assert fire(store, run, row, 'log', {**body, 'tool_response': 'before\n'}) == 0
    assert native_tools.pending(store, row['launch_id']) == 1
    assert native_producer.status(store, run)['gap'] is None
    assert fire(store, run, row, 'tool-start', payload('blocked-action')) == 2
    retry = {**body, 'tool_use_id': 'complete-retry'}
    assert fire(store, run, row, 'tool-start', retry) == 0
    assert fire(store, run, row, 'log', {**retry, 'tool_response': 'after\n'}) == 0
    assert native_tools.pending(store, row['launch_id']) == 0
    assert native_producer.status(store, run)['gap'] is None
    assert fire(store, run, row, 'tool-start', payload('resumed-action')) == 0
