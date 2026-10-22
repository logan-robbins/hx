"""Token pressure at native hooks, cache-aware reset boundaries, and model output caps.

Three regimes decide a seam, from the usage the ledger already captures:

- hard: context reached the threshold. Clear and re-issue the packet; the cache
  loss is accepted, and tools are refused until the reset lands.
- opportunistic: context reached `seam.soft_fraction` of the threshold, the
  prompt cache is cold anyway, and the targeted packet is at least
  `seam.min_benefit_bytes` smaller than what the conversation grew by. Cold
  means the last turn's cache write outweighed its cache read (a miss), or the
  conversation has been idle for `seam.cache_ttl_s` since its last assistant
  message. The next turn rewrites the whole prefix regardless, so installing
  the smaller packet then costs nothing extra.
- hold: anything else. A warm cache is never rewritten.

The packet a seam installs is composed deterministically from ledger state and
measured after every Companion pass (`materialise`), so a seam is the adapter's
reset command and one paste, never a wait on a model.
"""
from __future__ import annotations
import json
import time
from pathlib import Path

from .continuity_store import Conflict, canonical
from .config_models import load_models
from .config_harness import load_harness
from .errors import ValidationError

#: config/hx.json `seam` defaults. 300 s is the default prompt-cache lifetime; an
#: instance running with the 1-hour cache sets cache_ttl_s to 3600.
SEAM = {"soft_fraction": 0.6, "cache_ttl_s": 300, "min_benefit_bytes": 65536}
#: Live context is counted in tokens and packets in bytes.
BYTES_PER_TOKEN = 4
HARD, COLD_MISS, COLD_IDLE, HOLD = "hard", "cold_miss", "cold_idle", "hold"


