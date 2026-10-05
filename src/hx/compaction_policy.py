"""One concise compaction contract, installed into supported native prompt surfaces."""
PROMPT = '''Preserve only the current assignment. Write concise complete statements.
Keep the goal, exact constraints, current cursor and next action, unresolved blockers,
active child/tool operations, changed interfaces, and commands still needed to finish.
Keep negation, conditions, uncertainty, exact paths, symbols, commands and check status.
Use the runtime task checkpoint as current authority; do not rewrite its facts from memory.
Remove repeated tool output, superseded application states, completed exploration, and
irrelevant history. Preserve unresolved failures and call/result identities still needed.
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
    from .runtime_policy import CONTEXT_BYTES
    source = observation.get('source')
    if source not in {'compact', 'clear', 'resume'}:
        raise Conflict('unknown native continuation boundary')
    session = observation.get('session_id') or observation.get('sessionId') or observation.get('transcript_path')
    if row['payload'].get('native_session') != session:
        raise Conflict('continuation belongs to another native session')
    if row['status'] not in {'submitted', 'continuation_required'}:
        raise Conflict('continuation requires an active native assignment')
    if not row['payload'].get('submission_hash'):
        raise Conflict('the assignment was never submitted')
    pane = native_controller._owned_pane(row)
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
    budget = min(CONTEXT_BYTES, current['payload']['initial_input_limit'] - overhead)
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
            'packet_hash': packet['packet_hash'], 'startup_observed': True,
            'instruction_delivery': 'continuation_checkpoint', 'continuation_source': source}
        native_controller._transition(tx, run_id, {latest['status']}, 'submitted', payload=payload)
        # Read reuse never crosses a model context reset.
        tx.db.execute('DELETE FROM native_reads WHERE launch_id=? AND checkpoint_id<>?',
                      (row['launch_id'], packet['checkpoint_id']))
    return native_controller._pointer(path)


def prepare_native_compaction(ledger, row, observation):
    """Publish a current checkpoint before history disappears; no inference or acknowledgement."""
    from pathlib import Path
    from . import context_packets, native_controller
    from .continuity_store import digest
    from .store import atomic_write_text
    with ledger.transaction() as tx:
        cursors, _ = context_packets._pending(tx, row['run_id'])
    request = 'precompact-' + digest([row['launch_id'], observation, cursors])
    packet = context_packets.issue(ledger, row['run_id'], request_id=request, mode='forced', max_tokens=8192)
    path = Path(row['payload']['capsule']).parent / (packet['checkpoint_id'] + '.md')
    atomic_write_text(path, packet['text'])
    return native_controller._pointer(path)
