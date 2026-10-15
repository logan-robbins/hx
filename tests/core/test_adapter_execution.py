"""Actual native adapters preserve isolation and flags after packet admission."""
import json
from pathlib import Path
import pytest
from .test_native_controller import runtime, flavor, ready
from .test_native_launch import configured
from .test_context_packets import assignment
from .test_unit_execution import fleet
from .test_map_updates import active
from .test_appmap import mapped


@pytest.mark.parametrize('adapter', ['claude', 'codex', 'meta', 'grok', 'pi'])
def test_admitted_worker_executes_exact_adapter_flags_and_private_home(runtime, adapter):
    store, repo, run, env = runtime
    flavor(runtime, adapter)
    row = ready(runtime)
    record = json.loads((store.root/'run/eng-001/native-test.jsonl').read_text().splitlines()[0])
    argv = record['argv']
    home = Path(record['home'])
    assert home == Path(row['payload']['capsule'])/'run/eng-001/home'
    assert record['cwd'] == str(repo)
    assert 'fixture-key' not in ' '.join(argv)
    assert '-p' not in argv and '--print' not in argv
    if adapter == 'claude':
        assert '--dangerously-skip-permissions' in argv
        assert argv[argv.index('--setting-sources') + 1] == 'user'
        assert Path(argv[argv.index('--append-system-prompt-file') + 1]).is_file()
    elif adapter == 'codex':
        assert '--dangerously-bypass-approvals-and-sandbox' in argv
        assert '-a' not in argv
    elif adapter == 'meta':
        assert '--yolo' in argv and '--model' in argv and '-m' not in argv
    elif adapter == 'grok':
        assert '--minimal' in argv
        assert argv[argv.index('--permission-mode') + 1] == 'bypassPermissions'
        assert '--rules' in argv
    else:
        assert {'--no-approve','--no-extensions','--no-context-files','--no-skills'} <= set(argv)
        assert Path(argv[argv.index('--append-system-prompt') + 1]).is_file()
        actual = {argv[index+1] for index, arg in enumerate(argv) if arg == '--skill'}
        expected = {str(path) for path in (home/'skills').glob('*/SKILL.md')}
        assert actual == expected


def test_admitted_codex_can_use_existing_login_without_seed_token(runtime):
    store, _, _, _ = runtime
    flavor(runtime, 'codex')
    (store.root/'seed/codex-token').unlink()
    row = ready(runtime)
    auth = Path(row['payload']['capsule'])/'run/eng-001/home/auth.json'
    assert json.loads(auth.read_text()) == {'fixture': True}