def limits(root, worker):
    config = load_harness(Path(root) / 'config' / worker / 'harness.json', check_cross_file=False)
    model = load_models(Path(root) / 'config/models.json')[config.model]
    output = model.max_output_tokens or min(8192, max(1, (model.window - model.threshold) // 2))
    return {'threshold': max(1, min(model.threshold, model.window - output - min(4096, model.window // 10))),
            'autocompact_window': model.autocompact_window or model.window - output,
            'max_output_tokens': output, 'window': model.window}


def seam_profile(root):
    """The instance's `seam` from config/hx.json, or its defaults.

    Always the instance root: a launch capsule's hx.json carries only its paths.
    """
    from .loop_jobs import profile
    section = profile(root).get('seam') or {}
    if not isinstance(section, dict) or section.keys() - SEAM.keys():
        raise ValidationError('config/hx.json seam allows only ' + ', '.join(SEAM))
    value = {**SEAM, **section}
    fraction = value['soft_fraction']
    if type(fraction) not in (int, float) or not 0 < fraction <= 1:
        raise ValidationError('config/hx.json seam.soft_fraction must be a number in (0, 1]')
    for key, high in (('cache_ttl_s', 86400), ('min_benefit_bytes', 1 << 30)):
        if type(value[key]) is not int or not 0 <= value[key] <= high:
            raise ValidationError(f'config/hx.json seam.{key} must be an integer from 0 to {high}')
    return value


def regime(state, *, now, profile, packet_bytes):
    """HARD, COLD_MISS, COLD_IDLE or HOLD for one main conversation's token state."""
    tokens, threshold = state.get('tokens'), state.get('threshold')
    if type(tokens) is not int or type(threshold) is not int:
        return HOLD
    if tokens >= threshold:
        return HARD
    if tokens < profile['soft_fraction'] * threshold or type(packet_bytes) is not int:
        return HOLD
    grown = tokens - state.get('floor', 0)
    if grown * BYTES_PER_TOKEN - packet_bytes < profile['min_benefit_bytes']:
        return HOLD
    read, written = state.get('cache_read'), state.get('cache_creation')
    if type(read) is int and type(written) is int and written > 0 and written >= read:
        return COLD_MISS
    at = state.get('at')
    if type(at) in (int, float) and now - at >= profile['cache_ttl_s']:
        return COLD_IDLE
    return HOLD


def _next(old, count, bound, soft_fraction, usage=None, at=None):
    """The token state after one usage observation; a submitted reset stays submitted."""
    reset = old.get('reset')
    floor = count if reset == 'observed' or 'floor' not in old else old['floor']
    value = {'tokens': count, 'threshold': bound['threshold'], 'soft': int(soft_fraction * bound['threshold']),
             'floor': min(floor, count), 'reset': 'needed' if count >= bound['threshold'] else 'none'}
    if usage is not None:
        value.update(cache_read=usage.get('cache_read_input_tokens', usage.get('cacheRead', usage.get('cached_input_tokens'))),
                     cache_creation=usage.get('cache_creation_input_tokens', usage.get('cacheWrite')), at=at)
    else:
        value.update({key: old[key] for key in ('cache_read', 'cache_creation', 'at') if key in old})
    if reset == 'submitted':
        value.update(reset='submitted', seam=old.get('seam'))
    return value


def observe(store, row, event, payload):
    from .application_loop import _save, _state
    if any(payload.get(key) for key in ('agent_id', 'agentId', 'subagent_id')):
        return
    key = 'tokens:' + row['launch_id']
    old = _state(store, key)
    if event == 'context' and payload.get('source') in {'clear', 'compact'}:
        _save(store, key, {'tokens': 0, 'reset': 'observed', 'last_seam': old.get('seam')})
        return
    count = payload.get('context_tokens')
    if type(count) is not int or count < 0:
        return  # Never reinterpret cumulative spend as current context size.
    root = row['payload']['capsule']
    value = _next(old, count, limits(root, row['payload']['worker_id']), seam_profile(store.root)['soft_fraction'])
    if old != value:
        _save(store, key, value)


def guard(store, row):
    from .application_loop import _state
    from .native_tools import AdmissionDenied
    state = _state(store, 'tokens:' + row['launch_id'])
    if state.get('reset') in {'needed', 'submitted'} and state.get('seam', HARD) == HARD:
        raise AdmissionDenied('context threshold reached; end this turn so the companion checkpoint can be restored in a fresh context')


def materialise(store, run_id):
    """Measure the packet a seam would install now. Runs after every Companion pass."""
    from . import context_packets, native_launch, prompt_compiler
    from .application_loop import _save
    row = native_launch._row(store, run_id)
    if not row or row['status'] not in {'submitted', 'continuation_required'}:
        return None
    try:
        _, rendered = prompt_compiler.verified_bundle(store.root, row['payload']['worker_id'],
            Path(row['payload']['manifest']), workdir=row['payload']['workdir'])
        budget = row['payload']['initial_input_limit'] - len(rendered['system'].encode()) - 512
        packet = context_packets.issue(store, run_id, request_id='seam-preview', mode='forced',
            instructions=rendered['context'], max_tokens=budget, optional_tokens=min(1000, budget), preview=True)
        value = {'bytes': packet['bytes'], 'packet_hash': packet['packet_hash'], 'at': time.time()}
    except Exception as exc:
        # A failed measurement holds opportunistic seams; it must never fail the pass that ran it.
        value = {'error': f'{type(exc).__name__}: {str(exc)[:480]}', 'at': time.time()}
    _save(store, 'packet:' + run_id, value)
    return value


def reset_one(store, *, env=None):
    """Round-robin like native_completion.advance_one: a busy pane yields its turn."""
    from . import application_loop as loop
    after = loop._state(store, 'reset-round').get('after', '')
    keys = ['continuation:' + row[0] for row in store.db.execute("SELECT run_id FROM native_launches WHERE status='continuation_required' AND json_extract(payload,'$.continuation_input.phase')='prepared'")]
    keys += [row[0] for row in store.db.execute("""SELECT scope FROM runtime_cycles WHERE scope LIKE 'tokens:%'
        AND (json_extract(payload,'$.reset')='needed'
             OR json_extract(payload,'$.reset')='none' AND json_extract(payload,'$.tokens')>=json_extract(payload,'$.soft'))""")]
    for key in sorted(keys, key=lambda key: (key <= after, key)):
        try:
            result = _reset_one(store, key, env=env)
        except BaseException:
            loop._save(store, 'reset-round', {'after': key})
            raise
        if result:
            loop._save(store, 'reset-round', {'after': key})
            return result
    return None


def _reset_one(store, key, *, env=None):
    from . import application_loop as loop, goal, native_controller, native_launch, native_tools
    if key.startswith('continuation:'):
        return native_controller.submit_continuation(store, native_launch._row(store, key.split(':', 1)[1]), env=env)
    row = store.db.execute("SELECT scope,payload FROM runtime_cycles WHERE scope=?", (key,)).fetchone()
    launch = store.db.execute('SELECT run_id FROM native_launches WHERE launch_id=?', (row['scope'][7:],)).fetchone()
    if not launch:
        return None
    current = native_launch._row(store, launch[0])
    if current['launch_id'] != row['scope'][7:]:
        return None
    state = json.loads(row['payload'])
    packet = loop._state(store, 'packet:' + current['run_id']) or materialise(store, current['run_id']) or {}
    decided = regime(state, now=time.time(), profile=seam_profile(store.root), packet_bytes=packet.get('bytes'))
    if decided == HOLD:
        return None
    if current['status'] != 'submitted' or native_tools.pending(store, current['launch_id']):
        return None
    with store.transaction() as tx:
        if tx.db.execute("SELECT 1 FROM native_children WHERE launch_id=? AND status='active'", (current['launch_id'],)).fetchone():
            return None
        from .native_producer import require_drained
        require_drained(tx, current['run_id'])
    pane = native_controller._owned_pane(current)
    child = {**(env or {}), 'HX_TMUX': ' '.join(current['payload']['tmux'])}
    visible = goal.capture_pane(current['payload']['session'], child)
    if not visible or not goal.pane_is_idle(visible):
        return None
    from . import native_restart
    if native_restart.request_instruction_refresh(store, current):
        return {'status': 'instruction_restart_queued', 'run_id': current['run_id']}
    # Claim before sending; ambiguous delivery must not repeatedly clear state.
    # The packet itself is composed at the reset's own SessionStart, from the ledger.
    with store.transaction() as tx:
        actual = native_launch._row(tx, current['run_id'])
        latest = loop._state(tx, row['scope'])
        if actual['status'] != 'submitted' or actual['payload'].get('pane') != pane or latest != state:
            return None
        tx._change()
        tx.db.execute('UPDATE runtime_cycles SET payload=? WHERE scope=?',
                      (canonical({**state, 'reset': 'submitted', 'seam': decided}), row['scope']))
    adapter = current['payload']['tool_selection']['adapter']
    slash = (Path(current['payload']['capsule']) / 'adapters' / adapter / 'seam-command').read_text().strip()
    if not slash.startswith('/') or any(char.isspace() for char in slash):
        raise Conflict('invalid native reset command')
    goal.paste(current['payload']['session'], slash, child)
    return {'status': 'reset_submitted', 'run_id': current['run_id'], 'seam': decided}


def captured_usage(tx, run_id, stream_id, event):
    """Use incremental capture; never rescan the transcript in a hook."""
    if stream_id.startswith('child:'):
        return
    from .native_launch import _row
    row = _row(tx, run_id)
    if not row:
        return
    usage = event.data.get('last_usage') if event.data.get('usage_scope') == 'session_total' else event.usage
    if not usage:
        return
    adapter = row['payload'].get('tool_selection', {}).get('adapter')
    if adapter == 'codex':
        count = usage.get('input_tokens')
    else:
        fields = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens') if 'input_tokens' in usage else ('input', 'cacheRead', 'cacheWrite')
        count = sum(usage.get(key, 0) for key in fields) if fields[0] in usage else None
    if type(count) is not int or count < 0:
        return
    root = row['payload']['capsule']
    key = 'tokens:' + row['launch_id']
    old = tx.db.execute('SELECT payload FROM runtime_cycles WHERE scope=?', (key,)).fetchone()
    old = json.loads(old[0]) if old else {}
    value = _next(old, count, limits(root, row['payload']['worker_id']), seam_profile(tx.store.root)['soft_fraction'],
                  usage=usage, at=time.time())
    tx._change()
    tx.db.execute('INSERT OR REPLACE INTO runtime_cycles VALUES(?,?)', (key, canonical(value)))


def partner_state(root):
    """The Partner's main-stream token state, in the shape `regime` reads."""
    from . import streams, timestamps
    from .hook_log import threshold_for
    from .ids import PARTNER
    threshold = threshold_for(root, PARTNER)
    state, floor = {'threshold': threshold}, None
    for record in streams.iter_records(streams.main_stream(root, PARTNER)):
        if record.get('event') == 'seam':
            floor = None
        tokens = record.get('context_tokens')
        if type(tokens) is not int:
            continue
        floor = tokens if floor is None else floor
        stamp = timestamps.parse(str(record.get('ts', '')))
        state.update(tokens=tokens, floor=floor, cache_read=record.get('cache_read'),
                     cache_creation=record.get('cache_creation'), at=stamp.timestamp() if stamp else None)
    return state


def partner_regime(root, *, now=None):
    """The Partner's regime; its packet is PARTNER.md, the one file its seam re-reads.

    Its token state is the newest main-stream usage: every tool call's `log` record
    and every turn's `turn_end` record, written at Stop from the turn's last response.
    """
    from .ids import PARTNER
    from .companion import is_disabled
    path = Path(root) / 'PARTNER.md'
    if is_disabled(root, PARTNER) or not path.is_file():
        return HOLD
    now = time.time() if now is None else now
    return regime(partner_state(root), now=now, profile=seam_profile(root), packet_bytes=path.stat().st_size)


def partner_reset(root, *, env=None):
    """The Partner's idle-cold seam, taken by the loop while its pane waits on an empty prompt.

    Its turns are the human's, so the cache goes cold between them; the next turn
    rewrites the prefix regardless. A stream untouched for less than the cache
    lifetime is still warm, which the file's mtime answers without parsing it.
    """
    from . import goal, seam, streams
    from .hook_log import seam_marker
    from .ids import PARTNER
    root = Path(root)
    path = streams.main_stream(root, PARTNER)
    marker = seam_marker(root, PARTNER)
    if marker.exists() or not path.is_file() or not (root / 'PARTNER.md').is_file():
        return None
    now = time.time()
    if now - path.stat().st_mtime < seam_profile(root)['cache_ttl_s'] or partner_regime(root, now=now) != COLD_IDLE:
        return None
    pane = goal.capture_pane(seam.pane_target(PARTNER, env), env)
    if pane is None or not goal.input_is_empty(pane):
        return None
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(COLD_IDLE)
    return {'status': 'partner_seam', 'seam': COLD_IDLE, **seam.seam(root, PARTNER, env=env)}
