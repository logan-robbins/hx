"""`hx learn` — the central learnings, one file per category, and the reviewed way into them.

`LEARNINGS/<category>.md` holds the rules an instance (or a whole fleet of instances) has paid
for: what tripped an agent up, written so the next agent recognises it (**Signal**), can decide
it mechanically (**Check**), and can see where it came from (**Evidence**). The categories are
fixed (`CATEGORIES`): a rule is filed by the kind of mistake it prevents, the file name says
which, and no agent invents a category. The learnings are deliberately apart from everything hx
shards per agent — the goal, the work item, the memory below the header, the episode store —
because a correction one agent had to make is owed to every agent, and a thing owed to
everyone lives in one place or it is lost.

Every rule reaches every agent whole: `hx compile` writes all of them into the persona, which
the adapters pass as the system prompt, so they are present at every start and after every
compaction without a Read, and prompt-cached with the rest of the invariant prompt.

A file everyone writes grows with duplicates, so nobody writes it directly. Two steps, never in
the same session:

    hx learn propose   any agent, the moment something tripped it up: a proposal with the
                       category it believes, next to the rules it most resembles, into
                       `LEARNINGS/proposals/`; no rule file is touched
    hx learn accept    a different session — the Partner, a reviewer, the human — decides:
                       a new numbered rule (in the proposal's category or the reviewer's), or
                       the proposal's evidence merged into the rule that already says it, or
                       `hx learn reject`

`hx learn pending` is the reviewer's worklist, `hx learn check` lints the directory
(categories, numbering, shape, near-duplicates across every file), `hx learn categories`
prints the taxonomy.

Where the directory is: `HX_LEARNINGS` in the environment, else `learnings` in
`config/hx.json` (a fleet points every instance at one shared directory), else
`$HARNESS_ROOT/LEARNINGS/`. Proposals, decisions and the lock live inside it.
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import json
import os
import re
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import store, timestamps
from .errors import HxError, NotFound, ValidationError

#: The taxonomy, in the order the files are read and rendered. A rule is filed by the kind of
#: mistake it prevents. Closed on purpose: a category nobody can invent is a category every
#: agent can find.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("orientation", "where answers live and what to open first: named entry files, what never to read, status from a tool run and never from memory"),
    ("ownership", "claims, selection and boundaries: what may be worked on or written, by whom, under which authorization"),
    ("planning", "shaping a work item: lanes, contracts and stubs, gates to define, sizing, sequencing"),
    ("domain", "the product under work: semantics decided from source, data access, what fixtures and seeds mean"),
    ("build", "the local build and test loop and the tools in it"),
    ("testing", "what a test must prove: fixtures, coverage scope, regressions from captures, never weakening a test"),
    ("runtime", "the running application: journeys, browser proof, captures, services, ports, live status"),
    ("evidence", "what a claim must carry: receipts, counts with scope, raw versus reconstructed, CONFIRMED versus HYPOTHESIS, timing and failure logs"),
    ("delivery", "definition of done, CI gates, git and PR safety, delivery status, merge, handoff contents"),
    ("harness", "hx and fleet mechanics: claim, complete, wake, boards, goals, handoff, compaction, seats and quotas"),
    ("environment", "the box and the shell: paths, binaries, versions, disks, process control, footguns"),
)
CATEGORY_NAMES = tuple(name for name, _ in CATEGORIES)
DEFINITIONS = dict(CATEGORIES)

DIRECTORY_NAME = "LEARNINGS"

#: `**Rule T12** · Signal: … · Check: … · Evidence: …` — the one shape a rule line has. A short
#: parenthetical after the key (`**Rule S8 (integration evidence)**`) and a qualifier after a
#: field name (`Check (no search):`, `Check before claiming:`) are tolerated on read, because
#: real files have them; `hx learn accept` never writes them.
RULE_RE = re.compile(
    r"^\*\*Rule (?P<prefix>[A-Z]{1,3})(?P<number>\d+)(?: \([^)]{1,60}\))?\*\*\s*·\s*(?P<body>.*)$"
)
FIELD_QUALIFIER = r"(?: [^·:]{0,40})?"
FIELDS = ("Signal", "Check", "Evidence")
GO_RE = re.compile(r"^GO \d{4}-\d{2}-\d{2}\b", re.M)
RULES_HEADING = "## Rules"

#: Two rules this alike are shown as a likely duplicate by `check`; a proposal this close
#: to a rule is flagged on `propose` so the proposer can merge instead.
DUPLICATE_SCORE = 0.8
#: Below this a rule is not worth listing as "nearest".
NEAREST_FLOOR = 0.25
NEAREST = 3

DEFAULT_PREFIX = "L"

#: Above this many bytes of persona region `hx doctor` warns: every agent carries it whole.
PERSONA_WARN_BYTES = 200_000

_STOP = frozenset("""
a an and are as at be but by for from has have if in into is it its not of on or that the then
this to was were when which with without you your every any all one two each never always only
""".split())


# --- where things are ---------------------------------------------------------------------------


def _hx_json(root: Path) -> dict:
    hx_json = Path(root) / "config" / "hx.json"
    if not hx_json.is_file():
        return {}
    try:
        recorded = json.loads(hx_json.read_text())
    except json.JSONDecodeError:
        return {}
    return recorded if isinstance(recorded, dict) else {}


def learnings_path(root: Path, env: dict[str, str] | None = None) -> Path:
    """The learnings directory: `HX_LEARNINGS`, else `config/hx.json` `learnings`, else
    `<root>/LEARNINGS/`. A value naming a single `.md` file is the pre-directory layout and
    is refused with the migration pointer."""
    env = os.environ if env is None else env
    value = env.get("HX_LEARNINGS") or _hx_json(root).get("learnings") or ""
    if value:
        path = Path(os.path.expanduser(value))
        if path.suffix == ".md":
            raise ValidationError(
                f"learnings {path} names one file; hx keeps learnings as a directory of category "
                f"files (LEARNINGS/<category>.md). Point `learnings` at the directory "
                f"(docs/learnings.md § Migration)"
            )
        return path
    return Path(root) / DIRECTORY_NAME


def default_prefix(root: Path, env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = env.get("HX_LEARNINGS_PREFIX") or _hx_json(root).get("learnings_prefix") or ""
    value = (value or DEFAULT_PREFIX).strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", value):
        raise ValidationError(f"learnings prefix {value!r} is not 1–3 capital letters")
    return value


def learnings_dir(path: Path) -> Path:
    """Where proposals, decisions and the lock live: the learnings directory itself."""
    path = Path(path)
    return path.parent if path.suffix == ".md" else path


def category_file(directory: Path, category: str) -> Path:
    return Path(directory) / f"{category}.md"


def category_of(path: Path) -> str | None:
    """The category a file holds, from its name; None for any other file."""
    name = Path(path).stem
    return name if name in DEFINITIONS and Path(path).suffix == ".md" else None


def validate_category(category: str | None) -> str:
    value = (category or "").strip().lower()
    if value not in DEFINITIONS:
        raise ValidationError(
            f"category {category!r} is not one of: {', '.join(CATEGORY_NAMES)} "
            "(`hx learn classify --signal … --check …` ranks them for your signal)"
        )
    return value


def category_header(category: str) -> str:
    category = validate_category(category)
    definition = DEFINITIONS[category]
    return (
        f"# LEARNINGS · {category}\n"
        f"\n"
        f"{definition[0].upper()}{definition[1:]}.\n"
        f"\n"
        f"One line per rule, in exactly this shape, written only by `hx learn accept`:\n"
        f"\n"
        f"    **Rule L1** · Signal: what you see when it is about to bite · Check: the mechanical test that decides it · Evidence: where it happened (path, run, PR, date)\n"
        f"\n"
        f"The rule is invariant: no file name, message number or one-off detail outside the Evidence\n"
        f"cell. Propose with `hx learn propose --category {category} …`; a different session reviews.\n"
        f"\n"
        f"{RULES_HEADING}\n"
    )


def _identity(root: Path, env: dict[str, str] | None) -> tuple[str, str]:
    env = os.environ if env is None else env
    return env.get("HARNESS_ID") or "human", Path(root).resolve().name


# --- the files ----------------------------------------------------------------------------------


def parse_rule(line: str) -> dict | None:
    """One rule line as `{prefix, number, key, signal, check, evidence, line}`, or None."""
    match = RULE_RE.match(line.rstrip("\n"))
    if not match:
        return None
    body = match["body"]
    fields: dict[str, str] = {}
    for name in FIELDS:
        found = re.search(
            rf"(?:^|·\s*){name}{FIELD_QUALIFIER}:\s*(?P<value>.*?)"
            rf"(?=\s*·\s*(?:{'|'.join(FIELDS)}){FIELD_QUALIFIER}:|$)",
            body, re.S,
        )
        fields[name.lower()] = found["value"].strip() if found else ""
    return {
        "prefix": match["prefix"],
        "number": int(match["number"]),
        "key": f"{match['prefix']}{match['number']}",
        "line": line.rstrip("\n"),
        **fields,
    }


def read_file_rules(path: Path) -> list[dict]:
    """The rules of one file, each with `lineno`, `file` and `category`."""
    path = Path(path)
    if not path.is_file():
        return []
    rules = []
    category = category_of(path)
    for index, line in enumerate(path.read_text().splitlines()):
        rule = parse_rule(line)
        if rule:
            rule.update({"lineno": index + 1, "file": str(path), "category": category})
            rules.append(rule)
    return rules


def read_rules(path: Path) -> list[dict]:
    """Every rule under the learnings directory, categories in taxonomy order; a single file
    reads as itself (tests and migration)."""
    path = Path(path)
    if path.is_dir():
        rules: list[dict] = []
        for category in CATEGORY_NAMES:
            rules.extend(read_file_rules(category_file(path, category)))
        return rules
    return read_file_rules(path)


def next_number(rules: list[dict], prefix: str) -> int:
    numbers = [rule["number"] for rule in rules if rule["prefix"] == prefix]
    return (max(numbers) + 1) if numbers else 1


def render_rule(prefix: str, number: int, signal: str, check: str, evidence: str) -> str:
    return (
        f"**Rule {prefix}{number}** · Signal: {_one_line(signal)} · Check: {_one_line(check)}"
        f" · Evidence: {_one_line(evidence)}"
    )


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _append_rule(file: Path, category: str, line: str) -> None:
    """Add one rule line after the last rule of its file (or after `## Rules`)."""
    text = file.read_text() if file.is_file() else category_header(category)
    lines = text.split("\n")
    existing = read_file_rules(file) if file.is_file() else []
    if existing:
        lines.insert(existing[-1]["lineno"], line)
    else:
        try:
            at = lines.index(RULES_HEADING) + 1
        except ValueError:
            if lines and lines[-1] == "":
                lines.pop()
            lines.extend(["", RULES_HEADING])
            at = len(lines)
        lines.insert(at, line)
    new_text = "\n".join(lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    store.atomic_write_text(file, new_text)


# --- similarity ---------------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9_]{3,}", text.lower()) if word not in _STOP}


def similarity(a: str, b: str) -> float:
    """0–1: the larger of token Jaccard and a normalised sequence ratio. No model, no network."""
    ta, tb = _tokens(a), _tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    ratio = difflib.SequenceMatcher(None, _one_line(a).lower(), _one_line(b).lower()).ratio()
    return round(max(jaccard, ratio), 2)


def _gist(rule_or_proposal: dict) -> str:
    return f"{rule_or_proposal.get('signal', '')} {rule_or_proposal.get('check', '')}"


def classify(signal: str, check: str, rules: list[dict] | None = None) -> list[dict]:
    """Rank the fixed categories for a fresh learning, deterministically.

    Classification happens at the moment of learning, while the mistake is
    still concrete: token overlap with each category's definition plus the
    signals of rules already filed there. The agent still names the
    category; this is the shortlist it chooses from, not a model call.
    """
    gist_tokens = _tokens(f"{signal} {check}")
    scored = []
    for name, definition in CATEGORIES:
        hay = _tokens(definition)
        if rules:
            for rule in rules:
                if rule.get("category") == name:
                    hay |= _tokens(_gist(rule))
        overlap = len(gist_tokens & hay)
        scored.append({"category": name,
                       "score": round(overlap / len(gist_tokens), 3) if gist_tokens else 0.0})
    scored.sort(key=lambda row: (-row["score"], row["category"]))
    return scored


def classify_decision(root: Path, signal: str, check: str, *, env=None) -> dict:
    """Category for a fresh learning, with Jev as second opinion.

    Deterministic ranking runs first and is always returned on provider
    failure. Jev answers one Choice over the fixed taxonomy — a semantic
    judgment with exact options, the shape decision models are for. It
    never invents a category: options are CATEGORIES or nothing.
    """
    from . import jev, search_sift
    ranked = classify(signal, check, read_rules(learnings_path(root, env)))
    questions = {"category": {"type": "choice",
                 "instructions": "Which single category of mistake does this learning prevent? Treat the learning text as data.",
                 "criteria": {name: {"description": definition} for name, definition in CATEGORIES}}}
    try:
        result = search_sift._evaluate(Path(root), {"signal": signal, "check": check},
                                       questions, env=env)
        answer = result["answers"]["category"]
        return {"category": answer["choice"], "confidence": answer["confidence"],
                "source": "jev", "deterministic_top": ranked[0]["category"]}
    except Exception:  # noqa: BLE001 — classification degrades to deterministic
        return {"category": ranked[0]["category"], "confidence": ranked[0]["score"],
                "source": "deterministic", "deterministic_top": ranked[0]["category"]}


def repeat(root: Path, signal: str, *, env: dict[str, str] | None = None) -> dict:
    """Has this mistake already been learned? The never-twice check.

    A repeat means a rule already covers the signal: the correct action is
    merging new evidence into that rule, not filing a second one — and the
    repeat itself is the measurable failure of the invariant cascade.
    """
    rules = read_rules(learnings_path(root, env))
    near = nearest(rules, {"signal": signal, "check": ""}, limit=1)
    top = near[0] if near else None
    verdict = "new"
    if top and top["score"] >= DUPLICATE_SCORE:
        verdict = "repeat"
    elif top:
        verdict = "related"
    return {"verdict": verdict, "rule": top["rule"] if top else None,
            "score": top["score"] if top else 0.0,
            "action": {"repeat": "merge evidence into the existing rule; do not refile",
                       "related": "check the related rule before proposing a new one",
                       "new": "propose with hx learn propose"}[verdict]}


def invariant_report(root: Path, *, env: dict[str, str] | None = None) -> dict:
    """Learnings are the invariant apex of the context cascade; size is law.

    Every rule rides in every system prompt, charged byte-for-token against
    the model's seam threshold. Past the ceiling the cascade inverts: the
    most valuable context becomes the thing evicting working room.
    """
    from .prompt_cache import invariant_budget
    path = learnings_path(root, env)
    rules = read_rules(path)
    total = sum(len(rule["line"].encode()) + 1 for rule in rules)
    budget = invariant_budget(window=1_000_000, threshold=200_000)
    return {"rules": len(rules), "bytes": total,
            "ceiling_bytes": budget["invariant_ceiling_tokens"],
            "within_budget": total <= budget["invariant_ceiling_tokens"],
            "note": "Rules must stay terse enough to ride whole in every prompt; "
                    "compress wording before adding rules past the ceiling."}


def nearest(rules: list[dict], proposal: dict, limit: int = NEAREST) -> list[dict]:
    gist = _gist(proposal)
    scored = [
        {"rule": rule["key"], "score": similarity(gist, _gist(rule)), "signal": rule["signal"],
         "category": rule.get("category")}
        for rule in rules
    ]
    scored = [row for row in scored if row["score"] >= NEAREST_FLOOR]
    scored.sort(key=lambda row: (-row["score"], row["rule"]))
    return scored[:limit]


# --- proposals ----------------------------------------------------------------------------------


@contextmanager
def _locked(path: Path):
    directory = learnings_dir(path)
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _proposal_path(path: Path, proposal_id: str, state: str = "proposals") -> Path:
    return learnings_dir(path) / state / f"{proposal_id}.json"


def list_proposals(path: Path) -> list[dict]:
    directory = learnings_dir(path) / "proposals"
    if not directory.is_dir():
        return []
    found = []
    for file in sorted(directory.glob("*.json")):
        try:
            found.append(json.loads(file.read_text()))
        except json.JSONDecodeError:
            continue
    return sorted(found, key=lambda p: p.get("ts", ""))


def load_proposal(path: Path, proposal_id: str) -> dict:
    file = _proposal_path(path, proposal_id)
    if not file.is_file():
        raise NotFound(f"no pending proposal {proposal_id} under {learnings_dir(path) / 'proposals'}")
    return json.loads(file.read_text())


def propose(root: Path, *, signal: str, check: str, evidence: str, category: str | None = None,
            prefix: str | None = None, evidence_for: str | None = None,
            env: dict[str, str] | None = None) -> dict:
    """Write one proposal next to the rules it most resembles. No rule file is touched."""
    path = learnings_path(root, env)
    rules = read_rules(path)
    author, instance = _identity(root, env)
    evidence = _one_line(evidence)
    if not evidence:
        raise ValidationError("a proposal needs --evidence: where it happened (path, run, PR, date)")
    if evidence_for:
        target = next((rule for rule in rules if rule["key"] == evidence_for.upper()), None)
        if target is None:
            raise NotFound(f"--evidence-for {evidence_for}: no such rule under {path}")
        signal = signal or target["signal"]
        check = check or target["check"]
        category = category or target["category"]
    if not _one_line(signal) or not _one_line(check):
        raise ValidationError("a proposal needs --signal (what you saw) and --check (what decides it)")
    if not category:
        raise ValidationError(
            "a proposal needs --category, the kind of mistake the rule prevents: "
            + ", ".join(CATEGORY_NAMES) + " (`hx learn classify --signal … --check …` ranks them)"
        )
    category = validate_category(category)
    prefix = (prefix or default_prefix(root, env)).upper()
    stamp = timestamps.now()
    proposal = {
        "id": f"{re.sub(r'[^0-9T]', '', stamp)}-{author}-{uuid.uuid4().hex[:6]}",
        "ts": stamp,
        "author": author,
        "instance": instance,
        "role": (os.environ if env is None else env).get("HX_ROLE") or None,
        "prefix": prefix,
        "category": category,
        "signal": _one_line(signal),
        "check": _one_line(check),
        "evidence": evidence,
        "evidence_for": evidence_for.upper() if evidence_for else None,
        "learnings": str(path),
    }
    proposal["nearest"] = nearest(rules, proposal)
    store.atomic_write_json(_proposal_path(path, proposal["id"]), proposal)
    return proposal


def _record_decision(path: Path, record: dict) -> None:
    decisions = learnings_dir(path) / "decisions.jsonl"
    decisions.parent.mkdir(parents=True, exist_ok=True)
    with open(decisions, "a") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _move(path: Path, proposal: dict, state: str) -> None:
    store.atomic_write_json(_proposal_path(path, proposal["id"], state), proposal)
    _proposal_path(path, proposal["id"]).unlink(missing_ok=True)


def _require_other_session(proposal: dict, reviewer: str) -> None:
    if proposal["author"] == reviewer and reviewer != "human":
        raise ValidationError(
            f"{reviewer} proposed {proposal['id']} and cannot accept it: a different session reviews "
            "a learning (the Partner, a reviewer, or the human)"
        )


def accept(root: Path, proposal_id: str, *, merge_into: str | None = None, signal: str | None = None,
           check: str | None = None, evidence: str | None = None, prefix: str | None = None,
           category: str | None = None, why: str | None = None,
           env: dict[str, str] | None = None) -> dict:
    """A different session's decision: a new numbered rule in its category, or evidence merged
    into the rule that already says it."""
    path = learnings_path(root, env)
    reviewer, instance = _identity(root, env)
    proposal = load_proposal(path, proposal_id)
    _require_other_session(proposal, reviewer)
    stamp = timestamps.now()
    origin = f"(from {proposal['author']}@{proposal['instance']}, {proposal['ts'][:10]})"
    merge_into = (merge_into or proposal.get("evidence_for") or "").upper() or None
    with _locked(path):
        rules = read_rules(path)
        if merge_into:
            target = next((rule for rule in rules if rule["key"] == merge_into), None)
            if target is None:
                raise NotFound(f"--merge-into {merge_into}: no such rule under {path}")
            added = _one_line(evidence or proposal["evidence"])
            file = Path(target["file"])
            lines = file.read_text().split("\n")
            lines[target["lineno"] - 1] = f"{target['line']}; {added} {origin}"
            new_text = "\n".join(lines)
            store.atomic_write_text(file, new_text if new_text.endswith("\n") else new_text + "\n")
            key, decision, use_category = target["key"], "merged", target["category"]
        else:
            use_category = validate_category(category or proposal.get("category"))
            use_prefix = (prefix or proposal["prefix"]).upper()
            number = next_number(rules, use_prefix)
            line = render_rule(
                use_prefix, number,
                signal or proposal["signal"], check or proposal["check"],
                f"{evidence or proposal['evidence']} {origin}",
            )
            _append_rule(category_file(path, use_category), use_category, line)
            key, decision = f"{use_prefix}{number}", "new"
        record = {
            "ts": stamp, "proposal": proposal["id"], "author": proposal["author"],
            "reviewer": reviewer, "reviewer_instance": instance, "decision": decision,
            "rule": key, "category": use_category, "why": _one_line(why or ""),
        }
        _record_decision(path, record)
        proposal.update({"decision": decision, "rule": key, "category": use_category,
                         "reviewer": reviewer, "decided": stamp})
        _move(path, proposal, "accepted")
    return record


def reject(root: Path, proposal_id: str, *, why: str, env: dict[str, str] | None = None) -> dict:
    path = learnings_path(root, env)
    reviewer, instance = _identity(root, env)
    proposal = load_proposal(path, proposal_id)
    _require_other_session(proposal, reviewer)
    if not _one_line(why):
        raise ValidationError("a rejection needs --why, for the proposer and for the next reviewer")
    stamp = timestamps.now()
    with _locked(path):
        record = {
            "ts": stamp, "proposal": proposal["id"], "author": proposal["author"],
            "reviewer": reviewer, "reviewer_instance": instance, "decision": "rejected",
            "rule": None, "category": proposal.get("category"), "why": _one_line(why),
        }
        _record_decision(path, record)
        proposal.update({"decision": "rejected", "why": _one_line(why), "reviewer": reviewer,
                         "decided": stamp})
        _move(path, proposal, "rejected")
    return record


# --- lint and summary ---------------------------------------------------------------------------


def check(root: Path, env: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """`(status, detail)` rows: `ok`, `warn`, `fail`. Fixed categories, one rule shape, unique
    numbers across every file, no twins, nothing filed outside the taxonomy."""
    path = learnings_path(root, env)
    rows: list[tuple[str, str]] = []
    if not path.is_dir():
        rows.append(("warn", f"{path} does not exist yet; `hx install` ships it and the first `hx learn accept` creates the file it needs"))
        pending = list_proposals(path)
        rows.append(("ok" if not pending else "warn", f"{len(pending)} pending proposal(s)"))
        return rows
    rules = read_rules(path)
    present = sum(1 for category in CATEGORY_NAMES if category_file(path, category).is_file())
    rows.append(("ok", f"{path}: {len(rules)} rule(s) in {present} category file(s)"))
    for category in CATEGORY_NAMES:
        if not category_file(path, category).is_file():
            rows.append(("warn", f"{category}.md missing; `hx learn accept … --category {category}` creates it"))
    for stray in sorted(path.glob("*.md")):
        if category_of(stray) is None:
            held = read_file_rules(stray)
            if held:
                rows.append(("fail", f"{stray.name} holds {len(held)} rule(s) but is not a category "
                             f"({', '.join(CATEGORY_NAMES)}); move them"))
            else:
                rows.append(("warn", f"{stray.name} is not a category file; nothing reads it"))
    seen: dict[str, str] = {}
    for rule in rules:
        where = f"{Path(rule['file']).name}:{rule['lineno']}"
        if rule["key"] in seen:
            rows.append(("fail", f"{where}: {rule['key']} is also {seen[rule['key']]}"))
        seen.setdefault(rule["key"], where)
        missing = [name for name in FIELDS if not rule[name.lower()]]
        if missing:
            rows.append(("fail", f"{where}: {rule['key']} has no {', '.join(missing)}"))
    for category in CATEGORY_NAMES:
        file = category_file(path, category)
        if not file.is_file():
            continue
        for line_no, line in enumerate(file.read_text().splitlines(), 1):
            if line.startswith("**Rule") and parse_rule(line) is None:
                rows.append(("fail", f"{file.name}:{line_no}: starts like a rule but is not `**Rule <P><n>** · …`"))
    for i, a in enumerate(rules):
        for b in rules[i + 1:]:
            score = similarity(_gist(a), _gist(b))
            if score >= DUPLICATE_SCORE:
                rows.append(("warn", f"{a['key']} ({a['category']}) and {b['key']} ({b['category']}) look like one rule ({score}); merge them"))
    pending = list_proposals(path)
    rows.append(("ok" if not pending else "warn",
                 f"{len(pending)} pending proposal(s)" + ("" if not pending else " (hx learn pending)")))
    return rows


def summary(root: Path, env: dict[str, str] | None = None) -> dict:
    """What `hx doctor` and `hx compile` say in one line."""
    path = learnings_path(root, env)
    rules = read_rules(path)
    pending = list_proposals(path)
    last = max(rules, key=lambda rule: (rule["prefix"], rule["number"]))["key"] if rules else None
    counts = {category: 0 for category in CATEGORY_NAMES}
    for rule in rules:
        counts[rule["category"]] += 1
    return {"path": str(path), "exists": path.is_dir(), "rules": len(rules),
            "pending": len(pending), "last": last,
            "categories": {category: n for category, n in counts.items() if n}}


def persona_region(root: Path, env: dict[str, str] | None = None) -> str:
    """The fenced text `hx compile` distributes into every agent's persona: every rule, whole,
    category by category. The agent never reads the files — they are in its system prompt at
    every start and after every compaction, and cached with it."""
    info = summary(root, env)
    rules = read_rules(Path(info["path"]))
    count = f"{info['rules']} rule(s)" + (f", last {info['last']}" if info["last"] else "")
    head = (
        f"**Learnings: `{info['path']}`** ({count}). The central record of what tripped agents up "
        "here — a wrong assumption, a correction someone had to make, a wasted cycle — as rules: "
        "Signal (what you see), Check (what decides it), Evidence (where it happened). Every rule "
        "is below, in full; it is in your system prompt at every start and after every compaction, "
        "so never read the files. When something trips you up, propose a rule the moment you "
        "understand it, in the category of the mistake it prevents:\n"
        "`hx learn propose --category <category> --signal \"<what you saw>\" --check \"<what decides it>\" --evidence \"<where>\"`\n"
        "(or `--evidence-for <Rule>` when an existing rule already says it). Categories: "
        + ", ".join(CATEGORY_NAMES) + ". Never edit the files: a different session reviews every "
        "proposal (`hx learn accept`), so each lesson is one rule."
    )
    if not rules:
        return head
    blocks = [head]
    for category in CATEGORY_NAMES:
        held = [rule for rule in rules if rule["category"] == category]
        if not held:
            continue
        blocks.append(f"### {category} — {DEFINITIONS[category]}\n" + "\n".join(rule["line"] for rule in held))
    return "\n\n".join(blocks)


def go_line(root: Path) -> str | None:
    """The `GO <date>` line in `PARTNER.md`, or None: the fleet go-gate (docs/multi-instance.md §3)."""
    partner = Path(root) / "PARTNER.md"
    if not partner.is_file():
        return None
    found = GO_RE.search(partner.read_text())
    return found.group(0) if found else None


# --- CLI ----------------------------------------------------------------------------------------


def _print_proposal(proposal: dict, rules: list[dict]) -> None:
    near = nearest(rules, proposal) if rules else proposal.get("nearest", [])
    print(f"{proposal['id']}  by {proposal['author']}@{proposal['instance']}  {proposal['ts']}"
          f"  prefix {proposal['prefix']}  category {proposal.get('category')}"
          + (f"  evidence-for {proposal['evidence_for']}" if proposal.get("evidence_for") else ""))
    print(f"  Signal: {proposal['signal']}")
    print(f"  Check: {proposal['check']}")
    print(f"  Evidence: {proposal['evidence']}")
    if near:
        print("  nearest: " + "; ".join(f"{row['rule']} ({row['score']}) {row['signal'][:70]}" for row in near))
    else:
        print("  nearest: none")


def main(argv: list[str], root: Path, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx learn", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("propose", help="file a proposal next to the rules it resembles")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--category", help="the kind of mistake the rule prevents: " + ", ".join(CATEGORY_NAMES))
    p.add_argument("--signal", default="", help="what you see when it is about to bite")
    p.add_argument("--check", default="", help="the mechanical test that decides it")
    p.add_argument("--evidence", default="", help="where it happened: path, run, PR, date")
    p.add_argument("--evidence-for", help="an existing rule this is more evidence for (no new rule)")
    p.add_argument("--prefix", help="rule prefix letter(s); default from config/hx.json or L")
    p.add_argument("--file", help="read Category:/Signal:/Check:/Evidence: lines from this file instead")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("pending", help="the reviewer's worklist, each next to its nearest rules")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("accept", help="a different session's decision: new rule or merge")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("proposal")
    p.add_argument("--merge-into", help="append the evidence to this rule instead of a new one")
    p.add_argument("--category", help="file the rule under this category instead of the proposed one")
    p.add_argument("--signal", help="the reviewer's generalised wording")
    p.add_argument("--check", help="the reviewer's generalised wording")
    p.add_argument("--evidence", help="the reviewer's wording")
    p.add_argument("--prefix")
    p.add_argument("--why", help="one line for decisions.jsonl")

    p = sub.add_parser("reject", help="a different session's decision: not a rule")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("proposal")
    p.add_argument("--why", required=True)

    p = sub.add_parser("check", help="lint the directory: categories, shape, numbering, near-duplicates")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("list", help="the rules, or one rule in full")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("rule", nargs="?")
    p.add_argument("--prefix")
    p.add_argument("--category")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("categories", help="the fixed taxonomy: what each category holds")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("path", help="print the directory this instance writes learnings to")
    p.add_argument("--root", help=argparse.SUPPRESS)

    p = sub.add_parser("classify", help="rank categories for a fresh learning")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--signal", required=True)
    p.add_argument("--check", required=True)
    p.add_argument("--jev", action="store_true", help="Jev Choice as second opinion")

    p = sub.add_parser("repeat", help="never-twice check: is this mistake already a rule?")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--signal", required=True)

    p = sub.add_parser("invariant", help="learnings size against the prompt invariant budget")
    p.add_argument("--root", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.command == "categories":
        if args.json:
            print(json.dumps(dict(CATEGORIES), indent=2))
        else:
            for name, definition in CATEGORIES:
                print(f"{name:<12} {definition}")
        return 0

    path = learnings_path(root, env)

    if args.command == "propose":
        signal, check_text, evidence, category = args.signal, args.check, args.evidence, args.category
        if args.file:
            body = Path(args.file).read_text()
            for name, value in re.findall(r"^(Category|Signal|Check|Evidence):\s*(.*)$", body, re.M):
                if name == "Signal":
                    signal = signal or value
                elif name == "Check":
                    check_text = check_text or value
                elif name == "Category":
                    category = category or value
                else:
                    evidence = evidence or value
        proposal = propose(root, signal=signal, check=check_text, evidence=evidence, category=category,
                           prefix=args.prefix, evidence_for=args.evidence_for, env=env)
        if args.json:
            print(json.dumps(proposal, indent=2, sort_keys=True))
            return 0
        near = ", ".join(f"{row['rule']}:{row['score']}" for row in proposal["nearest"]) or "none"
        print(f"HX-LEARN-PROPOSED {proposal['id']} category={proposal['category']} nearest={near}")
        twin = next((row for row in proposal["nearest"] if row["score"] >= DUPLICATE_SCORE), None)
        if twin and not proposal.get("evidence_for"):
            print(f"hx: learn: this reads like {twin['rule']} ({twin['score']}); the reviewer may merge it "
                  f"(`hx learn accept {proposal['id']} --merge-into {twin['rule']}`)", file=sys.stderr)
        return 0

    if args.command == "pending":
        proposals = list_proposals(path)
        rules = read_rules(path)
        if args.json:
            for proposal in proposals:
                proposal["nearest"] = nearest(rules, proposal)
            print(json.dumps(proposals, indent=2, sort_keys=True))
            return 0
        if not proposals:
            print("HX-LEARN-PENDING 0")
            return 0
        for proposal in proposals:
            _print_proposal(proposal, rules)
        print(f"HX-LEARN-PENDING {len(proposals)}")
        return 0

    if args.command == "accept":
        record = accept(root, args.proposal, merge_into=args.merge_into, signal=args.signal,
                        check=args.check, evidence=args.evidence, prefix=args.prefix,
                        category=args.category, why=args.why, env=env)
        print(f"HX-LEARN {record['decision']} {record['proposal']} rule={record['rule']} category={record['category']}")
        return 0

    if args.command == "reject":
        record = reject(root, args.proposal, why=args.why, env=env)
        print(f"HX-LEARN rejected {record['proposal']}")
        return 0

    if args.command == "check":
        rows = check(root, env)
        if args.json:
            print(json.dumps([{"status": s, "detail": d} for s, d in rows], indent=2))
        else:
            for status, detail in rows:
                print(f"{status:<5} {detail}")
        failed = sum(1 for status, _ in rows if status == "fail")
        info = summary(root, env)
        print(f"HX-LEARN-CHECK rules={info['rules']} pending={info['pending']} fails={failed}")
        return 1 if failed else 0

    if args.command == "list":
        rules = read_rules(path)
        if args.rule:
            wanted = [rule for rule in rules if rule["key"] == args.rule.upper()]
            if not wanted:
                raise NotFound(f"no rule {args.rule} under {path}")
            print(json.dumps(wanted[0], indent=2, sort_keys=True) if args.json else wanted[0]["line"])
            return 0
        if args.prefix:
            rules = [rule for rule in rules if rule["prefix"] == args.prefix.upper()]
        if args.category:
            rules = [rule for rule in rules if rule["category"] == validate_category(args.category)]
        if args.json:
            print(json.dumps(rules, indent=2, sort_keys=True))
            return 0
        for rule in rules:
            print(f"{rule['key']:<6} {rule['category']:<12} {rule['signal'][:100]}")
        print(f"HX-LEARN-LIST {len(rules)} rule(s) under {path}")
        return 0

    if args.command == "path":
        print(path)
        return 0

    if args.command == "classify":
        if args.jev:
            print(json.dumps(classify_decision(root, args.signal, args.check, env=env), indent=2))
        else:
            print(json.dumps(classify(args.signal, args.check, read_rules(path)), indent=2))
        return 0

    if args.command == "repeat":
        result = repeat(root, args.signal, env=env)
        print(json.dumps(result, indent=2))
        return 1 if result["verdict"] == "repeat" else 0

    if args.command == "invariant":
        print(json.dumps(invariant_report(root, env=env), indent=2))
        return 0

    raise HxError(f"hx learn: unknown command {args.command}")  # pragma: no cover
