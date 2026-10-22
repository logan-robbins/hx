"""Mask the exact credentials hx loaded before anything it captures is stored or sent.

Workers run with full permissions, so a captured tool result (an `env` dump, a verbose client
log) can carry the literal values hx itself exported or read (issue #5): `seed/token`, which
`adapters/claude/start.sh` exports as ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN; the provider
keys of `envfile.SEED_KEYS`, from the process environment, the instance env file
(`config/auth.json` `env_file`) and the `seed/` files; and the Jev key `jev.credential`
resolves. This module loads those values, from those named keys only, and replaces every exact
occurrence with a run of `*` of the same length. Equality, never a pattern (fleet plan §4.3):
no false positives, and a bare value that no key pattern could name is caught too.

Each value is also masked in its three base64 alignments, standard and URL-safe alphabets, so
`base64`, `Authorization: Basic` and similar renderings do not pass; the one or two characters
at either end of such a rendering that also encode a neighbouring byte survive, carrying at
most a nibble of the value's first and last byte. Values shorter than MIN_LENGTH are not
masked: a `1`, a `true` or a hostname in a credential variable would otherwise star every
text, and no credential hx loads is that short.

The set is loaded once per instance root per process (`for_root`): a rotation on disk is seen
by the next process, and a process that captured an event before the rotation re-captures it
identically (`append_event` is idempotent by payload hash). Masking keeps length, so sizes,
offsets, chunk hashes and container formats (a `git archive` tar installed as an artifact)
stay valid.

Sinks: ledger events (`continuity_store.append_event`), observations and artifacts
(`observer.encode_observation`, `put_artifact`, `put_artifact_file`), captured check and tool
output as it leaves the pipe (`checks.execute`), hook streams (`streams.append_record`), every
Jev request (`jev.encode`) and fetched content (`content_store.redact`). Not covered: the
worker's own transcript and pane log hold what it printed, and hx-authored ledger rows other
than events (task payloads, recipes, receipts, records) are written as given.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Iterable

from . import envfile
from .errors import HxError

#: A value in a credential variable shorter than this is not a credential hx masks.
MIN_LENGTH = 16
FILL = "*"
#: The credential variables hx itself reads or exports (`start.sh`, `load-env.sh`,
#: `envfile.sync_seed`, `jev.credential`). Other env-file variables reach agents but are not
#: credentials hx knows.
NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
         *dict.fromkeys(name for names in envfile.SEED_KEYS.values() for name in names),
         "TYPESAFE_API_KEY", "JEV_API_KEY")
#: The `seed/` files `hx install` and `envfile.sync_seed` write, `token` first.
SEED_FILES = ("token", *envfile.SEED_KEYS)


def _depends(index: int) -> tuple[int, ...]:
    """The input byte offsets one base64 output character encodes."""
    group, position = divmod(index, 4)
    first = 3 * group
    return ((first,), (first, first + 1), (first + 1, first + 2), (first + 2,))[position]


def renderings(value: str) -> list[str]:
    """`value` and, for each of the three byte alignments and both alphabets, the part of its
    base64 encoding that depends on its own bytes alone; longest first."""
    raw = value.encode("utf-8")
    forms = {value}
    for lead in range(3):
        stream = b"\0" * lead + raw
        stream += b"\0" * (-len(stream) % 3)
        encoded = base64.b64encode(stream).decode("ascii")
        first, last = lead, lead + len(raw) - 1
        stable = [index for index in range(len(encoded))
                  if all(first <= byte <= last for byte in _depends(index))]
        if stable:
            form = encoded[stable[0]:stable[-1] + 1]
            forms.add(form)
            forms.add(form.replace("+", "-").replace("/", "_"))
    return sorted(forms, key=lambda form: (-len(form), form))


class Mask:
    """The exact values to mask, in text and in bytes, longest form first so a value that
    contains another is never half-starred."""

    def __init__(self, values: Iterable[str]) -> None:
        kept = {value for value in values if isinstance(value, str) and len(value) >= MIN_LENGTH}
        forms = sorted({form for value in kept for form in renderings(value)},
                       key=lambda form: (-len(form), form))
        self.pairs = tuple((form, FILL * len(form)) for form in forms)
        self.byte_pairs = tuple((form.encode("utf-8"), FILL.encode("ascii") * len(form.encode("utf-8")))
                                for form in forms)
        #: Bytes a chunked writer must hold back so a value split across two chunks is seen whole.
        self.carry = max((len(form) for form, _ in self.byte_pairs), default=1) - 1

    def __bool__(self) -> bool:
        return bool(self.pairs)

    def apply(self, value):
        """`value` with every occurrence starred: strings, and the strings inside dicts (keys
        too), lists and tuples; anything else as given."""
        if not self.pairs:
            return value
        if isinstance(value, str):
            for form, fill in self.pairs:
                value = value.replace(form, fill)
            return value
        if isinstance(value, dict):
            return {self.apply(key): self.apply(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.apply(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.apply(item) for item in value)
        return value

    def apply_bytes(self, data: bytes) -> bytes:
        for form, fill in self.byte_pairs:
            data = data.replace(form, fill)
        return data

    def stream(self) -> Stream:
        return Stream(self)


class Stream:
    """Mask bytes fed in chunks of any size. Each `feed` returns what is safe to write; the last
    `carry` bytes wait for the next chunk, and `finish` releases them."""

    def __init__(self, mask: Mask) -> None:
        self._mask = mask
        self._held = b""

    def feed(self, chunk: bytes) -> bytes:
        if not self._mask:
            return chunk
        data = self._mask.apply_bytes(self._held + chunk)
        carry = self._mask.carry
        if carry:
            self._held, data = data[-carry:], data[:-carry]
        return data

    def finish(self) -> bytes:
        held, self._held = self._held, b""
        return held


# --- loading ---------------------------------------------------------------------------------------


def _env_file(root: Path) -> list[str | None]:
    try:
        path = envfile.settings(root).get("env_file")
        values = envfile.parse(Path(path)) if path else {}
    except HxError:
        return []
    return [values.get(name) for name in NAMES]


def _seed(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() if path.is_file() else None
    except (OSError, UnicodeDecodeError):
        return None


def _jev(root: Path, env) -> str | None:
    from . import jev

    try:
        return jev.credential(root, env)
    except jev.Unavailable:
        return None


def load(root, env=None) -> Mask:
    """Every value the named credential keys hold for this instance, read now: the environment,
    the instance env file, the `seed/` files and the Jev key, exactly as hx itself loads them."""
    env = os.environ if env is None else env
    root = Path(root)
    values = [env.get(name) for name in NAMES]
    values += _env_file(root)
    values += [_seed(root / "seed" / name) for name in SEED_FILES]
    values.append(_jev(root, env))
    return Mask(value for value in values if value)


_loaded: dict[str, Mask] = {}


def for_root(root) -> Mask:
    """The instance's mask, loaded once per process from the process environment and `root`."""
    key = os.fspath(root)
    mask = _loaded.get(key)
    if mask is None:
        mask = _loaded[key] = load(root)
    return mask


def reset() -> None:
    """Forget every loaded mask, so the next `for_root` reads afresh as a new process would."""
    _loaded.clear()
