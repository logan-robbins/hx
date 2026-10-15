"""Bounded reviewed-rule publication; refused growth preserves the pending proposal."""
from . import store
from .errors import ValidationError
from .file_limits import validate_bytes


def write_category(path, text):
    try:
        validate_bytes(text.encode('utf-8'), path=path)
    except ValidationError as exc:
        raise ValidationError(
            f'Reviewed learning category cannot be published: {exc} '
            'The proposal remains pending and the existing rules are unchanged; '
            'merge duplicate rules or shorten reviewed wording before accepting.'
        ) from exc
    store.atomic_write_text(path, text)
