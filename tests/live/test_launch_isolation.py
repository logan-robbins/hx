"""Prove admitted native launch configuration with a real private tmux and fake CLI.

These tests inspect installed settings, the actual executable argv and capsule contents.
They do not claim a paid provider's implementation obeyed a flag, and never invoke one.
Native compaction/reset correctness is covered in core/test_compaction_policy.py and
core/test_token_controls.py; graph execution and corrections are in tests/scenario.
"""
import json
from pathlib import Path
import pytest
from hx import native_launch
from core.test_appmap import mapped as mapped_fixture, git
from core.test_map_updates import active
from core.test_unit_execution import fleet
from core.test_context_packets import assignment
from core.test_native_launch import configured
from core.test_native_controller import runtime, ready

TRIPWIRE='PROJECT-INSTRUCTIONS-MUST-NOT-LOAD'


@pytest.fixture
def mapped(tmp_path):
    generator=mapped_fixture.__wrapped__(tmp_path)
    value=next(generator)
    repo=value[1]
    (repo/'.claude').mkdir()
    (repo/'.claude/settings.json').write_text(json.dumps({'permissions':{'defaultMode':'plan'},'marker':TRIPWIRE}))
    (repo/'CLAUDE.md').write_text(TRIPWIRE+'\n')
    (repo/'.claude/skills/project-only').mkdir(parents=True)
    (repo/'.claude/skills/project-only/SKILL.md').write_text(TRIPWIRE)
    git(repo,'add','.')
    git(repo,'commit','-qm','Instruction isolation fixture')
    try:
        yield value
    finally:
        try: next(generator)
        except StopIteration: pass


def observations(runtime):
    store,repo,run,env=runtime
    return [json.loads(line) for line in (store.root/'run/eng-001/native-test.jsonl').read_text().splitlines()]


def test_admitted_worker_uses_frozen_capsule_and_user_settings_only(runtime):
    store,repo,run,env=runtime
    row=ready(runtime)
    actual=observations(runtime)[0]
    assert actual['cwd']==str(repo)
    assert actual['home'].startswith(row['payload']['capsule'])
    assert actual['argv'][actual['argv'].index('--setting-sources')+1]=='user'
    assert '--dangerously-skip-permissions' in actual['argv']
    home=Path(actual['home'])
    settings=json.loads((home/'settings.json').read_text())
    assert settings['claudeMdExcludes']
    assert 'project-only' not in str(list(home.rglob('SKILL.md')))
    assert TRIPWIRE not in (home/'CLAUDE.md').read_text()
    assert not (store.root/'tasks.json').exists()
    assert not (store.root/'pods').exists()


def test_worker_hx_path_and_hooks_bind_the_same_admitted_run(runtime):
    store,repo,run,env=runtime
    binary=Path(json.loads((store.root/'config/claude.json').read_text())['bin'])
    binary.write_text(binary.read_text().replace("'home': str(home)","'home': str(home), 'hx': __import__('shutil').which('hx')"))
    row=ready(runtime)
    actual=observations(runtime)[0]
    assert actual['hx']==str(Path(row['payload']['capsule'])/'bin/hx')
    settings=json.loads((Path(actual['home'])/'settings.json').read_text())
    commands=[hook['command'] for entries in settings['hooks'].values() for entry in entries for hook in entry['hooks']]
    assert all('--continuity-run '+run in command for command in commands)
    assert all(row['launch_id'] in command for command in commands)


def test_prior_worker_home_is_not_a_native_launch_input(runtime):
    store,repo,run,env=runtime
    old=store.root/'run/eng-001/home'
    old.mkdir(parents=True,exist_ok=True)
    (old/'CLAUDE.md').write_text('OBSOLETE-ROOT-HOME')
    (old/'history.jsonl').write_text('OBSOLETE-WORKER-HISTORY')
    row=ready(runtime)
    home=Path(row['payload']['capsule'])/'run/eng-001/home'
    assert not (home/'history.jsonl').exists()
    assert 'OBSOLETE-ROOT-HOME' not in (home/'CLAUDE.md').read_text()
