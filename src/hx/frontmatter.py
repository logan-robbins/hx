"""The YAML-subset frontmatter parser shared by order files and work items.

hx is stdlib only (spec 08), so this parses exactly the subset the spec uses and rejects
everything else by name rather than guessing. Supported:

    ---
    id: eng-001
    pod: engineers
    after: [eng-000, eng-002]
    outcome:
    dispatched: 2026-09-20T12:00:00Z
    ---

plus block sequences (`- item` lines under a bare `key:`), single- and double-quoted
scalars, `null`/`~`, `true`/`false`, and integers. Anything else — nesting, flow mappings,
tabs, anchors, multi-line scalars — is rejected with the file, the line, and the rule.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ValidationError

FENCE = "---"


def _scalar(raw: str, path: str, lineno: int) -> object:
    text = raw.strip()
    if text == "" or text in ("null", "~"):
        return None
    if text in ("true", "false"):
        return text == "true"
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    if text.startswith("["):
        if not text.endswith("]"):
            raise ValidationError(
                f"{path}: frontmatter line {lineno}: unterminated flow sequence; "
                f"a list is written `[a, b]` on one line or as `- a` lines beneath the key"
            )
        inner = text[1:-1].strip()
        if inner == "":
            return []
        return [_scalar(part, path, lineno) for part in inner.split(",")]
    if text.startswith("{"):
        raise ValidationError(
            f"{path}: frontmatter line {lineno}: flow mappings are not supported in hx "
            f"frontmatter; use `key: value` lines"
        )
    try:
        return int(text)
    except ValueError:
        return text


def parse_frontmatter(text: str, path: str | Path) -> tuple[dict[str, object] | None, str]:
    """Split `text` into (frontmatter mapping or None, body).

    A document that does not open with a `---` line has no frontmatter; the whole text is
    the body. A document that opens with `---` and never closes it is an error.
    """
    path = str(path)
    lines = text.split("\n")
    if not lines or lines[0].strip() != FENCE:
        return None, text

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            end = i
            break
    if end is None:
        raise ValidationError(
            f"{path}: frontmatter opened with `---` on line 1 but never closed; "
            f"a closing `---` line is required"
        )

    data: dict[str, object] = {}
    current_list_key: str | None = None
    for offset, line in enumerate(lines[1:end], start=2):
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            raise ValidationError(
                f"{path}: frontmatter line {offset}: tab character; frontmatter is spaces only"
            )
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValidationError(
                    f"{path}: frontmatter line {offset}: sequence item `{stripped}` with no key above it"
                )
            if data[current_list_key] is None:
                data[current_list_key] = []
            seq = data[current_list_key]
            if not isinstance(seq, list):
                raise ValidationError(
                    f"{path}: frontmatter line {offset}: sequence item under `{current_list_key}`, "
                    f"which already has the scalar value `{seq}`"
                )
            seq.append(_scalar(stripped[2:], path, offset))
            continue
        if line[:1] == " ":
            raise ValidationError(
                f"{path}: frontmatter line {offset}: indented line `{stripped}`; hx frontmatter "
                f"is one level of `key: value` with optional `- item` sequences"
            )
        if ":" not in stripped:
            raise ValidationError(
                f"{path}: frontmatter line {offset}: `{stripped}` is not `key: value`"
            )
        key, _, raw = stripped.partition(":")
        key = key.strip()
        if key == "":
            raise ValidationError(f"{path}: frontmatter line {offset}: empty key")
        if key in data:
            raise ValidationError(
                f"{path}: frontmatter line {offset}: duplicate key `{key}`"
            )
        if raw.strip() == "":
            # Either an explicit null or the head of a block sequence; the next line decides.
            data[key] = None
            current_list_key = key
            continue
        data[key] = _scalar(raw, path, offset)
        current_list_key = None

    body = "\n".join(lines[end + 1 :])
    return data, body


def frontmatter_list(
    data: dict[str, object], key: str, path: str | Path, *, default: list | None = None
) -> list:
    """Read `key` as a list of strings, accepting an absent key and an explicit null."""
    if key not in data or data[key] is None:
        return [] if default is None else list(default)
    value = data[key]
    if not isinstance(value, list):
        raise ValidationError(
            f"{path}: frontmatter `{key}` must be a list such as `[eng-001, eng-002]`, "
            f"got {type(value).__name__} `{value}`"
        )
    for item in value:
        if not isinstance(item, str) or item == "":
            raise ValidationError(
                f"{path}: frontmatter `{key}` entries must be non-empty strings, got `{item!r}`"
            )
    return list(value)
