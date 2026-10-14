"""Registration, test, parity and exact precedent questions."""

from __future__ import annotations
import re
from pathlib import Path
from . import code_queries as query, code_store as store
from .code_extract import LANGUAGES, mask


def _members(conn, row, seen=None):
    seen = set() if seen is None else seen
    identity = (row["repo"], row["path"], row["qualified"])
    if identity in seen:
        return []
    seen.add(identity)
    prefix = row["qualified"] + "."
    rows = conn.execute(
        "SELECT * FROM symbols WHERE repo=? AND path=? AND qualified LIKE ? ORDER BY CASE lane WHEN 'resolved' THEN 0 WHEN 'syntax' THEN 1 ELSE 2 END,qualified,line LIMIT 101",
        (row["repo"], row["path"], prefix + "%"),
    )
    result = []
    identities = set()
    for r in rows:
        key = (r["qualified"], r["line"], r["column"])
        if (
            key not in identities
            and r["qualified"][len(prefix) :].count(".") == 0
            and r["kind"] in {"method", "function", "property"}
        ):
            identities.add(key)
            result.append(store.decorate(conn, r))
    if row["kind"] in {"interface", "trait"}:
        header = re.split(r":|\bextends\b", row["signature"], maxsplit=1)
        if len(header) == 2:
            for declaration in _parameters(header[1]):
                matched = re.match(r"\s*([\w.]+)", declaration)
                if matched:
                    for parent in query.symbols(conn, matched[1], repo=row["repo"])[:1]:
                        if parent["kind"] in {"interface", "trait"}:
                            result.extend(_members(conn, parent, seen))
    return result


def _parameters(value):
    out = []
    start = depth = 0
    for i, c in enumerate(value):
        if c in "<([{":
            depth += 1
        elif c in ">)]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(value[start:i])
            start = i + 1
    return [p.strip() for p in [*out, value[start:]] if p.strip()]


def normalize(signature, *, language=None):
    language = language or ("go" if signature.lstrip().startswith("func ") else None)
    signature = re.sub(r"^\s*func\s+(?:\([^)]*\)\s*)?", "", signature)
    value = re.sub(
        r"\b(public|private|protected|internal|abstract|virtual|override|async|static|sealed|readonly|unsafe|partial|export|default|pub|fn|function|def)\b",
        "",
        signature,
    )
    code = mask(value, rust=language == "rust")
    m = re.search(r"([\w$]+)(<[^()]*>)?\s*\(", code)
    if not m:
        return re.sub(r"\s+", "", value), bool(re.search(r"\bwhere\b|=|\(", value))
    depth = 1
    close = m.end() - 1
    for close in range(m.end(), len(code)):
        depth += (code[close] == "(") - (code[close] == ")")
        if depth == 0:
            break
    if depth:
        return re.sub(r"\s+", "", value), True
    before = value[: m.start()].strip()
    parameters = value[m.end() : close]
    tail = value[close + 1 :]
    params = []
    uncertain = bool(m[2] or "=" in parameters or re.search(r"\bwhere\b", tail))
    raw = _parameters(parameters)
    for index, p in enumerate(raw):
        if p in {"self", "cls", "&self", "&mut self"}:
            continue
        if language == "go":
            matched = re.match(r"^\w+\s+(.+)$", p)
            if matched:
                p = matched[1]
            elif any(re.match(r"^\w+\s+(.+)$", other) for other in raw[index + 1 :]):
                p = next(
                    re.match(r"^\w+\s+(.+)$", other)[1]
                    for other in raw[index + 1 :]
                    if re.match(r"^\w+\s+(.+)$", other)
                )
        elif ":" in p:
            p = p.split(":", 1)[1].strip()
        else:
            p = re.sub(r"\s+[A-Za-z_]\w*(?:\s*=.*)?$", "", p)
        params.append(re.sub(r"\s+", "", p))
    tail = tail.strip()
    if language == "go" and tail.startswith("(") and tail.endswith(")"):
        tail = (
            "("
            + ",".join(re.sub(r"^\w+\s+", "", p) for p in _parameters(tail[1:-1]))
            + ")"
        )
    return (
        re.sub(r"\s+", "", before)
        + m[1]
        + (m[2] or "")
        + "("
        + ",".join(params)
        + ")"
        + re.sub(r"\s+", "", tail)
    ), uncertain


def normalized(row):
    return normalize(row["signature"], language=LANGUAGES.get(Path(row["path"]).suffix))


