"""Command parsing and presentation for reviewed learnings."""
from __future__ import annotations

from . import learn
from .learn import (
    CATEGORIES, CATEGORY_NAMES, DUPLICATE_SCORE, HxError, NotFound, Path, accept, argparse, check, classify, classify_decision, invariant_report, is_text, json, learnings_path, list_proposals, nearest, propose, re, read_all_rules, reject, repeat, scope_label, summary, sys, validate_category, validate_role
)


def _print_proposal(proposal: dict, rules: list[dict]) -> None:
    if is_text(proposal):
        print(f"{proposal['id']}  by {proposal['author']}@{proposal['instance']}  {proposal['ts']}"
              f"  prefix {proposal['prefix']}  category {proposal.get('category')}"
              f"  scope {scope_label(proposal.get('scope'))}  text"
              f"  ({proposal.get('source')})"
              + (f"  rules so far {', '.join(proposal['rules'])}" if proposal.get("rules") else ""))
        for line in proposal["text"].splitlines():
            print(f"  | {line}" if line.strip() else "  |")
        print("  accept each lesson with --signal and --check (--keep while more remain), "
              "--merge-into a rule, or reject")
        return
    near = nearest(rules, proposal) if rules else proposal.get("nearest", [])
    print(f"{proposal['id']}  by {proposal['author']}@{proposal['instance']}  {proposal['ts']}"
          f"  prefix {proposal['prefix']}  category {proposal.get('category')}"
          f"  scope {scope_label(proposal.get('scope'))}"
          + (f"  evidence-for {proposal['evidence_for']}" if proposal.get("evidence_for") else ""))
    print(f"  Signal: {proposal['signal']}")
    print(f"  Check: {proposal['check']}")
    print(f"  Evidence: {proposal['evidence']}")
    if near:
        print("  nearest: " + "; ".join(f"{row['rule']} ({row['score']}) {row['signal'][:70]}" for row in near))
    else:
        print("  nearest: none")


def main(argv: list[str], root: Path, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx learn", description=learn.__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("propose", help="file a proposal next to the rules it resembles", epilog=(
        'example: hx learn propose --category testing --signal "tests pass locally, fail in CI" '
        '--check "run the suite with the CI env file" --evidence "PR 42, 2026-10-12"'))
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--category", help="the kind of mistake the rule prevents: " + ", ".join(CATEGORY_NAMES))
    p.add_argument("--signal", default="", help="what you see when it is about to bite")
    p.add_argument("--check", default="", help="the mechanical test that decides it")
    p.add_argument("--evidence", default="", help="where it happened: path, run, PR, date")
    p.add_argument("--evidence-for", help="an existing rule this is more evidence for (no new rule)")
    p.add_argument("--role", help="scope the rule to this role (harness.json `role`); default global")
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
    p.add_argument("--keep", action="store_true",
                   help="leave the proposal pending after this rule (a text proposal with more lessons)")
    p.add_argument("--role", help="scope the new rule to this role instead of the proposal's scope")
    p.add_argument("--global", dest="global_scope", action="store_true",
                   help="make the new rule global whatever the proposal's scope")

    p = sub.add_parser("reject", help="a different session's decision: not a rule")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("proposal")
    p.add_argument("--why", required=True)

    p = sub.add_parser("check", help="lint the directory: categories, shape, numbering, near-duplicates")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("list", help="the rules, or one rule in full",
                       epilog="example: hx learn list --category testing")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("rule", nargs="?")
    p.add_argument("--prefix")
    p.add_argument("--category")
    p.add_argument("--role", help="only the rules scoped to this role (`global` for the global ones)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("categories", help="the fixed taxonomy: what each category holds")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("path", help="print the directory this instance writes learnings to")
    p.add_argument("--root", help=argparse.SUPPRESS)

    p = sub.add_parser("classify", help="rank categories for a fresh learning", epilog=(
        'example: hx learn classify --signal "tests pass locally, fail in CI" '
        '--check "run the suite with the CI env file"'))
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--signal", required=True)
    p.add_argument("--check", required=True)
    p.add_argument("--jev", action="store_true", help="Jev Choice as second opinion")

    p = sub.add_parser("repeat", help="never-twice check: is this mistake already a rule?",
                       epilog='example: hx learn repeat --signal "tests pass locally, fail in CI"')
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
                           prefix=args.prefix, evidence_for=args.evidence_for, role=args.role, env=env)
        if args.json:
            print(json.dumps(proposal, indent=2, sort_keys=True))
            return 0
        near = ", ".join(f"{row['rule']}:{row['score']}" for row in proposal["nearest"]) or "none"
        print(f"HX-LEARN-PROPOSED {proposal['id']} category={proposal['category']} "
              f"scope={scope_label(proposal['scope'])} nearest={near}")
        twin = next((row for row in proposal["nearest"] if row["score"] >= DUPLICATE_SCORE), None)
        if twin and not proposal.get("evidence_for"):
            print(f"hx: learn: this reads like {twin['rule']} ({twin['score']}); the reviewer may merge it "
                  f"(`hx learn accept {proposal['id']} --merge-into {twin['rule']}`)", file=sys.stderr)
        return 0

    if args.command == "pending":
        proposals = list_proposals(path)
        rules = read_all_rules(root, env)
        if args.json:
            for proposal in proposals:
                proposal["nearest"] = nearest(
                    rules, {"signal": proposal["text"][:2000], "check": ""} if is_text(proposal) else proposal)
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
                        category=args.category, why=args.why, keep=args.keep, role=args.role,
                        global_scope=args.global_scope, env=env)
        print(f"HX-LEARN {record['decision']} {record['proposal']} rule={record['rule']} "
              f"category={record['category']} scope={record['scope']}" + (" kept" if args.keep else ""))
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
        rules = read_all_rules(root, env)
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
        if args.role:
            wanted = None if args.role == "global" else validate_role(args.role)
            rules = [rule for rule in rules if rule.get("role") == wanted]
        if args.json:
            print(json.dumps(rules, indent=2, sort_keys=True))
            return 0
        for rule in rules:
            print(f"{rule['key']:<6} {rule['category']:<12} {scope_label(rule.get('role')):<18} {rule['signal'][:100]}")
        print(f"HX-LEARN-LIST {len(rules)} rule(s) under {path}")
        return 0

    if args.command == "path":
        print(path)
        return 0

    if args.command == "classify":
        if args.jev:
            print(json.dumps(classify_decision(root, args.signal, args.check, env=env), indent=2))
        else:
            print(json.dumps(classify(args.signal, args.check, read_all_rules(root, env)), indent=2))
        return 0

    if args.command == "repeat":
        result = repeat(root, args.signal, env=env)
        print(json.dumps(result, indent=2))
        return 1 if result["verdict"] == "repeat" else 0

    if args.command == "invariant":
        print(json.dumps(invariant_report(root, env=env), indent=2))
        return 0

    raise HxError(f"hx learn: unknown command {args.command}")  # pragma: no cover

