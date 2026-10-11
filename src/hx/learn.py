"""`hx learn` — the central learnings file, and the reviewed way into it.

One file, `LEARNINGS.md`, holds the category rules an instance (or a whole fleet of instances)
has paid for: what tripped an agent up, written so the next agent recognises it (**Signal**),
can decide it mechanically (**Check**), and can see where it came from (**Evidence**). It is
deliberately apart from everything hx shards per agent — the goal, the work item, the memory
below the header, the episode store — because a correction one agent had to make is owed to
every agent, and a thing owed to everyone lives in one place or it is lost.

A file everyone writes grows with duplicates, so nobody writes it directly. Two steps, never in
the same session:

    hx learn propose   any agent, the moment something tripped it up: a proposal, next to the
                       rules it most resembles, into `learnings/proposals/`; the rule file is
                       not touched
    hx learn accept    a different session — the Partner, a reviewer, the human — decides:
                       a new numbered rule, or the proposal's evidence merged into the rule
                       that already says it, or `hx learn reject`

`hx learn pending` is the reviewer's worklist, `hx learn check` lints the file (numbering,
shape, near-duplicates), and `hx compile` puts the file's path and rule count into every
agent's persona so no agent has to be told to look.

Where the file is: `HX_LEARNINGS` in the environment, else `learnings` in `config/hx.json`
(a fleet points every instance at one shared file), else `$HARNESS_ROOT/LEARNINGS.md`.
Proposals, decisions and the lock live in `learnings/` next to the file.
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

#: `**Rule T12** · Signal: … · Check: … · Evidence: …` — the one shape a rule line has.
RULE_RE = re.compile(r"^\*\*Rule (?P<prefix>[A-Z]{1,3})(?P<number>\d+)\*\*\s*·\s*(?P<body>.*)$")
FIELDS = ("Signal", "Check", "Evidence")
GO_RE = re.compile(r"^GO \d{4}-\d{2}-\d{2}\b", re.M)

#: Two rules this alike are shown as a likely duplicate by `check`; a proposal this close
#: to a rule is flagged on `propose` so the proposer can merge instead.
DUPLICATE_SCORE = 0.8
#: Below this a rule is not worth listing as "nearest".
NEAREST_FLOOR = 0.25
NEAREST = 3

DEFAULT_PREFIX = "L"

HEADER = """# LEARNINGS

Category rules this instance has paid for. One line per rule, in exactly this shape:

    **Rule L1** · Signal: what you see when it is about to bite · Check: the mechanical test that decides it · Evidence: where it happened (path, run, PR, date)

The rule is invariant: no file name, message number or one-off detail outside the Evidence cell.
Nobody edits this file by hand. An agent that was tripped up runs `hx learn propose …`; a
different session reviews the proposal with `hx learn pending` and `hx learn accept` or
`hx learn reject`, so the file never grows with duplicates. `hx learn check` lints it.

