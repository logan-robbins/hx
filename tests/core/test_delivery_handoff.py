"""Scoped evidence handoffs preserve pending boundaries and immutable proof."""

import json
from argparse import Namespace

from hx import change_delivery, delivery_handoff, handoff
from .test_change_delivery import covered
from .test_unit_execution import fleet
from .test_map_updates import active
from .test_appmap import mapped


def test_pending_handoff_does_not_claim_integration_or_deployment(fleet, tmp_path):
    store, _, body, _ = covered(fleet, tmp_path)
    change_delivery.submit(store, body)
    directory = tmp_path / 'handoff'
    captured = delivery_handoff.capture(store, directory, body['id'])
    states = captured['boundaries']
    assert states['coverage']['covered'] and not states['review']['approved']
    assert states['candidate'] is None
    assert 'Integration and pre-deploy validation: unverified' in (directory / 'HANDOFF.md').read_text()
    assert delivery_handoff.check(store, directory, live=True)['valid']
    change_delivery.review(store, body['id'], decision='approve', expected_revision=1, reason='Reviewed')
    assert delivery_handoff.check(store, directory)['valid']  # historical evidence keeps its scope
    assert not delivery_handoff.check(store, directory, live=True)['valid']


def test_resealing_cannot_change_authoritative_captured_boundaries(fleet, tmp_path):
    store, _, body, _ = covered(fleet, tmp_path)
    change_delivery.submit(store, body)
    directory = tmp_path / 'handoff'
    delivery_handoff.capture(store, directory, body['id'])
    wrapper = json.loads((directory / delivery_handoff.MANIFEST).read_text())
    wrapper['capture']['boundaries']['review']['approved'] = True
    (directory / delivery_handoff.MANIFEST).write_text(json.dumps(wrapper))
    (directory / 'HANDOFF.md').write_text(delivery_handoff.render(wrapper['capture']))
    handoff.cmd_sums(Namespace(dir=str(directory)))
    result = delivery_handoff.check(store, directory)
    assert not result['valid'] and 'immutable artifact' in result['reasons'][0]
    store.collect_artifacts()
    assert store.db.execute("SELECT count(*) FROM artifact_refs WHERE owner_type='delivery_handoff'").fetchone()[0] == 1


def test_delivery_handoff_reachable_through_main(fleet, tmp_path, capsys):
    store, _, body, _ = covered(fleet, tmp_path)
    change_delivery.submit(store, body)
    directory = tmp_path / 'handoff'
    assert handoff.main(['delivery', str(directory), '--change', body['id']], root=store.root) == 0
    assert handoff.main(['check', str(directory), '--live'], root=store.root) == 0
    assert '"valid": true' in capsys.readouterr().out


def test_deploy_handoff_follows_verified_checkpoint_membership(fleet, tmp_path):
    from .test_delivery_validation import landed
    from hx import delivery_validation
    store, _, body, bare, landing = landed(fleet, tmp_path)
    integration = delivery_validation.candidate(store, bare, landing['head'])
    delivery_validation.run_validation(store, integration['batch'], tmp_path / 'integration')
    deploy = delivery_validation.candidate(store, bare, landing['head'], boundary='deploy')
    result = delivery_validation.run_validation(store, deploy['batch'], tmp_path / 'deploy')
    assert result['deployable'] and result['checkpoint']
    directory = tmp_path / 'handoff'
    capture = delivery_handoff.capture(store, directory, body['id'], deploy['batch'])
    assert capture['boundaries']['candidate']['checkpoint']['batch'] == integration['batch']
    assert delivery_handoff.check(store, directory, live=True)['valid']
