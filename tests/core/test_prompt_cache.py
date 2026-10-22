"""Prompt caching: know the limit, keep invariants cached, restructure when cold."""
from __future__ import annotations

import json
from pathlib import Path
from hx.errors import ValidationError

import pytest

from hx import prompt_cache


def test_ttl_parsing():
    assert prompt_cache.ttl_seconds("1h") == 3600
    assert prompt_cache.ttl_seconds("5m") == 300
    assert prompt_cache.ttl_seconds(None) == 300
    assert prompt_cache.ttl_seconds(600) == 600


def test_warm_cache_keeps_prefix():
    result = prompt_cache.assess(prefix_bytes=50_000, age_s=60, ttl="5m")
    assert result["state"] == "warm"
    assert result["recommendation"] == "keep-prefix"


def test_cold_cache_restructures_freely():
    result = prompt_cache.assess(prefix_bytes=50_000, age_s=4000, ttl="1h")
    assert result["state"] == "cold"
    assert result["recommendation"] == "restructure-free"


def test_tiny_prefix_has_no_cache_benefit():
    result = prompt_cache.assess(prefix_bytes=500, age_s=10, ttl="5m")
    assert result["cacheable"] is False
    assert result["recommendation"] == "restructure-free"


def test_expiring_cache_defers_small_changes():
    result = prompt_cache.assess(prefix_bytes=50_000, age_s=1700, ttl="30m")
    assert result["state"] == "expiring"
    assert result["recommendation"] == "defer-to-expiry"


def test_invariant_budget_leaves_working_room():
    budget = prompt_cache.invariant_budget(window=1_000_000, threshold=200_000)
    assert budget["invariant_ceiling_tokens"] <= 200_000
    assert budget["working_room_tokens"] >= 800_000


# Worker reports use frozen native inputs; provider residency stays unmeasured.
from hx import native_launch
from .test_native_launch import configured
from .test_context_packets import assignment
from .test_map_updates import active
from .test_appmap import mapped
from .test_unit_execution import fleet


@pytest.fixture
def cached(configured):
    store,repo,run=configured
    launch=native_launch.prepare(store,'eng-001',run,'cache-report',env={})
    return store,repo,run,launch


def test_worker_report_counts_frozen_channels_and_exact_checkpoint(cached):
    store,repo,run,launch=cached
    row=prompt_cache.agent_report(store.root,'eng-001')
    manifest=json.loads(Path(launch['payload']['manifest']).read_text())
    assert row['kind']=='packet' and row['launch']['launch_id']==launch['launch_id']
    assert row['run']['run_id']==run
    assert row['invariant']['prompt_version']==launch['payload']['prompt_version']
    assert row['invariant']['prefix_bytes']==sum(channel['bytes'] for channel in manifest['delivery'].values())
    assert row['checkpoint']['checkpoint_id']==launch['payload']['checkpoint_id']
    assert row['checkpoint']['packet_hash']==launch['payload']['packet_hash']
    assert row['checkpoint']['size']>0 and row['checkpoint']['charged_tokens']>0
    assert row['tools']['launch_selection']==launch['payload']['tool_selection']
    assert row['budget']['model']=='claude-opus-5-5'
    assert row['budget']['within'] is None
    assert row['cache']['state']=='unmeasured'
    assert row['context_usage']=='unmeasured'
    assert row['invariant']['native_tool_schemas']=='unmeasured'


def test_worker_report_does_not_rebuild_from_root_persona_or_file_streams(cached):
    store,repo,run,launch=cached
    before=prompt_cache.agent_report(store.root,'eng-001')
    (store.root/'personas/backend-engineer.md').write_text('changed current instructions')
    (store.root/'config/CLAUDE.md').write_text('changed current invariants')
    old=store.root/'run/eng-001/home/skills/obsolete'
    old.mkdir(parents=True)
    (old/'SKILL.md').write_text('injected stale skill')
    logs=store.root/'logs/eng-001';logs.mkdir(parents=True,exist_ok=True)
    (logs/'eng-001-main.jsonl').write_text('{"event":"seam","ts":"2026-10-15T12:00:00Z"}\n')
    after=prompt_cache.agent_report(store.root,'eng-001')
    assert after==before


def test_worker_report_preserves_scoped_captured_usage_without_guessing_residency(cached):
    store,repo,run,launch=cached
    usage={'input_tokens':100,'cache_read_input_tokens':80,'output_tokens':20}
    with store.transaction() as tx:
        tx.db.execute('INSERT INTO native_bindings VALUES(?,?,?,?,?,?,?)',
            ('usage-binding',run,'main','claude','session','claude',json.dumps(usage)))
        tx.db.execute('INSERT INTO runtime_cycles VALUES(?,?)',('tokens:'+launch['launch_id'],json.dumps({'tokens':180,'reset':'none'})))
    result=prompt_cache.agent_report(store.root,'eng-001')
    assert result['provider_usage'][0]['latest_usage']==usage
    assert result['context_usage']['tokens']==180
    assert result['cache']['state']=='unmeasured'


def test_tampered_frozen_input_is_reported_instead_of_falling_back(cached):
    store,repo,run,launch=cached
    from pathlib import Path
    (Path(launch['payload']['capsule'])/'config/models.json').write_text('{}')
    with pytest.raises(ValidationError,match='altered'):
        prompt_cache.agent_report(store.root,'eng-001')


def test_unassigned_worker_has_no_file_prefix_fallback(instance):
    result=prompt_cache.agent_report(instance,'eng-001')
    assert result['status']=='unassigned'
    assert result['invariant']['prefix_bytes'] is None
    assert result['budget']['model'] is None


def test_partner_orchestration_report_and_cli_remain_available(instance,capsys):
    result=prompt_cache.report(instance,'partner')
    assert result['agents'][0]['kind']=='partner'
    assert result['agents'][0]['invariant']['identity_bytes']>0
    assert prompt_cache.main(['report','partner','--json'],instance)==0
    assert json.loads(capsys.readouterr().out)['agents'][0]['kind']=='partner'
    assert prompt_cache.main(['report','eng-001'],instance)==0
    assert 'unmeasured' in capsys.readouterr().out


def test_cache_report_help_and_unknown_identity(instance,capsys):
    from hx.errors import NotFound
    with pytest.raises(NotFound): prompt_cache.report(instance,'eng-404')
    with pytest.raises(SystemExit): prompt_cache.main(['report','--help'],None)
    assert 'example' in capsys.readouterr().out


def test_required_full_text_is_counted_separately_and_never_reported_as_read(configured):
    store,repo,run=configured
    (store.root/'personas/backend-engineer.md').write_text('# Backend engineer\n\n## Domain judgment\n'+('Preserve the declared provider contract.\n'*700))
    row=native_launch.prepare(store,'eng-001',run,'referenced-cache-report',env={})
    result=prompt_cache.agent_report(store.root,'eng-001')
    invariant=result['invariant']
    assert invariant['inline_bytes']==invariant['prefix_bytes']
    assert invariant['backing_material_bytes']>20000
    assert invariant['inline_bytes']>=invariant['backing_material_bytes']>20000
    assert invariant['referenced_full_text_bytes']==0
    assert invariant['required_files'][0]['integrity']=='verified'
    assert invariant['required_files'][0]['read_status']=='not_required_inline'
    assert invariant['reference_read_status']=='not_required_inline'
    assert result['cache']['state']=='unmeasured'