## Rules
"""

_STOP = frozenset("""
a an and are as at be but by for from has have if in into is it its not of on or that the then
this to was were when which with without you your every any all one two each never always only
""".split())


# --- where things are ---------------------------------------------------------------------------


def learnings_path(root: Path, env: dict[str, str] | None = None) -> Path:
    """`HX_LEARNINGS`, else `config/hx.json` `learnings`, else `<root>/LEARNINGS.md`."""
    env = os.environ if env is None else env
    value = env.get("HX_LEARNINGS")
    if not value:
        hx_json = Path(root) / "config" / "hx.json"
        if hx_json.is_file():
            try:
                recorded = json.loads(hx_json.read_text())
            except json.JSONDecodeError:
                recorded = {}
            if isinstance(recorded, dict):
                value = recorded.get("learnings") or ""
    if value:
        return Path(os.path.expanduser(value))
    return Path(root) / "LEARNINGS.md"


def default_prefix(root: Path, env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = env.get("HX_LEARNINGS_PREFIX")
    if not value:
        hx_json = Path(root) / "config" / "hx.json"
        if hx_json.is_file():
            try:
                recorded = json.loads(hx_json.read_text())
            except json.JSONDecodeError:
                recorded = {}
            if isinstance(recorded, dict):
                value = recorded.get("learnings_prefix") or ""
    value = (value or DEFAULT_PREFIX).strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", value):
        raise ValidationError(f"learnings prefix {value!r} is not 1–3 capital letters")
    return value


def learnings_dir(path: Path) -> Path:
    return Path(path).parent / "learnings"


def _identity(root: Path, env: dict[str, str] | None) -> tuple[str, str]:
    env = os.environ if env is None else env
    return env.get("HARNESS_ID") or "human", Path(root).resolve().name


# --- the file -----------------------------------------------------------------------------------


def parse_rule(line: str) -> dict | None:
    """One rule line as `{prefix, number, key, signal, check, evidence, line}`, or None."""
    match = RULE_RE.match(line.rstrip("\n"))
    if not match:
        return None
    body = match["body"]
    fields: dict[str, str] = {}
    for name in FIELDS:
        found = re.search(
            rf"(?:^|·\s*){name}:\s*(?P<value>.*?)(?=\s*·\s*(?:{'|'.join(FIELDS)}):|$)", body, re.S
        )
        fields[name.lower()] = found["value"].strip() if found else ""
    return {
        "prefix": match["prefix"],
        "number": int(match["number"]),
        "key": f"{match['prefix']}{match['number']}",
        "line": line.rstrip("\n"),
        **fields,
    }


def read_rules(path: Path) -> list[dict]:
    if not Path(path).is_file():
        return []
    rules = []
    for index, line in enumerate(Path(path).read_text().splitlines()):
        rule = parse_rule(line)
        if rule:
            rule["lineno"] = index + 1
            rules.append(rule)
    return rules


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


def nearest(rules: list[dict], proposal: dict, limit: int = NEAREST) -> list[dict]:
    gist = _gist(proposal)
    scored = [
        {"rule": rule["key"], "score": similarity(gist, _gist(rule)), "signal": rule["signal"]}
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


def propose(root: Path, *, signal: str, check: str, evidence: str, prefix: str | None = None,
            evidence_for: str | None = None, env: dict[str, str] | None = None) -> dict:
    """Write one proposal next to the rules it most resembles. The rule file is not touched."""
    path = learnings_path(root, env)
    rules = read_rules(path)
    author, instance = _identity(root, env)
    evidence = _one_line(evidence)
    if not evidence:
        raise ValidationError("a proposal needs --evidence: where it happened (path, run, PR, date)")
    if evidence_for:
        target = next((rule for rule in rules if rule["key"] == evidence_for.upper()), None)
        if target is None:
            raise NotFound(f"--evidence-for {evidence_for}: no such rule in {path}")
        signal = signal or target["signal"]
        check = check or target["check"]
    if not _one_line(signal) or not _one_line(check):
        raise ValidationError("a proposal needs --signal (what you saw) and --check (what decides it)")
    prefix = (prefix or default_prefix(root, env)).upper()
    stamp = timestamps.now()
    proposal = {
        "id": f"{re.sub(r'[^0-9T]', '', stamp)}-{author}-{uuid.uuid4().hex[:6]}",
        "ts": stamp,
        "author": author,
        "instance": instance,
        "role": (os.environ if env is None else env).get("HX_ROLE") or None,
        "prefix": prefix,
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
           why: str | None = None, env: dict[str, str] | None = None) -> dict:
    """A different session's decision: a new numbered rule, or evidence merged into one."""
    path = learnings_path(root, env)
    reviewer, instance = _identity(root, env)
    proposal = load_proposal(path, proposal_id)
    _require_other_session(proposal, reviewer)
    stamp = timestamps.now()
    origin = f"(from {proposal['author']}@{proposal['instance']}, {proposal['ts'][:10]})"
    merge_into = (merge_into or proposal.get("evidence_for") or "").upper() or None
    with _locked(path):
        text = path.read_text() if path.is_file() else HEADER
        lines = text.split("\n")
        rules = read_rules(path)
        if merge_into:
            target = next((rule for rule in rules if rule["key"] == merge_into), None)
            if target is None:
                raise NotFound(f"--merge-into {merge_into}: no such rule in {path}")
            added = _one_line(evidence or proposal["evidence"])
            lines[target["lineno"] - 1] = f"{target['line']}; {added} {origin}"
            key, decision = target["key"], "merged"
        else:
            use_prefix = (prefix or proposal["prefix"]).upper()
            number = next_number(rules, use_prefix)
            line = render_rule(
                use_prefix, number,
                signal or proposal["signal"], check or proposal["check"],
                f"{evidence or proposal['evidence']} {origin}",
            )
            same = [rule for rule in rules if rule["prefix"] == use_prefix]
            if same:
                lines.insert(same[-1]["lineno"], line)
            else:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.insert(len(lines) - 1 if lines[-1] == "" else len(lines), line)
            key, decision = f"{use_prefix}{number}", "new"
        new_text = "\n".join(lines)
        if not new_text.endswith("\n"):
            new_text += "\n"
        store.atomic_write_text(path, new_text)
        record = {
            "ts": stamp, "proposal": proposal["id"], "author": proposal["author"],
            "reviewer": reviewer, "reviewer_instance": instance, "decision": decision,
            "rule": key, "why": _one_line(why or ""),
        }
        _record_decision(path, record)
        proposal.update({"decision": decision, "rule": key, "reviewer": reviewer, "decided": stamp})
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
            "rule": None, "why": _one_line(why),
        }
        _record_decision(path, record)
        proposal.update({"decision": "rejected", "why": _one_line(why), "reviewer": reviewer,
                         "decided": stamp})
        _move(path, proposal, "rejected")
    return record


