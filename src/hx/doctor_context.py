"""Report complete context overflow; automatic transport fragments are not loss."""
from __future__ import annotations

from .doctor_homes import WARN
from .errors import HxError
from .ids import ID_RE


def checks(root, env=None):
    from . import partner_context, persona
    from .token_controls import limits
    over = []
    for harness in sorted((root / 'config').glob('*/harness.json')):
        agent = harness.parent.name
        if not ID_RE.match(agent):
            continue
        try:
            body = persona.boundary(root, agent, env=env)
            if agent == 'partner':
                partner_context.validate(root, body['text'])
            else:
                budget = limits(root, agent)['threshold']
                if body['bytes'] > budget:
                    over.append(f'{agent}: complete instructions use {body["bytes"]} UTF-8 bytes; native context budget is {budget} bytes')
        except (HxError, OSError, ValueError) as exc:
            over.append(f'{agent}: complete context preflight failed: {exc}')
    if not over:
        return []
    return [(WARN, 'hook-size', '; '.join(over) + '; repair the complete source or configured delivery budget before launching')]
