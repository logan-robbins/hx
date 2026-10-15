"""One restricted native transport shared by independent context and learning jobs."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from . import checks
from .continuity_store import Conflict, canonical, digest
from .errors import ValidationError
from .config_models import load_models

MAX_TURNS = 6
HOST_RESERVE = 4096


def runtime(root, config, *, purpose):
    """Freeze the selected binary and independent model configuration."""
    import json
    binary = Path(json.loads((root / 'config/claude.json').read_text())['bin']).resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValidationError('configured sidecar Claude executable is unavailable')
    info = binary.stat()
    options = config.learning if purpose == 'learning' else config.companion
    selected = options.get('model') or config.model
    models_path = root / 'config/models.json'
    model = load_models(models_path).get(selected)
    if model is None:
        raise ValidationError('independent sidecar model must have an explicit models.json context limit')
    output = model.max_output_tokens or min(8192, max(1, (model.window - model.threshold) // (MAX_TURNS + 1)))
    input_limit = min(model.threshold, model.window) - MAX_TURNS * output - min(HOST_RESERVE, model.window // 10)
    if input_limit <= 0:
        raise ValidationError('independent sidecar output reserve exhausts its model context window')
    return {'models_path': str(models_path.resolve()), 'model_snapshot': model.__dict__,
            'model_input_limit': input_limit, 'max_output_tokens': output, 'binary': str(binary), 'binary_stat': [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns],
            'model': selected, 'effort': options.get('effort') or config.effort}


def verify_runtime(payload):
    model = load_models(payload['models_path']).get(payload['model'])
    if model is None or model.__dict__ != payload['model_snapshot']:
        raise Conflict('independent sidecar model context limits changed after preparation')
    info = Path(payload['binary']).stat()
    if [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns] != payload['binary_stat']:
        raise Conflict('sidecar executable changed after preparation')


def preflight(root, payload, body, *, protocol, timeout_s=60):
    """Validate every deterministic input before a paid execution slot is claimed."""
    import importlib
    if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 60:
        raise ValidationError('sidecar execution deadline must be at most 60 seconds')
    verify_runtime(payload)
    schemas = importlib.import_module('hx.' + protocol).TOOLS
    size = len(payload['system'].encode()) + len(canonical(body).encode()) + len(canonical(schemas).encode())
    bound = min(payload.get('request_bytes', payload['model_input_limit']), payload['model_input_limit'])
    if size > bound or size + payload.get('evidence_budget', 0) > payload['model_input_limit']:
        raise ValidationError('complete sidecar request exceeds its independent model context budget')
    token = root / 'seed/token'
    info = token.stat()
    if info.st_mode & 0o77:
        raise ValidationError('sidecar credential must be private')
    if info.st_size > 8192:
        raise ValidationError('sidecar credential exceeds its supported bound')
    with token.open('rb') as source:
        raw = source.read(8193)
    if len(raw) > 8192:
        raise ValidationError('sidecar credential exceeds its supported bound')
    credential = raw.decode().strip()
    if not credential:
        raise ValidationError('sidecar credential is absent')
    return {'credential': credential}


def execute(root, job_id, payload, body, *, protocol, tools, on_spawn, env=None, timeout_s=60,
            output_bytes=65536, transport=None):
    transport = transport or preflight(root, payload, body, protocol=protocol, timeout_s=timeout_s)
    credential = transport['credential']
    child = {key: value for key, value in (os.environ if env is None else env).items()
             if key in {'PATH', 'TMPDIR', 'ANTHROPIC_BASE_URL'}}
    child.update(CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', CLAUDE_CODE_DISABLE_AUTO_MEMORY='1',
                 CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(payload['max_output_tokens']), DISABLE_AUTOUPDATER='1')
    child['ANTHROPIC_API_KEY' if credential.startswith('sk-ant-api') else 'CLAUDE_CODE_OAUTH_TOKEN'] = credential
    with tempfile.TemporaryDirectory(prefix='hx-sidecar-') as scratch:
        home = Path(scratch)
        child.update(HOME=scratch, CLAUDE_CONFIG_DIR=str(home / 'config'), XDG_CONFIG_HOME=str(home / 'xdg'))
        (home / 'config').mkdir()
        (home / 'config/settings.json').write_text(canonical({'disableAllHooks': True, 'autoMemoryEnabled': False,
            'permissions': {'defaultMode': 'dontAsk'}, 'enabledPlugins': {}}))
        # These are native input transports, consumed whole by the subprocess;
        # they are never exposed as partial filesystem tools to the model.
        (home / 'system.md').write_text(payload['system'])
        (home / 'pass.json').write_text(canonical(body))
        server = home / 'server.py'
        server.write_text('import sys\nsys.path.insert(0,' + repr(str(Path(__file__).resolve().parent.parent)) + ')\n'
            'from pathlib import Path\nfrom hx.' + protocol + ' import serve\nserve(Path(sys.argv[1]),sys.argv[2])\n')
        (home / 'mcp.json').write_text(canonical({'mcpServers': {'continuity': {'command': sys.executable,
            'args': [str(server), str(root.resolve()), job_id]}}}))
        argv = [payload['binary'], '--print', '--output-format', 'json', '--no-session-persistence',
            '--setting-sources', 'user', '--system-prompt-file', str(home / 'system.md'), '--tools', '',
            '--strict-mcp-config', '--mcp-config', str(home / 'mcp.json'), '--allowedTools',
            ','.join('mcp__continuity__' + name for name in tools), '--permission-mode', 'dontAsk',
            '--disable-slash-commands', '--no-chrome', '--max-turns', str(MAX_TURNS), '--model', payload['model'],
            '--effort', payload['effort']]
        with (home / 'pass.json').open('rb') as input_file:
            return checks.execute(argv, home, child, home / 'output.json', timeout_s, output_bytes,
                                  stdin=input_file, on_spawn=on_spawn)


def supersede_unpaid(store, table, job_id, prompt_version):
    """Retire stale prepared inputs without ever replaying attempted execution."""
    if table not in {'companion_jobs', 'learning_jobs'}:
        raise ValueError('unknown sidecar table')
    import json
    with store.transaction() as tx:
        current = tx.db.execute(f'SELECT * FROM {table} WHERE job_id=?', (job_id,)).fetchone()
        payload = json.loads(current['payload'])
        if current['status'] != 'prepared' or payload.get('attempts', 0) or payload['prompt_version'] == prompt_version:
            return False
        tx._change()
        tx.db.execute(f"UPDATE {table} SET status='superseded',request_key=? WHERE job_id=?",
                      (digest(['superseded-unpaid', current['request_key'], job_id]), job_id))
    return True
