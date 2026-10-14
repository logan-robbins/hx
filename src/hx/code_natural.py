"""Build-free Natural source facts; runtime targets remain unresolved candidates.

See docs/search-and-retrieval.md for supported source formats and queries.
"""

import re
from bisect import bisect_right
from pathlib import Path

from .code_store import span_sha

OBJECT_KINDS = {
    ".nat": "natural_object",
    ".nsp": "program",
    ".nsn": "subprogram",
    ".nss": "subroutine",
    ".ns7": "function",
    ".nsc": "copycode",
    ".nsh": "helproutine",
    ".nsg": "data_area",
    ".nsl": "data_area",
    ".nsa": "data_area",
    ".nsm": "map",
    ".nsd": "ddm",
}
IDENTIFIER = r"[A-Za-z_#$&][A-Za-z0-9_#$&./-]*"
LEXEME = re.compile(
    r"'(?:''|[^'\n])*'|\"(?:\"\"|[^\"\n])*\"|" + IDENTIFIER + r"|\d+|[^\s]"
)
SECTIONS = {"PARAMETER", "LOCAL", "GLOBAL", "INDEPENDENT", "CONTEXT", "OBJECT"}


def return_header(words, start, end):
    """Consume a RETURNS layout, including wrapped formats and HANDLE OF."""
    i = start + 1
    if i < end and words[i] not in {"(", "HANDLE"}:
        i += 1  # Optional result variable.
    if i < end and words[i] == "(":
        depth = 1
        i += 1
        while i < end and depth:
            depth += (words[i] == "(") - (words[i] == ")")
            i += 1
    if words[i : i + 2] == ["HANDLE", "OF"]:
        i += 3
    if words[i : i + 1] == ["DYNAMIC"]:
        i += 1
    if words[i : i + 2] == ["BY", "VALUE"]:
        i += 2
    return min(i, end)


def source(text):
    """Remove listing numbers and comments without moving any source offsets.

    Blank-slash-asterisk is a line comment. An adjacent slash-asterisk in
    an array expression is executable syntax, and *DATX is a system variable.
    """
    lines = []
    for line in text.splitlines(keepends=True):
        line = re.sub(r"^([ \t]*\d{4,6})[ \t]", lambda m: " " * len(m[0]), line)
        if re.match(r"^[ \t]*\*(?:\s|\*|$)", line):
            line = re.sub(r"[^\r\n]", " ", line)
        else:
            for match in re.finditer(
                r"'(?:''|[^'\r\n])*'|\"(?:\"\"|[^\"\r\n])*\"|(?<!\S)/\*[^\r\n]*", line
            ):
                if match[0].startswith("/*"):
                    line = (
                        line[: match.start()]
                        + " " * len(match[0])
                        + line[match.end() :]
                    )
                    break
        lines.append(line)
    return "".join(lines)


