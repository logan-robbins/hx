"""Current identity/channel selection and bounded context, without migration paths."""
import json
import pytest
from hx import instruction_materials, persona, learn
from hx.errors import ValidationError, NotFound


def configure(root, flavor='claude', role='custom-engineer', **extra):
    path=root/'config'/'eng-001'/'harness.json'
    body=json.loads(path.read_text())
    body.update(flavor=flavor,role=role,purpose='Own the requested packet result.',**extra)
    path.write_text(json.dumps(body))
    (root/'personas'/f'{role}.md').write_text('# Custom role\n\nPreserve the declared contract.\n')


def test_identity_uses_custom_role_and_purpose(instance):
    configure(instance)
    values=persona.identity_values(instance,'eng-001')
    assert values['id']=='eng-001' and values['role']=='custom-engineer'
    assert persona.identity_line(instance,'eng-001')=='You are eng-001 in pod engineers. Own the requested packet result.'


def test_explorer_model_and_effort_are_explicit(instance):
    configure(instance,explorer={'model':'claude-sonnet-5','effort':'high'})
    assert persona.identity_line(instance,'eng-001').endswith('Your explorer runs on claude-sonnet-5 at high.')


@pytest.mark.parametrize('flavor,invariants,role_persona',[
    ('claude',False,True),('pi',True,False),('grok',True,False),('meta',True,True),('codex',False,False)])
def test_each_source_has_one_native_channel(instance,flavor,invariants,role_persona):
    configure(instance,flavor)
    text=persona.boundary(instance,'eng-001')['text']
    clauses=(instance/'config'/'CLAUDE.md').read_text().strip()
    assert (clauses in text) is invariants
    assert text.count(clauses)<=1
    assert ('Preserve the declared contract.' in text) is role_persona
    assert 'Own the requested packet result.' in text


def test_template_identity_is_resolved_and_unknown_values_are_rejected():
    assert persona.expand_identity('Worker {{id}} owns {{workdir}}',{'id':'be-001','workdir':'/repo'})=='Worker be-001 owns /repo'
    with pytest.raises(ValidationError,match='unresolved'):
        persona.expand_identity('{{unknown}}',{})


def test_custom_subagent_instructions_are_preserved(instance):
    configure(instance)
    own=instance/'config'/'eng-001'/'SUBAGENTS.md'
    own.write_text('Read the packet and return bounded evidence.')
    source,text=persona.render_subagents(instance,'eng-001')
    assert source=='config/eng-001/SUBAGENTS.md'
    assert text==own.read_text()


def test_shared_subagent_instructions_expand_current_identity(instance):
    configure(instance)
    (instance/'config'/'eng-001'/'SUBAGENTS.md').unlink(missing_ok=True)
    source,text=persona.render_subagents(instance,'eng-001')
    assert source.startswith('templates/')
    assert '{{id}}' not in text and '{{workdir}}' not in text


def test_large_persona_stays_complete_in_one_native_input(instance,monkeypatch):
    configure(instance)
    (instance/'personas'/'custom-engineer.md').write_text('bounded context '+('source '*1000))
    monkeypatch.setitem(persona.HOOK_BYTES_BY_FLAVOR,'claude',1000)
    result=persona.boundary(instance,'eng-001')
    assert result['bytes']>result['bound']
    assert result['as_files']==[]
    assert 'bounded context '+('source '*1000) in result['text']
    assert 'Required complete persona' not in result['text']
    assert 'bounded context' in instruction_materials.read_bundle(result['required_files']).decode()
    assert 'Own the requested packet result.' in result['text']


def test_partner_uses_orchestration_notes_and_requires_persona(instance):
    result=persona.boundary(instance,'partner')
    assert result['file']==instance/'PARTNER.md'
    (instance/'personas'/'partner.md').unlink()
    with pytest.raises(NotFound): persona.boundary(instance,'partner')


def test_cli_prints_current_boundary(instance,run_hx):
    configure(instance)
    result=run_hx('persona','eng-001','--root',str(instance))
    assert result.returncode==0,result.stderr
    assert result.stdout==persona.boundary(instance,'eng-001')['text']
