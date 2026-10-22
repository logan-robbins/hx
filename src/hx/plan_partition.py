"""Graph-derived providers, families, pairs and waves for a draft multi-unit plan.

`hx plan partition --repo WT [--scope PATH...] [--out plan.json]` reads one checkout's
file links through the bounded postings query (`code_queries.links`) and reports:

- providers: declarations whose callers sit in two or more scopes, ranked by fan-in;
  their files are built first and published as contracts;
- families: every other file joins the family of its smallest caller (by bytes), so a
  callee shared inside one scope is built once, by the cheapest unit that needs it; a
  file that nothing in scope calls heads its own family;
- pairs: links that a `partition.merge` rule in config/hx.json matches join their two
  families into one unit (for Natural, the FETCH pair: program to program);
- units with their needs (contract on a provider's file, hard on any other), merged
  where the needs form a cycle, and waves: Kahn levels over the hard needs, as
  `planning` computes start levels, with their width and depth.

`--out` writes the units as a schema-2 plan that `hx plan validate` accepts. The graph
cannot name interface files, so a provider's contract output is its source files and
publish-contract freezes them; split an interface file out before applying the draft.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from pathlib import Path, PurePosixPath

from .errors import ValidationError

LINK_KINDS = ("imports", "calls", "tests")
PATH_MATCH = {"path", "prefix", "suffix"}
SYMBOL_MATCH = {"name", "name_prefix", "name_suffix", "symbol_kind"}
MAX_RULES = 64


def merge_rules(root):
    """The `partition.merge` rules of config/hx.json: structural matches, no patterns."""
    from .loop_jobs import profile

    section = profile(root).get("partition")
    if section is None:
        return []
    if not isinstance(section, dict) or section.keys() - {"merge"}:
        raise ValidationError("config/hx.json partition holds only merge rules")
    return validated(section.get("merge", []))


def validated(rules):
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise ValidationError(f"config/hx.json partition.merge is a list of at most {MAX_RULES} rules")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict) or "id" not in rule or rule.keys() - {"id", "kind", "from", "to"}:
            raise ValidationError("a partition.merge rule names an id and matches on kind, from and to")
        if not isinstance(rule["id"], str) or not rule["id"] or rule["id"] in seen:
            raise ValidationError("partition.merge rule ids are unique nonempty strings")
        seen.add(rule["id"])
        if "kind" in rule and rule["kind"] not in LINK_KINDS:
            raise ValidationError("a partition.merge kind is imports, calls or tests")
        for side, allowed in (("from", PATH_MATCH), ("to", PATH_MATCH | SYMBOL_MATCH)):
            match = rule.get(side, {})
            if (not isinstance(match, dict) or match.keys() - allowed
                    or any(not isinstance(value, str) or not value for value in match.values())):
                raise ValidationError(
                    f"partition.merge {side} matches nonempty {', '.join(sorted(allowed))}")
        if not rule.get("from") and not rule.get("to"):
            raise ValidationError("a partition.merge rule matches a from or to endpoint")
    return rules


def _within(path, scope):
    return scope == "." or path == scope or path.startswith(scope + "/")


def _scope(path, scopes):
    if scopes is None:
        return path.split("/", 1)[0] if "/" in path else "."
    return max((s for s in scopes if _within(path, s)), key=lambda s: (s != ".", len(s)), default=None)


def _path_matches(match, path):
    return (("path" not in match or path == match["path"])
            and ("prefix" not in match or _within(path, match["prefix"].rstrip("/")))
            and ("suffix" not in match or path.casefold().endswith(match["suffix"].casefold())))


def _matches(rule, link):
    target = rule.get("to", {})
    if "kind" in rule and link["kind"] != rule["kind"]:
        return False
    if not _path_matches(rule.get("from", {}), link["from_path"]) or not _path_matches(target, link["to_path"]):
        return False
    if not target.keys() & SYMBOL_MATCH:
        return True
    if link["name"] is None:
        return False
    name = link["name"].casefold()
    return (("name" not in target or name == target["name"].casefold())
            and ("name_prefix" not in target or name.startswith(target["name_prefix"].casefold()))
            and ("name_suffix" not in target or name.endswith(target["name_suffix"].casefold()))
            and ("symbol_kind" not in target or link["symbol_kind"] == target["symbol_kind"]))


def _components(nodes, edges):
    """Strongly connected components, iteratively (Tarjan), in a fixed order."""
    index, low, stack, on_stack, result, counter = {}, {}, [], set(), [], 0
    for start in nodes:
        if start in index:
            continue
        work = [(start, iter(sorted(edges[start])))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is None:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    group = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        group.append(member)
                        if member == node:
                            break
                    result.append(sorted(group))
            elif child not in index:
                index[child] = low[child] = counter
                counter += 1
                stack.append(child)
                on_stack.add(child)
                work.append((child, iter(sorted(edges[child]))))
            elif child in on_stack:
                low[node] = min(low[node], index[child])
    return result


def unit_id(path, taken):
    """A plan identifier for the unit headed by `path`, readable and unique."""
    text = "".join(c if c.isascii() and (c.isalnum() or c in "._-") else "-" for c in path.replace("/", "."))
    if not text[:1].isalnum():
        text = "u" + text
    suffix = sha256(path.encode()).hexdigest()
    if len(text) > 128:
        text = text[:111] + "-" + suffix[:16]
    if text in taken:
        text = text[:119] + "-" + suffix[:8]
    taken.add(text)
    return text


def analyse(files, links, scopes, rules):
    """Partition `files` ({path: bytes}) over `links` between them.

    Each link is `{from_path, to_path, kind, name, qualified, symbol_kind}`; `name` is
    None for a file-level link (an import). `scopes` is None for top-level directories.
    """
    def order(path):
        return files[path], path

    links = sorted(
        {(l["from_path"], l["to_path"], l["kind"], l["qualified"] or "", l["name"] or ""): l
         for l in links
         if l["from_path"] in files and l["to_path"] in files and l["from_path"] != l["to_path"]}.items())
    links = [link for _, link in links]

    callers, meta = defaultdict(set), {}
    for link in links:
        if link["name"] is not None:
            key = (link["to_path"], link["qualified"])
            callers[key].add(link["from_path"])
            meta[key] = link
    providers = []
    for key, sources in callers.items():
        reach = {_scope(path, scopes) for path in sources}
        if len(reach) >= 2:
            providers.append(dict(path=key[0], name=meta[key]["name"], qualified=key[1],
                                  kind=meta[key]["symbol_kind"], fan_in=len(sources), scopes=sorted(reach),
                                  **({"ordinal": meta[key]["ordinal"]} if "ordinal" in meta[key] else {})))
    providers.sort(key=lambda p: (-p["fan_in"], -len(p["scopes"]), p["path"], p["qualified"]))
    provided = {provider["path"] for provider in providers}

    # A callee is built once, by its smallest caller; providers head their own family.
    users = defaultdict(set)
    for link in links:
        users[link["to_path"]].add(link["from_path"])
    parent = {path: min(users[path], key=order) for path in files if path not in provided and users[path]}
    head = {}
    for start in sorted(files):
        trail, node = [], start
        while node not in head and node in parent and node not in trail:
            trail.append(node)
            node = parent[node]
        if node in head:
            top = head[node]
        elif node in trail:
            # Smallest callers that call each other: the cycle's smallest file heads it.
            top = min(trail[trail.index(node):], key=order)
            del parent[top]
        else:
            top = node
        head[top] = top
        for member in trail:
            head[member] = top
    families = defaultdict(list)
    for path in sorted(files):
        families[head[path]].append(path)

    joined = {top: top for top in families}

    def find(top):
        while joined[top] != top:
            joined[top] = joined[joined[top]]
            top = joined[top]
        return top

    pairs = {}
    for link in links:
        rule = next((rule["id"] for rule in rules if _matches(rule, link)), None)
        if rule is None:
            continue
        pairs.setdefault((rule, link["from_path"], link["to_path"]), link["name"])
        a, b = find(head[link["from_path"]]), find(head[link["to_path"]])
        if a != b:
            keep = min(a, b, key=order)
            joined[a] = joined[b] = keep

    def needs_of(unit_of):
        needs = {unit: {} for unit in set(unit_of.values())}
        for link in links:
            source, target = unit_of[link["from_path"]], unit_of[link["to_path"]]
            if source != target:
                need = "contract" if link["to_path"] in provided else "hard"
                if needs[source].get(target) != "hard":
                    needs[source][target] = need
        return needs

    unit_of = {path: find(head[path]) for path in files}
    needs = needs_of(unit_of)
    # Units whose needs form a cycle cannot be ordered; they are one unit.
    cycles = [group for group in _components(sorted(needs), needs) if len(group) > 1]
    for group in cycles:
        keep = min(group, key=order)
        unit_of.update({path: keep for path, unit in unit_of.items() if unit in group})
    if cycles:
        needs = needs_of(unit_of)

    from .planning import _levels

    members = defaultdict(list)
    for path in sorted(files):
        members[unit_of[path]].append(path)
    taken = set()
    names = {top: unit_id(top, taken) for top in sorted(members, key=order)}
    waves = [[names[top] for top in level]
             for level in _levels({top: {other for other, need in needs[top].items() if need == "hard"}
                                   for top in members})]
    position = {name: depth for depth, level in enumerate(waves) for name in level}
    units = []
    for top in sorted(members, key=lambda top: (position[names[top]], names[top])):
        units.append(dict(
            id=names[top], head=top, files=members[top],
            provides=[p["qualified"] for p in providers if p["path"] in members[top]],
            needs=[{"unit": names[other], "need": need} for other, need in sorted(needs[top].items(), key=lambda item: names[item[0]])],
            wave=position[names[top]]))
    return dict(
        providers=providers,
        families=[dict(head=top, files=paths, provider=top in provided)
                  for top, paths in sorted(families.items(), key=lambda item: order(item[0]))],
        pairs=[dict(rule=rule, from_path=source, to_path=target, name=name)
               for (rule, source, target), name in sorted(pairs.items())],
        cycles=[{"unit": names[unit_of[group[0]]], "heads": group} for group in cycles],
        units=units, waves=waves,
        width=max((len(level) for level in waves), default=0), depth=len(waves))


def _scopes(checkout, values):
    if not values:
        return None
    result = []
    for value in values:
        relative = PurePosixPath(value.strip().removeprefix("./").rstrip("/") or ".")
        if relative.is_absolute() or ".." in relative.parts or not (checkout / relative).resolve().is_relative_to(checkout):
            raise ValidationError("partition scopes are repository-relative paths inside the checkout")
        result.append(str(relative))
    return sorted(set(result))


def report(root, checkout, scopes=None, rules=(), *, local=False):
    """The partition of `checkout` over `scopes`, read from the graph that answers it."""
    checkout = Path(checkout).resolve()
    if not local:
        from .repo_service import configured_socket, request

        socket = configured_socket(root)
        if socket is not None:
            return request(socket, {"method": "partition", "repository": str(checkout),
                                    "scopes": scopes, "merge": list(rules)})
    from . import code_queries, code_store
    from .code_index import select

    selected = _scopes(checkout, scopes)
    namespace = code_store.namespace(checkout)
    conn = code_store.connect(root, existing=True)
    if conn is None:
        raise ValidationError(f"partition requires an indexed checkout; run hx code index --repo {checkout}")
    try:
        if select(conn, repo=namespace, caller=checkout) is None:
            raise ValidationError(f"partition requires an indexed checkout; run hx code index --repo {checkout}")
        graph = code_queries.links(conn, repo=namespace)
        files = {path: info["size"] for (_, path), info in graph["files"].items()
                 if info["declarations"] and (selected is None or any(_within(path, s) for s in selected))}
        stale = sum(link["stale"] for link in graph["links"])
        result = analyse(files, [link for link in graph["links"] if not link["stale"]], selected, rules)
        # Postings fold qualified names; each provider reads as its own file declares it.
        from .code_snapshot import graph_for

        for provider in result["providers"]:
            fact = graph_for(conn, namespace, provider["path"])["symbols"][provider.pop("ordinal")]
            provider.update(qualified=fact["qualified"], signature=fact["signature"], line=fact["line"])
        for unit in result["units"]:
            unit["provides"] = [p["qualified"] for p in result["providers"] if p["path"] in unit["files"]]
    finally:
        conn.close()
    notes = ["A provider's contract output names its source files and publish-contract freezes them; "
             "split an interface file out of each provider unit before applying a draft."] if result["providers"] else []
    return dict(repository=namespace, checkout=str(checkout),
                scopes=selected if selected is not None else sorted({_scope(path, None) for path in files}),
                files=len(files), links=len(graph["links"]), stale_links=stale,
                truncated=graph["truncated"], **result, notes=notes)


def draft(result, policy, plan_id):
    """The partition's units as a schema-2 plan: a source output per unit, a contract
    output on each provider's files, and hard or contract prerequisites per need."""
    from .planning import MAX_UNITS

    if result["truncated"]:
        raise ValidationError("the link query was truncated; narrow --scope before drafting a plan")
    if not isinstance(policy, dict) or policy.keys() != {"reviewer", "required_checks", "role"}:
        raise ValidationError("a drafted plan takes its check policy, role included, from config/planning.json")
    if not 1 <= len(result["units"]) <= MAX_UNITS:
        raise ValidationError(f"partition has {len(result['units'])} units; a plan holds 1–{MAX_UNITS}, so narrow --scope")
    provided = {provider["path"] for provider in result["providers"]}
    tasks = []
    for unit in result["units"]:
        if len(unit["files"]) > 64 or len(unit["needs"]) > 64:
            raise ValidationError(f"unit {unit['id']} spans {len(unit['files'])} files and {len(unit['needs'])} needs; "
                                  "a unit holds at most 64 of each, so narrow --scope or merge fewer pairs")
        outputs = [{"id": "source", "version": 1, "kind": "source", "paths": unit["files"],
                    "obligation": f"The unit's {len(unit['files'])} files are implemented and pass its checks."}]
        contract = [path for path in unit["files"] if path in provided]
        if contract:
            outputs.append({"id": "contract", "version": 1, "kind": "contract", "paths": contract,
                            "obligation": "The published interface declares "
                                          + ", ".join(unit["provides"][:8]) + (" and more" if len(unit["provides"]) > 8 else "")
                                          + " before the provider completes."})
        prerequisites = [
            {"task_id": need["unit"], "output_id": "source", "version": 1, "need": "hard"} if need["need"] == "hard"
            else {"task_id": need["unit"], "output_id": "contract", "version": 1, "need": "contract",
                  "provider_output": "source", "provider_version": 1, "stand_in": False}
            for need in unit["needs"]]
        tasks.append({"id": unit["id"], "expected_revision": 0,
                      "behavior": f"Build the family headed by {unit['head']} ({len(unit['files'])} files).",
                      "acceptance": ["Every file of the unit is implemented and its required checks pass."],
                      "workdir": result["checkout"], "write_paths": unit["files"], "map_inputs": [],
                      "check_policy": dict(policy), "resources": [], "outputs": outputs, "prerequisites": prerequisites})
    return {"schema_version": 2, "plan_id": plan_id, "expected_revision": 0, "repository": result["repository"],
            "goal": f"Build {len(tasks)} graph-partitioned units of {result['checkout']} in {result['depth']} waves.",
            "constraints": ["Each unit writes only its own files; consumers of a provider build on its published contract."],
            "tasks": tasks}
