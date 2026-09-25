"""Typed continuation facts and lossless, compact display grammar.

The companion fills fields; it does not rewrite a prose memory document.
Rendering never summarizes, truncates, or strips qualifiers from those fields.
Source hashes/evidence remain on the ledger record, addressable by its ID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .errors import ValidationError

FACT_SCHEMA_VERSION = 1

# Required and optional text fields. Lists are checked separately below.
FIELDS = {
    "goal": (("text",), ()),
    "constraint": (("text",), ("when",)),
    "decision": (("choice", "because"), ("when",)),
    "finding": (("text",), ("path", "symbol")),
    "search": (("query", "cwd", "result"), ()),
    "command": (("command", "cwd", "purpose"), ("when", "result")),
    "cursor": (("step", "phase", "next"), ("last", "blocker")),
    "dead_end": (("approach", "failure", "retry_when"), ()),
}
LIST_FIELDS = {"search": {"scope", "flags"}, "command": {"env"}, "cursor": {"children"}}


def validate_payload(kind: str, payload: dict) -> dict:
    if kind not in FIELDS:
        raise ValidationError(f"unknown fact kind {kind}")
    if not isinstance(payload, dict):
        raise ValidationError("fact payload must be an object")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != FACT_SCHEMA_VERSION:
        raise ValidationError("fact requires schema_version=1")
    required, optional = FIELDS[kind]
    lists = LIST_FIELDS.get(kind, set())
    unknown = payload.keys() - {"schema_version", *required, *optional, *lists}
    if unknown:
        raise ValidationError(f"{kind}: unknown fields: {', '.join(sorted(unknown))}")
    for key in required:
        if key not in payload:
            raise ValidationError(f"{kind}: missing {key}")
    for key in (*required, *optional):
        if key in payload and (not isinstance(payload[key], str) or not payload[key].strip()):
            raise ValidationError(f"{kind}.{key} must be nonempty text")
    for key in lists:
        if key in payload and (not isinstance(payload[key], list) or any(
            not isinstance(value, str) or not value.strip() for value in payload[key]
        )):
            raise ValidationError(f"{kind}.{key} must be a list of nonempty strings")
    if kind == "finding" and "symbol" in payload and "path" not in payload:
        raise ValidationError("a symbol requires its source path")
    if kind == "search" and not payload.get("scope"):
        raise ValidationError("search.scope must identify the searched paths/globs")
    return payload


def _text(value: str) -> str:
    # JSON quoting preserves multiline/whitespace-sensitive text exactly without
    # allowing it to look like a separate control line. Ordinary prose stays plain.
    if value != value.strip() or any(ord(c) < 32 or c in "\u2028\u2029" for c in value):
        return json.dumps(value, ensure_ascii=False)
    return value


def _exact(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _sentence(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else text + "."


def render_fact(record_id: str, version: int, kind: str, payload: dict) -> str:
    """Render complete clauses without paraphrasing supplied propositions.

    Findings can express any relationship as concise prose. The structured forms
    cover common command/cursor/search records, not an exhaustive language.
    """
    validate_payload(kind, payload)
    if not isinstance(record_id, str) or not record_id or any(c.isspace() or c in "[]@" for c in record_id):
        raise ValidationError("fact display ID must be a nonempty token without []@")
    if type(version) is not int or version < 1:
        raise ValidationError("fact version must be a positive integer")
    p = payload
    when = f" when {_exact(p['when'])}" if p.get("when") else ""
    if kind == "goal":
        clauses = [f"Goal: {_text(p['text'])}"]
    elif kind == "constraint":
        clauses = [f"When {_exact(p['when'])}: {_text(p['text'])}" if p.get("when") else _text(p["text"])]
    elif kind == "decision":
        clauses = [f"Use {_text(p['choice'])}{when} because {_text(p['because'])}"]
    elif kind == "finding":
        location = p.get("path", "") + (f"::{p['symbol']}" if p.get("symbol") else "")
        clauses = [f"{_text(p['text'])} (source: {_exact(location)})" if location else _text(p["text"])]
    elif kind == "search":
        flags = f" flags={json.dumps(p['flags'], ensure_ascii=False, separators=(',', ':'))}" if p.get("flags") else ""
        scope = json.dumps(p["scope"], ensure_ascii=False, separators=(",", ":"))
        clauses = [f"Searching for {_exact(p['query'])} in {_exact(p['cwd'])} (scope={scope}{flags}) returned {_exact(p['result'])}"]
    elif kind == "command":
        clauses = [f"Use {_exact(p['command'])} in {_exact(p['cwd'])} for {_text(p['purpose'])}{when}"]
        if p.get("env"):
            clauses.append("It requires environment entries " + json.dumps(p["env"], ensure_ascii=False, separators=(",", ":")))
        if p.get("result"):
            clauses.append(f"Its recorded result is {_exact(p['result'])}")
    elif kind == "cursor":
        clauses = [f"{_exact(p['step'])} is {_text(p['phase'])}"]
        if p.get("last"):
            clauses.append(f"Its last completed action was {_exact(p['last'])}")
        clauses.append(f"Next, {_text(p['next'])}")
        if p.get("blocker"):
            clauses.append(f"It is blocked by {_text(p['blocker'])}")
        if p.get("children"):
            clauses.append("Its child states are " + json.dumps(p["children"], ensure_ascii=False, separators=(",", ":")))
    else:
        clauses = [f"Avoid {_text(p['approach'])}; its recorded failure is {_exact(p['failure'])}",
                   f"Retry only when {_text(p['retry_when'])}"]
    return f"[{record_id}@{version}] " + "\n  ".join(_sentence(clause) for clause in clauses)


@dataclass(frozen=True)
class Fact:
    record_id: str
    version: int
    kind: str
    payload: dict

    @property
    def required(self) -> bool:
        return self.kind in {"goal", "constraint", "cursor"}

    def render(self) -> str:
        return render_fact(self.record_id, self.version, self.kind, self.payload)


class RequiredContextOverflow(ValidationError):
    """Caller must hold admission/split; required facts cannot be shortened away."""


@dataclass(frozen=True)
class FactSelection:
    text: str
    selected: tuple[str, ...]
    omitted: tuple[str, ...]
    charged_tokens: int


def select_facts(facts: list[Fact], *, max_tokens: int, count_tokens=None,
                 required_ids: frozenset[str] = frozenset()) -> FactSelection:
    """Fit whole facts in caller-ranked order, with all required facts first.

    Input validity/source applicability is the compiler's responsibility. Omission
    is not a retention decision; its IDs go to the context/store/compress/drop
    lifecycle separately. Without an adapter tokenizer, bytes are charged as tokens.
    """
    if type(max_tokens) is not int or max_tokens < 1:
        raise ValidationError("fact budget must be a positive integer")
    count = count_tokens or (lambda text: len(text.encode("utf-8")))
    by_id = {}
    for fact in facts:
        previous = by_id.get(fact.record_id)
        if previous and previous != fact:
            raise ValidationError(f"conflicting versions/content for {fact.record_id}")
        by_id[fact.record_id] = fact
    missing = required_ids - by_id.keys()
    if missing:
        raise ValidationError(f"required facts absent: {', '.join(sorted(missing))}")
    required, optional = [], []
    for fact in by_id.values():
        (required if fact.required or fact.record_id in required_ids else optional).append(fact)
    lines = [fact.render() for fact in required]
    selected = [fact.record_id for fact in required]
    omitted = []

    def charge(text: str) -> int:
        value = count(text)
        if type(value) is not int or value < 0:
            raise ValidationError("token counter must return a nonnegative integer")
        return value

    if charge("\n".join(lines)) > max_tokens:
        raise RequiredContextOverflow("required continuation facts exceed the packet budget; split or increase admission budget")
    for fact in optional:
        line = fact.render()
        if charge("\n".join([*lines, line])) <= max_tokens:
            lines.append(line)
            selected.append(fact.record_id)
        else:
            omitted.append(fact.record_id)
    text = "\n".join(lines)
    return FactSelection(text, tuple(selected), tuple(omitted), charge(text))
