"""Packaged instructions and templates describe the single packet product."""
import json
from pathlib import Path
import pytest
from hx import skill_manifest
from hx.config_harness import validate_harness
from hx.install import EXPECTED_SKELETON_FILES, skeleton_dir
SKILLS=skill_manifest.package_dir()


def test_all_required_package_inputs_exist():
    assert all((skeleton_dir()/name).is_file() for name in EXPECTED_SKELETON_FILES)
    for old in ('templates/work-item.md','templates/goal.md','templates/addendum.md'):
        assert not (skeleton_dir()/old).exists()
    assert not (SKILLS/'hx-memory').exists()


def test_portable_skill_manifest_and_worker_command_inventory():
    problems=[detail for status,detail in skill_manifest.check(SKILLS) if status!='ok']
    assert not problems,problems
    manifest=skill_manifest.load(SKILLS)
    assert 'hx-worker' in skill_manifest.select(manifest,audience='worker',role='backend-engineer')
    assert 'hx-signoff' in skill_manifest.select(manifest,audience='worker',role='qa-engineer')
    assert 'hx-signoff' not in skill_manifest.select(manifest,audience='worker',role='backend-engineer')
    assert 'hx-setup' not in skill_manifest.select(manifest,audience='worker',role='backend-engineer')
    assert 'hx-conversion' not in skill_manifest.select(manifest,audience='worker',role='backend-engineer')
    assert 'hx-conversion' in skill_manifest.select(manifest,audience='worker',role='backend-engineer',enabled=['hx-conversion'])


@pytest.mark.parametrize('kind',['worker','contributor'])
def test_worker_templates_remain_valid_current_role_configuration(kind):
    text=(skeleton_dir()/'templates'/kind/'harness.json').read_text().replace('{{id}}','eng-001').replace('{{pod}}','engineers')
    config=validate_harness(json.loads(text),'config/eng-001/harness.json',dir_name='eng-001')
    assert (skeleton_dir()/'personas'/f'{config.role}.md').is_file()
    assert (skeleton_dir()/'companion'/'roles'/f'{config.role}.md').is_file()


def test_every_role_has_persona_and_companion_policy():
    roles={p.stem for p in (skeleton_dir()/'personas').glob('*.md')}
    assert roles=={p.stem for p in (skeleton_dir()/'companion'/'roles').glob('*.md')}


def test_only_partner_is_preconfigured():
    configs=[p.parent.name for p in (skeleton_dir()/'config').glob('*/harness.json')]
    assert configs==['partner']


@pytest.mark.parametrize('relative', [
    'config/CLAUDE.md', 'personas/partner.md',
    *[f'personas/{role}.md' for role in ('backend-engineer','frontend-engineer','release-engineer','qa-engineer')],
    'templates/worker/SUBAGENTS.md', 'templates/contributor/SUBAGENTS.md',
])
def test_shipped_execution_policy_requires_dependency_search_reuse_and_useful_parallelism(relative):
    text=(skeleton_dir()/relative).read_text().lower()
    for obligation in ('graph','search','reuse','parallel'):
        assert obligation in text,(relative,obligation)
    assert 'task-size routes' in text or 'packet count' in text or relative.startswith('templates/')


def test_partner_children_explore_dependencies_without_claiming_worker_ownership():
    text=(skeleton_dir()/'config/partner/SUBAGENTS.md').read_text()
    assert 'Partner orchestrates and has no worker assignment' in text
    assert 'does not reserve a' in text and 'worker run' in text
    assert 'Do not implement product changes or mutate plan/run state' in text
    for obligation in ('graph','reuse','overlap','source paths/lines'):
        assert obligation in text


def test_packaged_instructions_cannot_reintroduce_removed_task_authorities():
    removed=('hx dispatch','hx resume','hx amend','hx bench','hx memory','hx recall','tasks.json','simple path')
    paths=[*SKILLS.glob('*/SKILL.md'),*(skeleton_dir()/'personas').glob('*.md'),skeleton_dir()/'config/CLAUDE.md']
    for path in paths:
        text=path.read_text()
        assert not [name for name in removed if name in text],path


def test_partner_skill_requires_complete_automatic_boundary_delivery_without_rereads():
    text=(SKILLS/'hx-partner/SKILL.md').read_text()
    assert 'automatically delivers your complete' in text
    assert 'no additional file read is required' in text
    assert 'without offsets' in text and 'or limits' in text
    assert 'Use the Read tool once on' not in text


def test_worker_mechanics_require_exact_independent_binding_and_typed_blockers():
    text=(SKILLS/'hx-worker/SKILL.md').read_text()
    for command in ('hx plan bind','hx plan binding','hx plan approve-binding','hx progress','hx plan audit','hx complete done'):
        assert command in text
    assert 'exact frozen hash' in text and 'cannot approve yourself' in text
    assert 'Blockers retain ownership' in text and 'there is no blocked completion' in text
    assert 'request is not completion' in ' '.join((SKILLS/'hx-signoff/SKILL.md').read_text().split())


def test_partner_observation_transport_quotes_current_pointer_without_assigning_a_worker():
    from hx.companion import POINTER
    companion=(SKILLS/'hx-companion/SKILL.md').read_text()
    contracts=(Path(__file__).resolve().parents[2]/'CONTRACTS.md').read_text()
    assert POINTER.format(path='<path>') in companion and POINTER.format(path='<path>') in contracts
    assert 'cannot' in companion and 'create a worker assignment' in companion