# --- lint and summary ---------------------------------------------------------------------------


def check(root: Path, env: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """`(status, detail)` rows: `ok`, `warn`, `fail`. One rule shape, unique numbers, no twins."""
    path = learnings_path(root, env)
    rows: list[tuple[str, str]] = []
    if not path.is_file():
        rows.append(("warn", f"{path} does not exist yet; the first `hx learn accept` creates it"))
        pending = list_proposals(path)
        rows.append(("ok" if not pending else "warn", f"{len(pending)} pending proposal(s)"))
        return rows
    rules = read_rules(path)
    rows.append(("ok", f"{path}: {len(rules)} rule(s)"))
    seen: dict[str, int] = {}
    for rule in rules:
        if rule["key"] in seen:
            rows.append(("fail", f"line {rule['lineno']}: {rule['key']} is also line {seen[rule['key']]}"))
        seen.setdefault(rule["key"], rule["lineno"])
        missing = [name for name in FIELDS if not rule[name.lower()]]
        if missing:
            rows.append(("fail", f"line {rule['lineno']}: {rule['key']} has no {', '.join(missing)}"))
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        if line.startswith("**Rule") and parse_rule(line) is None:
            rows.append(("fail", f"line {line_no}: starts like a rule but is not `**Rule <P><n>** · …`"))
    for i, a in enumerate(rules):
        for b in rules[i + 1:]:
            score = similarity(_gist(a), _gist(b))
            if score >= DUPLICATE_SCORE:
                rows.append(("warn", f"{a['key']} and {b['key']} look like one rule ({score}); merge them"))
    pending = list_proposals(path)
    rows.append(("ok" if not pending else "warn",
                 f"{len(pending)} pending proposal(s)" + ("" if not pending else " (hx learn pending)")))
    return rows


def summary(root: Path, env: dict[str, str] | None = None) -> dict:
    """What `hx doctor` and `hx compile` say in one line."""
    path = learnings_path(root, env)
    rules = read_rules(path)
    pending = list_proposals(path)
    last = rules[-1]["key"] if rules else None
    return {"path": str(path), "exists": path.is_file(), "rules": len(rules),
            "pending": len(pending), "last": last}


def persona_region(root: Path, env: dict[str, str] | None = None) -> str:
    """The fenced text `hx compile` distributes into every agent's persona."""
    info = summary(root, env)
    count = f"{info['rules']} rule(s)" + (f", last {info['last']}" if info["last"] else "")
    return (
        f"**Learnings: `{info['path']}`** ({count}). The central record of what tripped agents up "
        "here — a wrong assumption, a correction someone had to make, a wasted cycle — written as "
        "rules: Signal (what you see), Check (what decides it), Evidence (where it happened). Read it "
        "before you start. When something trips you up, propose a rule the moment you understand it:\n"
        "`hx learn propose --signal \"<what you saw>\" --check \"<what decides it>\" --evidence \"<where>\"`\n"
        "(or `--evidence-for <Rule>` when an existing rule already says it). Never edit the file: a "
        "different session reviews every proposal, so it stays one rule per lesson."
    )


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
          f"  prefix {proposal['prefix']}"
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
    p.add_argument("--signal", default="", help="what you see when it is about to bite")
    p.add_argument("--check", default="", help="the mechanical test that decides it")
    p.add_argument("--evidence", default="", help="where it happened: path, run, PR, date")
    p.add_argument("--evidence-for", help="an existing rule this is more evidence for (no new rule)")
    p.add_argument("--prefix", help="rule prefix letter(s); default from config/hx.json or L")
    p.add_argument("--file", help="read Signal:/Check:/Evidence: lines from this file instead")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("pending", help="the reviewer's worklist, each next to its nearest rules")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("accept", help="a different session's decision: new rule or merge")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("proposal")
    p.add_argument("--merge-into", help="append the evidence to this rule instead of a new one")
    p.add_argument("--signal", help="the reviewer's generalised wording")
    p.add_argument("--check", help="the reviewer's generalised wording")
    p.add_argument("--evidence", help="the reviewer's wording")
    p.add_argument("--prefix")
    p.add_argument("--why", help="one line for decisions.jsonl")

    p = sub.add_parser("reject", help="a different session's decision: not a rule")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("proposal")
    p.add_argument("--why", required=True)

    p = sub.add_parser("check", help="lint the file: shape, numbering, near-duplicates")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("list", help="the rules, or one rule in full")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("rule", nargs="?")
    p.add_argument("--prefix")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("path", help="print the file this instance writes learnings to")
    p.add_argument("--root", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    path = learnings_path(root, env)

    if args.command == "propose":
        signal, check_text, evidence = args.signal, args.check, args.evidence
        if args.file:
            body = Path(args.file).read_text()
            for name, value in re.findall(r"^(Signal|Check|Evidence):\s*(.*)$", body, re.M):
                if name == "Signal":
                    signal = signal or value
                elif name == "Check":
                    check_text = check_text or value
                else:
                    evidence = evidence or value
        proposal = propose(root, signal=signal, check=check_text, evidence=evidence,
                           prefix=args.prefix, evidence_for=args.evidence_for, env=env)
        if args.json:
            print(json.dumps(proposal, indent=2, sort_keys=True))
            return 0
        near = ", ".join(f"{row['rule']}:{row['score']}" for row in proposal["nearest"]) or "none"
        print(f"HX-LEARN-PROPOSED {proposal['id']} nearest={near}")
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
                        check=args.check, evidence=args.evidence, prefix=args.prefix, why=args.why,
                        env=env)
        print(f"HX-LEARN {record['decision']} {record['proposal']} rule={record['rule']}")
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
                raise NotFound(f"no rule {args.rule} in {path}")
            print(json.dumps(wanted[0], indent=2, sort_keys=True) if args.json else wanted[0]["line"])
            return 0
        if args.prefix:
            rules = [rule for rule in rules if rule["prefix"] == args.prefix.upper()]
        if args.json:
            print(json.dumps(rules, indent=2, sort_keys=True))
            return 0
        for rule in rules:
            print(f"{rule['key']:<6} {rule['signal'][:110]}")
        print(f"HX-LEARN-LIST {len(rules)} rule(s) in {path}")
        return 0

    if args.command == "path":
        print(path)
        return 0

    raise HxError(f"hx learn: unknown command {args.command}")  # pragma: no cover
