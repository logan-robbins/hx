from pathlib import Path

import pytest

from hx import native_tools, runtime_policy
from hx.continuity_store import Conflict
from .test_appmap import mapped
from .test_map_updates import active
from .test_native_tools import runtime, configured, assignment, fleet, ready, submitted, payload, fire


def test_read_limit_duplicate_rejection_and_source_change(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'bounded.py'
    path.write_text('return 1\n')
    body = payload(tool_name='Read', tool_input={'file_path': str(path), 'offset': 1, 'limit': 20})
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
    assert fire(store, run, row, 'log', {**body, 'tool_response': 'transient error', 'is_error': True}) == 0
    assert fire(store, run, row, 'tool-start', {**body, 'tool_use_id': 'retry'}) == 0


def test_native_hooks_deny_oversized_reads_searches_memory_and_context(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    path = Path(repo) / 'large.py'
    path.write_text('x' * 9000)
    requests = [('Read', {'file_path': str(path)}),
                ('Read', {'file_path': str(path), 'limit': 10000}),
                ('Grep', {'path': str(repo), 'pattern': 'x'}),
                ('Write', {'file_path': str(Path(repo) / 'MEMORY.md'), 'content': 'x' * 5000}),
                ('Bash', {'command': 'cat large.py'}),
                ('Bash', {'command': 'rg x .'})]
    for index, (name, args) in enumerate(requests):
        assert fire(store, run, row, 'tool-start', payload(str(index), tool_name=name, tool_input=args)) == 2
    assert native_tools.pending(store, row['launch_id']) == 0
    Path(row['payload']['packet_path']).write_text('x' * 8193)
    assert fire(store, run, row, 'tool-start', payload('oversize-context')) == 2


def test_bounded_search_and_read_remain_available(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    assert fire(store, run, row, 'tool-start', payload('search', tool_name='Grep',
        tool_input={'path': str(repo), 'pattern': 'Compiler', 'head_limit': 40})) == 0
    assert fire(store, run, row, 'tool-start', payload('shell-search', tool_input={'command': 'hx tool-exec --request search1 -- rg -m 20 Compiler compiler.py'})) == 0
