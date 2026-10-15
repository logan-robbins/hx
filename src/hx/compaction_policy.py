"""Restore companion-maintained state; native summarization is an emergency boundary."""
PROMPT = '''The companion maintains current working state before a controlled reset.
Normal continuation restores that state after clear; it needs no second conversation summary.
If native compaction occurs, reference the latest runtime checkpoint without rewriting it.
Preserve only necessary observations newer than that checkpoint and unresolved obligations.
For the Partner, keep the current human goal, constraints, owners, dependencies, blockers,
unanswered questions and next scheduling decision. Omit intermediate agent exchanges,
routine progress, worker investigation and successful check details already represented in state.
For engineering, keep remaining steps, edited-source state, exact useful commands, unresolved
failures and approaches that must not be repeated. For QA, keep the tested revision/environment,
reproduction, assertion coverage, unresolved defects and remaining checks.
Write concise complete statements.
Keep the goal, exact constraints, current cursor and next action, unresolved blockers,
active child/tool operations, changed interfaces, and commands still needed to finish.
Keep negation, conditions, uncertainty, exact paths, symbols, commands and check status.
Use the runtime task checkpoint as current authority; do not rewrite its facts from memory.
Remove repeated tool output, superseded application states, completed exploration, and
irrelevant history. Preserve unresolved failures and call/result identities still needed.
Keep invariant information (identity, policy, learnings) byte-stable so the prompt cache
keeps paying for it; restructure working state only at a cold seam, when the cache has
expired and the rewrite costs nothing extra.
Do not invent successful checks, infer completion, or create personal worker memory.
Keep the summary within 4096 UTF-8 bytes. If mandatory obligations cannot fit, state the
remaining obligation and its checkpoint/evidence address; never silently truncate it.'''


def compact_instructions():
    return '# Compact instructions\n\n' + PROMPT + '\n'


def continue_context(ledger, row, observation):
    """A native reset reads current ledger state; it never waits for Jev or a companion."""
    from pathlib import Path
    from . import context_packets, native_controller, native_launch, prompt_compiler, unit_execution
    from .continuity_store import Conflict, digest
    from .store import atomic_write_text
    source = observation.get('source')
    if source not in {'compact', 'clear', 'resume'}:
        raise Conflict('unknown native continuation boundary')
    session = observation.get('session_id') or observation.get('sessionId') or observation.get('transcript_path')
    if row['payload'].get('native_session') != session:
        from .application_loop import _state
        if not (source == 'clear' and row['payload'].get('tool_selection', {}).get('adapter') == 'pi'
                and _state(ledger, 'tokens:' + row['launch_id']).get('reset') == 'submitted'):
            raise Conflict('continuation belongs to another native session')
    if row['status'] not in {'submitted', 'continuation_required'}:
        raise Conflict('continuation requires an active native assignment')
    if not row['payload'].get('submission_hash'):
        raise Conflict('the assignment was never submitted')
    pane = native_controller._owned_pane(row)
    from . import native_restart
    if native_restart.request_instruction_refresh(ledger, row):
        return None
    run_id = row['run_id']
    with ledger.transaction() as tx:
        _, task, _ = unit_execution._run(tx, run_id)
        current = native_launch._row(tx, run_id)
        if current['status'] not in {'submitted', 'continuation_required'} or current['payload'].get('pane') != pane:
            raise Conflict('native continuation changed before composition')
        _, rendered = prompt_compiler.verified_bundle(ledger.root, current['payload']['worker_id'],
            Path(current['payload']['manifest']), workdir=task['payload']['workdir'])
        cursors, pending = context_packets._pending(tx, run_id)
        boundary = digest([source, session, cursors, current['payload'].get('prompt_version')])
    request = 'continue-' + boundary
    # Forced mode preserves pending corrections and source gaps explicitly.
    # It does not classify, acknowledge, or delete anything on a model's behalf.
    overhead = len(rendered['system'].encode()) + 512
    budget = current['payload']['initial_input_limit'] - overhead
    packet = context_packets.issue(ledger, run_id, request_id=request, mode='forced',
        instructions=rendered['context'], max_tokens=budget, optional_tokens=min(1000, budget))
    path = Path(current['payload']['capsule']).parent / (packet['checkpoint_id'] + '.md')
    atomic_write_text(path, packet['text'])
    with ledger.transaction() as tx:
        unit_execution._run(tx, run_id)
        latest = native_launch._row(tx, run_id)
        if latest['status'] not in {'submitted', 'continuation_required'} or latest['payload'].get('pane') != pane:
            raise Conflict('native assignment changed during continuation')
        payload = {**latest['payload'], 'checkpoint_id': packet['checkpoint_id'], 'packet_path': str(path),
            'packet_hash': packet['packet_hash'], 'startup_observed': True, 'native_session': session,
            'required_files': native_controller.required_files(latest['payload'], packet),
            'instruction_delivery': 'waiting_for_complete_continuation_input', 'continuation_source': source,
            'continuation_input': {'phase': 'prepared', 'checkpoint_id': packet['checkpoint_id'], 'packet_hash': packet['packet_hash']},
            'charged_initial_tokens': packet['charged_tokens'] + native_controller.startup_overhead(current['payload']['capsule'], run_id, current['launch_id'], rendered['system'])}
        native_controller._transition(tx, run_id, {latest['status']}, 'continuation_required', payload=payload)
        # Read reuse never crosses a model context reset.
        tx.db.execute('DELETE FROM native_reads WHERE launch_id=? AND checkpoint_id<>?',
                      (row['launch_id'], packet['checkpoint_id']))
    # The application loop submits the full checkpoint after the native UI is
    # idle. Hook output never carries mandatory continuation material.
    return None


def prepare_native_compaction(ledger, row, observation):
    """Publish a current checkpoint before history disappears; no inference or acknowledgement."""
    from pathlib import Path
    from . import context_packets, native_controller
    from .continuity_store import digest
    from .store import atomic_write_text
    with ledger.transaction() as tx:
        cursors, _ = context_packets._pending(tx, row['run_id'])
    request = 'precompact-' + digest([row['launch_id'], observation, cursors])
    packet = context_packets.issue(ledger, row['run_id'], request_id=request, mode='forced', max_tokens=row['payload']['initial_input_limit'])
    path = Path(row['payload']['capsule']).parent / (packet['checkpoint_id'] + '.md')
    atomic_write_text(path, packet['text'])
    return None
