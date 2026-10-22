import hashlib
import json
import os
from contextlib import closing
from pathlib import Path

import pytest

from hx import native_tools, runtime_policy, search_coverage
from hx.continuity_store import Conflict
from .test_appmap import mapped
from .test_map_updates import active
from .test_native_tools import runtime, configured, assignment, fleet, ready, submitted, payload, fire, claude_read


def test_complete_read_duplicate_rejection_and_source_change(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'bounded.py'
    path.write_text('return 1\n')
    body = payload(tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    assert fire(store, run, row, 'log', {**body, 'tool_response': claude_read('return 1\n', path)}) == 0
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


def test_noisy_commands_route_by_shape_not_first_word(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    noisy = ['python3 -m pytest -q', 'uv run pytest', 'cd x && pytest', 'git -C x diff',
             'cd x&&pytest', 'uv run python -m pytest tests', 'FOO=1 env -i npx pnpm test',
             'make lint; git --no-pager log -3', 'ls x || cargo build', 'pytest 2>&1 | tee run.log']
    for index, command in enumerate(noisy):
        assert fire(store, run, row, 'tool-start', payload(f'noisy-{index}', tool_input={'command': command})) == 2, command
    quiet = ['python3 -m json.tool data.json', 'python3 script.py -m pytest', 'git -C x status', 'cd x && ls',
             'echo "a && pytest"', 'pip list | grep pytest', 'hx tool-exec --request t1 -- uv run pytest']
    for index, command in enumerate(quiet):
        assert fire(store, run, row, 'tool-start', payload(f'quiet-{index}', tool_input={'command': command})) == 0, command


def coverage(sql):
    """One value from this test's host search ledger."""
    with closing(search_coverage.connect()) as db:
        return db.execute(sql).fetchone()[0]


def test_search_result_reused_after_reset_but_invalidated_by_source_change(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'coverage.py'
    path.write_text('Compiler = 1\n')
    body = payload('search1', tool_name='Grep', tool_input={'path': str(path), 'pattern': 'Compiler', 'head_limit': 40})
    assert fire(store, run, row, 'tool-start', body) == 0
    assert fire(store, run, row, 'log', {**body, 'tool_response': {'matches': ['Compiler = 1'], 'complete': True}}) == 0
    assert coverage('SELECT count(*) FROM coverage') == 1
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
    assert coverage('SELECT count(*) FROM coverage') == 0
    retry = {**body, 'tool_use_id': 'retry-search'}
    assert fire(store, run, row, 'tool-start', retry) == 0
    assert fire(store, run, row, 'log', {**retry, 'tool_response': {'matches': [], 'complete': True, 'truncated': True}}) == 0
    assert coverage('SELECT complete FROM coverage') == 0


def test_search_cache_ignores_source_changed_during_search(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'coverage.py'
    path.write_text('before\n')
    body = payload('racing-search', tool_name='Grep', tool_input={'path': str(path), 'pattern': 'before', 'head_limit': 20})
    assert fire(store, run, row, 'tool-start', body) == 0
    path.write_text('after\n')
    assert fire(store, run, row, 'log', {**body, 'tool_response': 'before'}) == 0
    assert coverage('SELECT count(*) FROM coverage') == 0


def test_one_grep_serves_sixty_worktrees_at_a_commit_and_a_change_invalidates_only_its_own(tmp_path):
    """60 lanes in 3 roots, one worktree each at one commit, issue the same scoped Grep."""
    import subprocess
    import time
    from contextlib import ExitStack
    from hx.continuity_store import ContinuityStore
    repo = tmp_path / 'lane-00'
    (repo / 'src').mkdir(parents=True)
    git = ['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
           '-c', 'commit.gpgSign=false', '-c', 'core.hooksPath=/dev/null']
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    for index in range(40):
        (repo / 'src' / f'module_{index:02}.py').write_text(f'class Compiler{index}:\n    pass\n' * 50)
    subprocess.run([*git, 'add', '.'], check=True)
    subprocess.run([*git, 'commit', '-qm', 'Fixture'], check=True)
    lanes = [repo, *(tmp_path / f'lane-{index:02}' for index in range(1, 60))]
    for lane in lanes[1:]:
        subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(lane)], check=True)
    executions, served = [], {}

    def grep(store, index, call):
        lane = lanes[index]
        body = {'tool_name': 'Grep', 'tool_input': {'path': str(lane / 'src'), 'pattern': 'Compiler3:',
                                                    'output_mode': 'files_with_matches'}}
        request = search_coverage.prepare({'workdir': str(lane)}, body)
        try:
            with store.transaction() as tx:
                search_coverage.remember(tx, {'launch_id': f'launch-{index}'}, 'session', 'main', call, request)
        except native_tools.AdmissionDenied as exc:
            served[index] = str(exc)
            return
        executions.append(index)
        found = sorted(str(path) for path in (lane / 'src').glob('*.py') if 'Compiler3:' in path.read_text())
        with store.transaction() as tx:
            search_coverage.settle(tx, f'launch-{index}', 'session', 'main', call,
                                   {**body, 'tool_response': {'filenames': found, 'numFiles': len(found)}})

    with ExitStack() as stack:
        roots = [stack.enter_context(ContinuityStore(tmp_path / f'root-{index}')) for index in range(3)]
        began = time.perf_counter()
        for index in range(60):
            grep(roots[index % 3], index, 'first')
        elapsed = time.perf_counter() - began
        assert executions == [0] and sorted(served) == list(range(1, 60))
        for index, text in served.items():
            # Each lane is served its own checkout's paths, never lane 0's.
            assert f'{lanes[index]}/src/module_03.py' in text and str(repo) + '/' not in text
        (lanes[7] / 'src' / 'module_05.py').write_text('class Compiler3:\n    pass\n')
        served.clear()
        for index in range(60):
            grep(roots[index % 3], index, 'second')
        assert executions == [0, 7] and sorted(served) == [index for index in range(60) if index != 7]
        assert f'{lanes[8]}/src/module_05.py' not in served[8]
        grep(roots[1], 7, 'third')
        assert executions == [0, 7] and f'{lanes[7]}/src/module_05.py' in served[7]
        # A pipe in the scope is neither hashed nor waited on: the search is simply not cached.
        os.mkfifo(lanes[3] / 'src' / 'pipe')
        assert search_coverage.prepare({'workdir': str(lanes[3])}, {'tool_name': 'Grep',
            'tool_input': {'path': str(lanes[3] / 'src'), 'pattern': 'Compiler3:'}}) is None
    with closing(search_coverage.connect()) as db:
        assert db.execute('SELECT count(*) FROM coverage').fetchone()[0] == 2
    print(f'\nsearch cache: 60 worktrees in 3 roots, 1 execution, 59 served in {elapsed:.2f}s total')


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
    assert fire(store, run, row, 'log', {**body, 'tool_response': claude_read(complete, path)}) == 0
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


def test_required_full_assignment_parts_are_each_read_completely_before_other_work(runtime, capsys):
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
    parts = [Path(item['path']) for item in references]
    assert len(parts) > 1
    # Part 1 lists the whole chain up front; every footer names the part to read next.
    assert f'Read {parts[1]} next; part 1 of {len(parts)}; the parts in order: ' + ', '.join(map(str, parts)) in path.read_text()
    body = payload('full-assignment', tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    assert fire(store, run, row, 'log', {**body, 'tool_response': claude_read(path.read_text(), path)}) == 0
    # A started chain is delivered only when every part has its own complete-read receipt.
    assert native_tools.pending(store, row['launch_id']) == 1
    capsys.readouterr()
    assert fire(store, run, row, 'tool-start', payload('before-chain-end')) == 2
    assert f'read {parts[1]} next (part 2 of {len(parts)})' in capsys.readouterr().err
    for index, part in enumerate(parts[1:], 2):
        read = payload(f'assignment-part-{index}', tool_name='Read', tool_input={'file_path': str(part)})
        assert fire(store, run, row, 'tool-start', read) == 0
        assert fire(store, run, row, 'log', {**read, 'tool_response': claude_read(part.read_text(), part)}) == 0
    receipts = [json.loads(item[0]) for item in store.db.execute('SELECT source_stamp FROM native_reads WHERE success=1')]
    assert [receipt['material']['part'] for receipt in receipts] == list(range(1, len(parts) + 1))
    assert native_tools.pending(store, row['launch_id']) == 0
    assert fire(store, run, row, 'tool-start', payload('after-chain-end')) == 0
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


def emitted(capsys):
    """The additionalContext a hook printed for the native host."""
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if 'hookSpecificOutput' in line]
    assert len(lines) == 1
    return lines[0]['hookSpecificOutput']['additionalContext']


def test_claude_read_result_is_the_delivery_and_the_hook_adds_only_a_marker(runtime, capsys):
    from hx import native_producer
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'whole.py'
    source = '# source\n' * 3000 + 'def required_tail():\n    pass\n'
    path.write_text(source)
    body = payload('native-read', tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    capsys.readouterr()
    assert fire(store, run, row, 'log', {**body, 'tool_response': claude_read(source, path)}) == 0
    data = source.encode()
    assert emitted(capsys) == f'Complete file: sha256={hashlib.sha256(data).hexdigest()}; bytes={len(data)}'
    assert store.db.execute('SELECT success FROM native_reads WHERE call_id=?', ('native-read',)).fetchone()[0] == 1
    assert native_tools.pending(store, row['launch_id']) == 0
    assert native_producer.status(store, run)['gap'] is None
    assert fire(store, run, row, 'tool-start', payload('next-action')) == 0


def test_crlf_source_shown_as_lf_is_receipted_by_its_on_disk_bytes(runtime, capsys):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'windows.txt'
    data = b'first line\r\nsecond line\r\n'
    path.write_bytes(data)
    body = payload('crlf-read', tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    capsys.readouterr()
    # Claude shows CRLF line endings as LF (live, Claude Code 2.1.299); the receipt keeps the file's bytes.
    assert fire(store, run, row, 'log', {**body, 'tool_response': claude_read('first line\nsecond line\n', path)}) == 0
    assert emitted(capsys) == (f'Complete file: sha256={hashlib.sha256(data).hexdigest()}; bytes={len(data)}; '
                               'CRLF line endings shown as LF')
    stamp, success = store.db.execute('SELECT source_stamp,success FROM native_reads WHERE call_id=?', ('crlf-read',)).fetchone()
    assert success == 1 and json.loads(stamp)['sha256'] == hashlib.sha256(data).hexdigest()


def test_capped_or_failed_claude_read_fails_delivery_until_a_complete_retry(runtime, capsys):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'native-too-large.py'
    source = '# source\n' * 3000 + 'def required_tail():\n    pass\n'
    path.write_text(source)
    capped = claude_read('# source\n' * 1349, path)
    capped['file'].update(numLines=1349, totalLines=3003, truncatedByTokenCap=True)
    attempts = [('capped', 'log', {'tool_response': capped}),
                ('failed', 'log-failure', {'tool_response': {'error': 'Native read token limit'}, 'is_error': True})]
    for failures, (call, event, result) in enumerate(attempts, 1):
        body = payload(call, tool_name='Read', tool_input={'file_path': str(path)})
        assert fire(store, run, row, 'tool-start', body) == 0
        capsys.readouterr()
        assert fire(store, run, row, event, {**body, **result}) == 0
        assert emitted(capsys).startswith('Complete-file delivery failed')
        assert store.db.execute('SELECT success FROM native_reads WHERE call_id=?', (call,)).fetchone()[0] == -1
        # Each failed delivery holds the barrier until one complete retry resolves the file.
        assert native_tools.pending(store, row['launch_id']) == failures
        assert fire(store, run, row, 'tool-start', payload('blocked-' + call)) == 2
    retry = payload('complete', tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', retry) == 0
    assert fire(store, run, row, 'log', {**retry, 'tool_response': claude_read(source, path)}) == 0
    assert native_tools.pending(store, row['launch_id']) == 0
    assert fire(store, run, row, 'tool-start', payload('next-action')) == 0


def test_claude_worker_installs_one_hook_entry_per_event(runtime):
    row = submitted(runtime)
    settings = json.loads((Path(row['payload']['capsule']) / 'run/eng-001/home/settings.json').read_text())
    hooks = settings['hooks']
    assert all(len(entries) == 1 for entries in hooks.values())
    # A Read or Bash call fires PreToolUse, then PostToolUse or PostToolUseFailure: three entries, not ~22.
    assert sum(len(hooks[event]) for event in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure')) == 3
    assert '--read-segment' not in json.dumps(settings)


def test_changed_file_delivery_fails_explicitly_and_can_retry_without_capture_gap(runtime):
    from hx import native_producer
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'changed-during-read.py'
    path.write_text('before\n')
    body = payload('raced-read', tool_name='Read', tool_input={'file_path': str(path)})
    assert fire(store, run, row, 'tool-start', body) == 0
    path.write_text('after\n')
    assert fire(store, run, row, 'log', {**body, 'tool_response': claude_read('before\n', path)}) == 0
    assert native_tools.pending(store, row['launch_id']) == 1
    assert native_producer.status(store, run)['gap'] is None
    assert fire(store, run, row, 'tool-start', payload('blocked-action')) == 2
    retry = {**body, 'tool_use_id': 'complete-retry'}
    assert fire(store, run, row, 'tool-start', retry) == 0
    assert fire(store, run, row, 'log', {**retry, 'tool_response': claude_read('after\n', path)}) == 0
    assert native_tools.pending(store, row['launch_id']) == 0
    assert native_producer.status(store, run)['gap'] is None
    assert fire(store, run, row, 'tool-start', payload('resumed-action')) == 0