def conform(conn, name, *, repo=None, limit=100):
    interfaces = [
        s
        for s in query.symbols(conn, name, repo=repo)
        if s["kind"] in {"interface", "trait", "class"}
    ]
    implementations = []
    diffs = []
    uncertain = []
    for interface in interfaces:
        members = _members(conn, interface)
        if "<" in interface["signature"] or re.search(
            r"\bwhere\b", interface["signature"]
        ):
            uncertain.append(
                dict(
                    interface=interface["qualified"],
                    reason="generic interface binding requires resolution",
                )
            )
        possible = conn.execute(
            "SELECT * FROM symbols WHERE repo=? AND kind IN ('class','struct','impl') ORDER BY path,line",
            (interface["repo"],),
        )
        candidates = []
        for row in possible:
            if row["qualified"] == interface["qualified"]:
                continue
            related = bool(
                re.search(
                    r"\b" + re.escape(interface["name"]) + r"\b", row["signature"]
                )
            )
            if not related:
                related = bool(
                    conn.execute(
                        "SELECT 1 FROM edges WHERE repo=? AND src_path=? AND kind='implements' AND dst_text IN (?,?) AND lane='resolved'",
                        (
                            interface["repo"],
                            row["path"],
                            interface["name"],
                            interface["qualified"],
                        ),
                    ).fetchone()
                )
            if related:
                candidates.append(store.decorate(conn, row))
        for implementation in candidates[:limit]:
            implementations.append(implementation)
            actual = _members(conn, implementation)
            for member in members:
                peers = [p for p in actual if p["name"] == member["name"]]
                expected, unknown = normalized(member)
                overloads = (
                    sum(m["name"] == member["name"] for m in members) > 1
                    or len(peers) > 1
                )
                if unknown or overloads:
                    uncertain.append(
                        dict(
                            interface=interface["qualified"],
                            implementation=implementation["qualified"],
                            member=member["name"],
                            reason="overload, generic constraint, or default value",
                        )
                    )
                if not peers:
                    diffs.append(
                        dict(
                            implementation=implementation["qualified"],
                            member=member["name"],
                            kind="missing",
                            expected=member["signature"],
                        )
                    )
                elif not any(normalized(p)[0] == expected for p in peers):
                    diffs.append(
                        dict(
                            implementation=implementation["qualified"],
                            member=member["name"],
                            kind="signature",
                            expected=member["signature"],
                            actual=[p["signature"] for p in peers],
                        )
                    )
                if any(normalized(p)[1] for p in peers):
                    uncertain.append(
                        dict(
                            member=member["name"],
                            reason="implementation signature requires resolution",
                        )
                    )
        if not members:
            uncertain.append(
                dict(interface=interface["qualified"], reason="no members indexed")
            )
        if not candidates:
            uncertain.append(
                dict(
                    interface=interface["qualified"], reason="no implementation indexed"
                )
            )
    tests = tests_for(conn, name, repo=repo, limit=limit)
    truncated = (
        len(implementations) > limit or len(diffs) > limit or len(uncertain) > limit
    )
    stale = any(r["stale"] for r in [*interfaces, *implementations, *tests])
    return dict(
        interface=name,
        interfaces=interfaces[:limit],
        implementations=implementations[:limit],
        tests=tests,
        diffs=diffs[:limit],
        uncertain=uncertain[:limit],
        truncated=truncated,
        stale=stale,
        clean=bool(interfaces and implementations)
        and not (diffs or uncertain or stale or truncated),
    )


def tests_for(conn, name, *, repo=None, limit=100):
    target = query.symbols(conn, name, repo=repo)
    if not target:
        target = [
            dict(r)
            for r in conn.execute(
                "SELECT name,repo,path FROM symbols WHERE path=? AND (? IS NULL OR repo=?) LIMIT 100",
                (name, repo, repo),
            )
        ]
    implementation_targets = list(target)
    for symbol in target:
        bases = conn.execute(
            "SELECT dst_text FROM edges WHERE repo=? AND src_path=? AND kind='implements'",
            (symbol["repo"], symbol["path"]),
        )
        for base in bases:
            implementation_targets.extend(
                query.symbols(conn, base[0], repo=symbol["repo"])
            )
    target = implementation_targets
    references = {
        (r["repo"], r["path"])
        for s in target
        for r in query.callers(conn, s["name"], repo=s["repo"])
    }
    names = {r["name"].casefold() for r in target} or {Path(name).stem.casefold()}
    result = []
    rows = conn.execute(
        "SELECT * FROM symbols WHERE (? IS NULL OR repo=?) ORDER BY repo,path,line",
        (repo, repo),
    )
    candidates = []
    for row in rows:
        labels = row["attributes"].casefold()
        is_test = (
            "test" in row["path"].casefold()
            or "spec" in row["path"].casefold()
            or "test" in row["name"].casefold()
            or any(t in labels for t in ("test", "fact", "theory"))
        )
        relevant = (row["repo"], row["path"]) in references or any(
            n in row["name"].casefold() for n in names
        )
        if is_test and relevant:
            stem = Path(name).stem.casefold()
            weight = 2 * int(stem in row["path"].casefold()) + int(
                any(n in row["name"].casefold() for n in names)
            )
            candidates.append((weight, dict(row)))
    for _, row in sorted(
        candidates,
        key=lambda item: (-item[0], item[1]["repo"], item[1]["path"], item[1]["line"]),
    )[:limit]:
        result.append(store.decorate(conn, row))
    return result


def run(conn, command, name, *, repo=None, limit=100, language=None):
    if command == "conform":
        return conform(conn, name, repo=repo, limit=limit)
    if command == "tests-for":
        return tests_for(conn, name, repo=repo, limit=limit)
    if command == "register":
        rows = conn.execute(
            "SELECT * FROM refs WHERE kind='registers' AND (lower(name) LIKE ? OR lower(qualified) LIKE ?) AND (? IS NULL OR repo=?) ORDER BY repo,path,line LIMIT ?",
            (
                "%" + name.casefold() + "%",
                "%" + name.casefold() + "%",
                repo,
                repo,
                limit,
            ),
        )
        return [store.decorate(conn, row) for row in rows]
    results = []
    # FTS nominates a bounded candidate set; returned snippets remain verbatim source.
    for hit in query.search(conn, name, repo=repo, limit=100):
        if language and LANGUAGES.get(Path(hit["path"]).suffix.lower()) != language:
            continue
        file = conn.execute(
            "SELECT body FROM files WHERE repo=? AND path=?", (hit["repo"], hit["path"])
        ).fetchone()
        if not file or name.casefold() not in file[0].casefold():
            continue
        text = file[0]
        position = text.casefold().find(name.casefold())
        hit.update(
            snippet=text[max(0, position - 100) : max(0, position - 100) + 300],
            line=text.count("\n", 0, position) + 1,
        )
        results.append(hit)
        if len(results) >= limit:
            break
    return results