def extract(text, path):
    clean = source(text)
    tokens = list(LEXEME.finditer(clean))
    if not tokens:
        return [], [], []
    words = [t[0].upper() for t in tokens]
    offsets = [0] + [m.end() for m in re.finditer("\n", text)]
    symbols, refs, edges = [], [], []
    name = Path(path).stem
    object_kind = OBJECT_KINDS[Path(path).suffix.lower()]

    def line(offset):
        return bisect_right(offsets, offset)

    def symbol(label, qualified, kind, start, end, header):
        first, last = line(start), line(end)
        return {
            "name": label,
            "qualified": qualified,
            "kind": kind,
            "path": path,
            "line": first,
            "end_line": last,
            "column": start - offsets[first - 1],
            "signature": " ".join(clean[start:header].split()),
            "lane": "syntax",
            "extractor": "stdlib-natural",
            "anchor_sha": span_sha(text, first, last),
            "attributes": [],
        }

    def reference(target, kind, token):
        refs.append(
            {
                "name": target,
                "qualified": target,
                "path": path,
                "line": line(token.start()),
                "kind": kind,
                "extractor": "stdlib-natural",
            }
        )

    # Layout sources have their own editor serialization, not executable statements.
    if object_kind in {"map", "ddm"}:
        return (
            [
                symbol(
                    name,
                    name,
                    object_kind,
                    tokens[0].start(),
                    tokens[-1].end() - 1,
                    tokens[0].end(),
                )
            ],
            [],
            [],
        )

    data = []
    routines = []
    stack = []
    variable_functions = set()
    data_ends = [i for i, w in enumerate(words) if w == "END-DEFINE"]
    for i, word in enumerate(words):
        if words[i : i + 2] == ["DEFINE", "DATA"]:
            end = bisect_right(data_ends, i + 1)
            if end < len(data_ends):
                data.append((i, data_ends[end]))
        if word == "DEFINE" and i + 2 < len(words):
            kind = words[i + 1]
            shorthand = kind not in {
                "DATA",
                "FUNCTION",
                "PROTOTYPE",
                "SUBROUTINE",
                "CLASS",
                "PRINTER",
                "WORK",
                "WINDOW",
            }
            if shorthand:
                kind = "SUBROUTINE"
            variable = False
            n = i + 1 if shorthand else i + 2
            if kind == "PROTOTYPE" and words[n] == "FOR":
                n += 1
            if n < len(words) and kind == "PROTOTYPE" and words[n] == "VARIABLE":
                variable = True
                n += 1
            if kind in {"FUNCTION", "SUBROUTINE", "PROTOTYPE"} and n < len(words):
                if not re.fullmatch(IDENTIFIER, tokens[n][0]):
                    continue
                label = tokens[n][0]
                if variable:
                    variable_functions.add(label.upper())
                parent = (
                    stack[-1]["qualified"]
                    if stack
                    else (
                        ""
                        if kind == "FUNCTION" or object_kind == "subroutine"
                        else name
                    )
                )
                routine = {
                    "start": i,
                    "label": label,
                    "qualified": ".".join(filter(None, [parent, label])),
                    "kind": kind.lower(),
                    "end": None,
                    "header": n + 1,
                }
                routines.append(routine)
                stack.append(routine)
        if stack and (
            word == "END-" + stack[-1]["kind"].upper()
            or (
                word == "RETURN"
                and stack[-1]["kind"] == "subroutine"
                and words[i - 1 : i] != ["FETCH"]
                and words[i - 2 : i] != ["FETCH", "REPEAT"]
            )
        ):
            stack.pop()["end"] = i

    external_root = (
        next(
            (
                r
                for r in routines
                if r["kind"] == "subroutine" and r["qualified"] == r["label"]
            ),
            None,
        )
        if object_kind == "subroutine"
        else None
    )
    # Header includes return and data declarations when they follow a definition.
    for routine in routines:
        end = routine["end"]
        if end is None:
            continue  # Never manufacture a complete declaration from broken source.
        header = routine["header"]
        if words[header : header + 1] == ["RETURNS"]:
            header = return_header(words, header, end)
        for first, last in data:
            if first == header:
                header = last + 1
                break
        if routine["kind"] == "prototype":
            header = end
        start = routine["start"]
        if routine is external_root and data and data[0][1] < start:
            start = data[0][0]
        symbols.append(
            symbol(
                routine["label"],
                routine["qualified"],
                routine["kind"],
                tokens[start].start(),
                tokens[end].end() - 1,
                tokens[header].start(),
            )
        )

    first, last = tokens[0].start(), tokens[-1].end() - 1
    header = tokens[0].end()
    if data and data[0][0] == 0:
        header = tokens[data[0][1]].end()
    if not any(
        r["qualified"].casefold() == name.casefold()
        and r["kind"] in {"function", "subroutine"}
        for r in symbols
    ):
        symbols.insert(
            0,
            symbol(
                name,
                name,
                "natural_object"
                if object_kind in {"function", "subroutine"}
                else object_kind,
                first,
                last,
                header,
            ),
        )

    # Fields retain hierarchical level names and multiline format/mode declarations.
    for first, last in data:
        enclosing = [
            r
            for r in routines
            if r["end"] is not None and r["start"] < first < r["end"]
        ]
        prefix = (
            enclosing[-1]["qualified"]
            if enclosing
            else (
                external_root["qualified"]
                if external_root is not None and external_root["end"] is not None
                else name
            )
        )
        section = "LOCAL"
        fields = []
        for i in range(first + 2, last):
            at_start = not clean[
                offsets[line(tokens[i].start()) - 1] : tokens[i].start()
            ].strip()
            if words[i] in SECTIONS and (i == first + 2 or at_start):
                section = words[i]
                fields.append((i, None, section))
            elif (
                at_start
                and words[i].isdigit()
                and 1 <= int(words[i]) <= 99
                and i + 1 < last
            ):
                if (
                    re.fullmatch(IDENTIFIER, tokens[i + 1][0])
                    and words[i + 1] != "REDEFINE"
                ):
                    fields.append((i, int(words[i]), section))
        parents = []
        for f, (i, level, section) in enumerate(fields):
            if level is None:
                parents = []
                continue
            while parents and parents[-1][0] >= level:
                parents.pop()
            label = tokens[i + 1][0]
            qualified = ".".join([prefix] + [p[1] for p in parents] + [label])
            next_i = fields[f + 1][0] if f + 1 < len(fields) else last
            boundary = next(
                (
                    fields[k][0]
                    for k in range(f + 1, len(fields))
                    if fields[k][1] is None or fields[k][1] <= level
                ),
                last,
            )
            view = "VIEW" in words[i:next_i]
            symbols.append(
                symbol(
                    label,
                    qualified,
                    "view"
                    if view
                    else ("parameter" if section == "PARAMETER" else "variable"),
                    tokens[i].start(),
                    tokens[boundary - 1].end() - 1,
                    tokens[next_i].start(),
                )
            )
            parents.append((level, label))

    for i, word in enumerate(words):
        n = i + 1
        while word == "FETCH" and n < len(words) and words[n] in {"RETURN", "REPEAT"}:
            n += 1
        if word in {"CALLNAT", "FETCH", "PERFORM", "INCLUDE"} and n < len(words):
            token = tokens[n]
            quoted = token[0][:1] in {"'", '"'}
            target = token[0][1:-1] if quoted else token[0]
            dynamic = (word in {"CALLNAT", "FETCH"} and not quoted) or "&" in target
            kind = (
                "dynamic_calls"
                if dynamic
                else ("imports" if word == "INCLUDE" else "calls")
            )
            reference(target, kind, tokens[i])
            if kind == "imports":
                edges.append((path, target, "imports"))
        if (
            word in SECTIONS
            and words[i + 1 : i + 2] == ["USING"]
            and i + 2 < len(words)
        ) and any(first < i < last for first, last in data):
            target = tokens[i + 2][0]
            reference(target, "imports", tokens[i])
            edges.append((path, target, "imports"))
        if words[i : i + 2] == ["VIEW", "OF"] and i + 2 < len(words):
            target = tokens[i + 2][0]
            reference(target, "imports", tokens[i])
            edges.append((path, target, "imports"))
        if word == "MAP" and words[i - 1 : i] == ["USING"] and i + 1 < len(words):
            token = tokens[i + 1]
            quoted = token[0][:1] in {"'", '"'}
            target = token[0][1:-1] if quoted else token[0]
            reference(target, "imports" if quoted else "dynamic_calls", tokens[i])
            if quoted:
                edges.append((path, target, "imports"))
        if re.fullmatch(IDENTIFIER, tokens[i][0]) and words[i + 1 : i + 3] == [
            "(",
            "<",
        ]:
            reference(
                tokens[i][0],
                "dynamic_calls" if word in variable_functions else "calls",
                tokens[i],
            )
    return symbols, refs, edges
