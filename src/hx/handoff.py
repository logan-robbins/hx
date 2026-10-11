"""`hx handoff` — what a handoff carries before another session acts on it, mechanically.

A handoff is the document one session leaves for another (a reviewer, a release engineer, the
human) saying what was published and what it proves. Everything it quotes from the remote —
PR heads, merge states, check results — rots the moment anyone pushes again, and everything it
proves locally is bound to one tree. So the contract (docs/multi-instance.md §6) is:

  - it opens with its authority (what it is and is not) and the time its facts were captured;
  - the remote facts come from one query made after the last push (`final-root-check.json`,
    written by `hx handoff capture`), earlier heads are labelled historical, a blocked state
    names its cause;
  - a delivery status line says the one true state: `LOCAL_GATE_PASSED`, `CI_PENDING`,
    `CI_FAILED` or `CI_GREEN`, and it must agree with the captured checks — a push starts
    remote CI, it does not pass it;
  - evidence from one tree is carried to another only with a recorded product diff
    (`publication.json` from `hx handoff pushed`), and an empty diff says "not rerun";
  - open findings stay listed under an `Open …` heading until a cited line closes them;
  - the directory is sealed by `EVIDENCE-SHA256SUMS.txt` (`hx handoff sums`), written last.

    hx handoff check   DIR [--handoff NAME] [--live [--repo OWNER/REPO]]
    hx handoff capture DIR --repo OWNER/REPO --pr N [N …]
    hx handoff pushed  DIR --repo-dir WT --head SHA [--evidenced SHA] [--product P …] [--exclude P …]
    hx handoff sums    DIR
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
PR_ROW_RE = re.compile(r"^\|\s*\[?#(\d+)")
STATUS_RE = re.compile(r"^\s*(?:\*\*)?Delivery status(?:\*\*)?:\s*`?(?P<status>[A-Z_]+)`?", re.M)
STATUSES = ("LOCAL_GATE_PASSED", "CI_PENDING", "CI_FAILED", "CI_GREEN", "NOT_PUBLISHED")
GH_FIELDS = (
    "number,state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefOid,baseRefOid,"
    "statusCheckRollup"
)
SUMS = "EVIDENCE-SHA256SUMS.txt"


def parse_iso(value: str) -> dt.datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def gh_json(args: list[str]) -> object:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"gh {' '.join(args)} failed")
    return json.loads(result.stdout)


def publication_records(directory: str) -> list[tuple[str, dict | None, str | None]]:
    found = set(glob.glob(os.path.join(directory, "**", "publication.json"), recursive=True))
    for name in ("PUBLICATION-RESULT.json", "POST-PUSH-PRS.json"):
        for base in (directory, os.path.dirname(os.path.abspath(directory))):
            candidate = os.path.join(base, name)
            if os.path.isfile(candidate):
                found.add(candidate)
    out: list[tuple[str, dict | None, str | None]] = []
    for path in sorted(found):
        try:
            record = json.load(open(path, encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — any unreadable record is reported, not raised
            out.append((path, None, f"unreadable: {exc}"))
            continue
        stamp = (record.get("pushed_at") or record.get("timestamp") or record.get("capturedAt")
                 or record.get("captured_at"))
        out.append((path, record, stamp))
    return out


def check_summary(pr: dict) -> dict:
    """`{total, success, pending, failed}` over a captured `statusCheckRollup`."""
    counts = {"total": 0, "success": 0, "pending": 0, "failed": 0}
    for item in pr.get("statusCheckRollup") or []:
        counts["total"] += 1
        state = (item.get("conclusion") or item.get("state") or "").upper()
        status = (item.get("status") or "").upper()
        if status and status != "COMPLETED" and not state:
            counts["pending"] += 1
        elif state in ("SUCCESS", "SKIPPED", "NEUTRAL"):
            counts["success"] += 1
        elif state in ("PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", ""):
            counts["pending"] += 1
        else:
            counts["failed"] += 1
    return counts


def cmd_check(args: argparse.Namespace) -> int:
    directory = args.dir
    fails: list[str] = []
    infos: list[str] = []
    name = args.handoff
    if not name:
        for candidate in ("REVIEW-HANDOFF.md", "HANDOFF.md"):
            if os.path.isfile(os.path.join(directory, candidate)):
                name = candidate
                break
    if not name:
        print("FAIL no REVIEW-HANDOFF.md or HANDOFF.md in", directory)
        return 1
    text = open(os.path.join(directory, name), encoding="utf-8").read()
    lines = text.splitlines()
    head_block = "\n".join(lines[:8])
    if not re.search(r"not merge approval|not an approval|^Authority:", head_block, re.I | re.M):
        fails.append("S90 authority: the first paragraph must say what the handoff is and is not "
                     "(e.g. 'local evidence index, not merge approval' or an 'Authority:' line)")
    if not ISO_RE.search(head_block):
        fails.append("S90 capture time: no ISO-8601 timestamp in the first paragraph")
    status_match = STATUS_RE.search(text)
    status = status_match["status"] if status_match else None
    if status not in STATUSES:
        fails.append("S93 no `Delivery status: <" + "|".join(STATUSES) + ">` line: a push starts CI, "
                     "it does not pass it, and the handoff says which state this is")
    frc_path = os.path.join(directory, "final-root-check.json")
    if not os.path.isfile(frc_path):
        fails.append("S90 final-root-check.json missing (`hx handoff capture …` after the last push)")
    else:
        frc = json.load(open(frc_path, encoding="utf-8"))
        captured = parse_iso(frc["captured_at"])
        infos.append(f"captured_at {frc['captured_at']}")
        records = publication_records(directory)
        if not records:
            fails.append("S90/S91 no publication record (publication.json / PUBLICATION-RESULT.json): "
                         "the push time cannot be ordered against the capture")
        last_push = None
        for path, record, stamp in records:
            if record is None:
                fails.append(f"publication record {path} {stamp}")
                continue
            if stamp:
                when = parse_iso(stamp)
                last_push = when if last_push is None or when > last_push else last_push
        if last_push is not None:
            if captured <= last_push:
                fails.append(f"S90 final-root-check captured {captured.isoformat()} is not later than "
                             f"the last push {last_push.isoformat()}")
            else:
                infos.append(f"last push {last_push.isoformat()} < capture")
        prs = frc.get("prs") or []
        if isinstance(prs, dict):
            prs = [dict(value, number=int(key)) for key, value in prs.items()]
        heads = {int(pr["number"]): pr for pr in prs}
        if not heads:
            fails.append("S90 final-root-check.json has no prs")
        seen: set[int] = set()
        for index, line in enumerate(lines):
            match = PR_ROW_RE.match(line)
            if not match:
                continue
            number = int(match.group(1))
            if number not in heads:
                continue
            seen.add(number)
            row_shas = set(SHA_RE.findall(line))
            head = heads[number].get("headRefOid") or heads[number].get("head")
            if head not in row_shas:
                fails.append(f"S90 PR #{number} row does not carry the captured head {head[:12]}")
            others = row_shas - {head}
            if others:
                j = index
                while j > 0 and lines[j].startswith("|"):
                    j -= 1
                header = lines[j + 1] if j + 1 < len(lines) else ""
                if "historical" not in header.lower() and "historical" not in line.lower():
                    fails.append(f"S90 PR #{number} row quotes {', '.join(s[:12] for s in sorted(others))} "
                                 "without the table header or row saying 'historical'")
            merge_state = heads[number].get("mergeStateStatus") or heads[number].get("merge_state_status")
            decision = (heads[number].get("reviewDecision") or heads[number].get("review_decision") or "")
            if merge_state == "BLOCKED":
                cause_ok = (decision and decision in line) or re.search(
                    r"failing (required )?context|required context .* (FAILURE|FAILED)", line)
                if not cause_ok and not (decision and decision in text):
                    fails.append(f"S90 PR #{number} is BLOCKED; the handoff must name the cause "
                                 f"({decision or 'failing required context'})")
        missing = sorted(set(heads) - seen)
        if missing:
            fails.append("S90 captured PRs absent from the handoff rows: "
                         + ", ".join(f"#{n}" for n in missing))
        # S93: the delivery status agrees with the captured checks.
        totals = {"total": 0, "success": 0, "pending": 0, "failed": 0}
        for pr in heads.values():
            for key, value in check_summary(pr).items():
                totals[key] += value
        infos.append(f"captured checks: {totals['success']} success, {totals['pending']} pending, "
                     f"{totals['failed']} failed")
        if status == "CI_GREEN":
            if totals["failed"] or totals["pending"]:
                fails.append(f"S93 Delivery status CI_GREEN but the capture has {totals['failed']} failed "
                             f"and {totals['pending']} pending check(s)")
            elif totals["total"] == 0:
                fails.append("S93 Delivery status CI_GREEN with no captured checks: nothing proves it")
        elif status in ("LOCAL_GATE_PASSED", "CI_PENDING") and totals["failed"]:
            fails.append(f"S93 Delivery status {status} but the capture has {totals['failed']} failed "
                         "check(s): say CI_FAILED and name them")
        if args.live:
            repo = args.repo or frc.get("repository")
            if not repo and os.path.isfile(os.path.join(directory, "checkpoint.json")):
                repo = json.load(open(os.path.join(directory, "checkpoint.json"))).get("repository")
            if not repo:
                fails.append("--live needs --repo OWNER/REPO (or a repository in the capture)")
            else:
                for number, pr in heads.items():
                    try:
                        now = gh_json(["pr", "view", str(number), "--repo", repo, "--json",
                                       "headRefOid,mergeStateStatus,reviewDecision,state"])
                    except RuntimeError as exc:
                        fails.append(f"live #{number}: {exc}")
                        continue
                    for key in ("headRefOid", "mergeStateStatus", "reviewDecision", "state"):
                        if pr.get(key) != now.get(key):
                            fails.append(f"live #{number} {key}: captured {pr.get(key)} now {now.get(key)} "
                                         "— recapture before anyone acts")
                infos.append(f"live comparison against {repo} done")
    if not re.search(r"^#{2,4} .*\bopen\b", text, re.I | re.M):
        fails.append("S91 no 'Open …' section: every open finding is listed by number until a cited "
                     "line closes it")
    if re.search(r"test[- ]only|test files only|still applies|carries? (over|forward)", text, re.I):
        with_delta = [(path, record) for path, record, _ in publication_records(directory)
                      if isinstance(record, dict) and "production_delta" in record]
        if not with_delta:
            fails.append("S91 carry-forward claim without a publication record carrying production_delta "
                         "(`hx handoff pushed …`)")
        else:
            for path, record in with_delta:
                delta = record.get("production_delta")
                if not isinstance(delta, list):
                    fails.append(f"S91 {path}: production_delta must be a list of paths")
                elif delta == [] and not re.search(r"not rerun|was not re-?run|not re-executed", text, re.I):
                    fails.append(f"S91 {path}: empty production_delta but the handoff never says the later "
                                 "tree was not rerun")
                elif delta:
                    infos.append(f"{os.path.relpath(path, directory)}: production_delta has {len(delta)} "
                                 "path(s) — the live layer must be rerun on that tree")
    sums = os.path.join(directory, SUMS)
    if not os.path.isfile(sums):
        fails.append(f"S90 {SUMS} missing (`hx handoff sums DIR`, last)")
    else:
        bad = verify_sums(directory)
        if bad:
            fails.append(f"S90 {SUMS} does not verify: {bad[0]}")
        else:
            infos.append("checksums verify")
    for info in infos:
        print("INFO", info)
    for fail in fails:
        print("FAIL", fail)
    print("RESULT:", "PASS" if not fails else "FAIL", f"({name})")
    return 0 if not fails else 1


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sums(directory: str) -> list[str]:
    """Every line of the sums file checked in Python; `[]` when all verify."""
    bad = []
    for line in open(os.path.join(directory, SUMS), encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip():
            continue
        expected, _, rel = line.partition("  ")
        path = os.path.join(directory, rel)
        if not os.path.isfile(path):
            bad.append(f"{rel}: missing")
        elif _sha256(path) != expected:
            bad.append(f"{rel}: changed")
    return bad


def cmd_capture(args: argparse.Namespace) -> int:
    prs = [gh_json(["pr", "view", str(n), "--repo", args.repo, "--json", GH_FIELDS]) for n in args.pr]
    out = {"captured_at": dt.datetime.now(dt.timezone.utc).isoformat(), "repository": args.repo, "prs": prs}
    os.makedirs(args.dir, exist_ok=True)
    path = os.path.join(args.dir, "final-root-check.json")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=2)
    for pr in prs:
        counts = check_summary(pr)
        print(f"#{pr['number']} head {pr['headRefOid'][:12]} {pr['state']} {pr['mergeStateStatus']} "
              f"{pr['reviewDecision'] or '-'} checks {counts}")
    print("wrote", path)
    return 0


def cmd_pushed(args: argparse.Namespace) -> int:
    product = args.product or ["app"]
    exclude = args.exclude or ["app/tests"]
    delta: list[str] = []
    if args.evidenced:
        result = subprocess.run(
            ["git", "-C", args.repo_dir, "diff", "--name-only", args.evidenced, args.head, "--", *product],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print("FAIL", result.stderr.strip())
            return 1
        for path in result.stdout.split():
            if not any(path == e or path.startswith(e.rstrip("/") + "/") for e in exclude):
                delta.append(path)
    record = {
        "pushed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "head": args.head,
        "evidenced": args.evidenced,
        "product_paths": product,
        "excluded": exclude,
    }
    if args.evidenced:
        record["production_delta"] = delta
    os.makedirs(args.dir, exist_ok=True)
    path = os.path.join(args.dir, "publication.json")
    json.dump(record, open(path, "w", encoding="utf-8"), indent=2)
    print("wrote", path, "production_delta:",
          "n/a (no --evidenced)" if not args.evidenced else (delta or "[] (test-only; say 'not rerun' in the handoff)"))
    return 0


def cmd_sums(args: argparse.Namespace) -> int:
    directory = os.path.abspath(args.dir)
    rows = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "git-view.git"))
        for file in sorted(files):
            if file == SUMS:
                continue
            path = os.path.join(root, file)
            if os.path.islink(path):
                continue
            rows.append(f"{_sha256(path)}  ./{os.path.relpath(path, directory)}")
    with open(os.path.join(directory, SUMS), "w", encoding="utf-8") as out:
        out.write("\n".join(rows) + "\n")
    print("wrote", os.path.join(directory, SUMS), f"({len(rows)} files)")
    return 0


def main(argv: list[str], root: Path | None = None, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx handoff", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("dir")
    p.add_argument("--handoff")
    p.add_argument("--live", action="store_true")
    p.add_argument("--repo")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("capture")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("dir")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", nargs="+", type=int, required=True)
    p.set_defaults(fn=cmd_capture)
    p = sub.add_parser("pushed")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("dir")
    p.add_argument("--repo-dir", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--evidenced")
    p.add_argument("--product", nargs="*")
    p.add_argument("--exclude", nargs="*")
    p.set_defaults(fn=cmd_pushed)
    p = sub.add_parser("sums")
    p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("dir")
    p.set_defaults(fn=cmd_sums)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except RuntimeError as exc:
        print("FAIL", exc, file=sys.stderr)
        return 1
