"""Shipped examples are packet projections without old assignment contracts."""
import json
from .conftest import FIXTURES


def test_examples_use_only_current_projection():
    for file in FIXTURES.glob('*.json'):
        document=json.loads(file.read_text())
        assert document['tasks'][0]['payload']['dependency_packet'] is True
        assert 'items' not in document and 'goals' not in document
        assert 'work_item' not in document and 'memory' not in document
    assert not (FIXTURES/'archive.json').exists()
