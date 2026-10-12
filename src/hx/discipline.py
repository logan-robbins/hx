"""Repo-derived size bounds, orientation load, and committable checks.

Module size is not esoteric: every codebase has a measurable distribution,
so the bounds come from the repository itself (median / p75 / p90 of its
modules and functions) instead of an invented quota. Orientation load is
estimated before work starts; a goal whose discovery surface is broad is a
decomposition problem for the Partner, not diligence expected of a worker.
"""
from __future__ import annotations

import ast
import json
import re
import statistics
import subprocess
from pathlib import Path

from .errors import ValidationError

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
FILE_RE = re.compile(r"[\w][\w./-]*\.(?:py|ts|tsx|js|jsx|go|rs|md|json|yaml|yml)")
STOP = {"the", "a", "an", "and", "or", "to", "in", "of", "for", "with", "fix", "add"}


def _python_files(repo: Path) -> list[Path]:
    base = repo / "src" if (repo / "src").is_dir() else repo
    files = []
    for path in sorted(base.rglob("*.py")):
        if any(part in SKIP_DIRS or part.startswith(".") for part in path.parts):
            continue
        files.append(path)
    return files


def _pct(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def profile(repo) -> dict:
    """Size distribution of the repo's own modules; bounds derived from it."""
    repo = Path(repo)
    files = _python_files(repo)
    if not files:
        raise ValidationError("discipline profile needs Python modules under src/ or the repo root")
    sizes, function_lengths = [], []
    for path in files:
        text = path.read_text(errors="replace")
        sizes.append((len(text.splitlines()), path.name))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
                function_lengths.append(node.end_lineno - node.lineno + 1)
    line_counts = [n for n, _ in sizes]
    # Robust to outliers (one huge file must not raise its own bound):
    # hard = median + 3 x the median-to-p75 spread, which tracks p90 in
    # healthy distributions without letting the largest file set the norm.
    median = int(statistics.median(line_counts))
    hard = int(median + 3 * max(1, _pct(line_counts, 0.75) - median))
    return {
        "modules": len(files),
        "median_lines": int(statistics.median(line_counts)),
        "module_soft": _pct(line_counts, 0.75),
        "module_hard": hard,
        "function_p90": _pct(function_lengths, 0.90) if function_lengths else None,
        "oversized": sorted(name for n, name in sizes if n > hard),
        "note": "New modules should land near the median and below module_soft; "
                "above module_hard is a split candidate, per this repo's own distribution.",
    }


def orient(root, goal: str) -> dict:
    """Estimate discovery load before an assignment starts.

    Signals: files the goal names, candidate files the code index finds for
    its salient terms, and goal size. Broad orientation means the model
    will spend its start searching instead of editing — the Partner splits
    the goal or supplies a brief; the worker is not expected to hero through.
    """
    if not isinstance(goal, str) or not goal.strip():
        raise ValidationError("orient requires a nonempty goal")
    named = sorted(set(FILE_RE.findall(goal)))
    terms = [w for w in re.findall(r"[A-Za-z][\w-]{3,}", goal.lower())
             if w not in STOP][:6]
    candidates = set()
    indexed = False
    if terms:
        try:
            from . import code_index
            if code_index.db_path(Path(root)).exists():
                indexed = True
                for hit in code_index.search(Path(root), " ".join(terms[:3]), limit=10):
                    candidates.add(hit["path"])
        except Exception:  # noqa: BLE001 — orientation degrades to lexical signals
            indexed = False
    signals = {"named_files": len(named), "candidate_files": len(candidates),
               "goal_bytes": len(goal.encode()), "index_consulted": indexed}
    broad = len(named) > 3 or len(candidates) > 8 or (
        len(goal.encode()) > 1200 and len(named) > 1)
    normal = len(named) > 1 or len(candidates) > 3 or len(goal.encode()) > 600
    return {"orientation": "broad" if broad else "normal" if normal else "easy",
            "signals": signals,
            "recommendation": "split" if broad else "proceed",
            "note": "Broad orientation: split the goal or attach a map/code brief "
                    "naming the starting files and symbols before dispatch."}


def _git(repo: Path, *args) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValidationError("commit check requires a Git worktree")
    return result.stdout


def commit_check(repo) -> dict:
    """Is the current diff one committable behaviour unit?

    Committable: few modules, tests travelling with the change, size inside
    the repo's own norms. Many modules or no tests means split or finish the
    slice — judged from the diff, not from the author's claim.
    """
    repo = Path(repo)
    changed = {}
    for line in _git(repo, "diff", "--numstat", "HEAD").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            added = int(parts[0]) if parts[0].isdigit() else 0
            deleted = int(parts[1]) if parts[1].isdigit() else 0
            changed[parts[2]] = added + deleted
    for line in _git(repo, "status", "--porcelain").splitlines():
        path = line[3:].strip()
        if line.startswith("??") and path not in changed:
            try:
                changed[path] = len((repo / path).read_text(errors="replace").splitlines())
            except OSError:
                changed[path] = 0
    source_files = [p for p in changed if "/tests/" not in p and not p.startswith("tests/")
                    and "test_" not in Path(p).name]
    has_tests = any("/tests/" in p or p.startswith("tests/") or "test_" in Path(p).name
                    for p in changed)
    docs_only = all(p.endswith((".md", ".json", ".toml")) for p in changed) if changed else False
    reasons = []
    if len(source_files) > 5:
        reasons.append(f"touches {len(source_files)} source modules; split by behaviour unit")
    if changed and not has_tests and not docs_only:
        reasons.append("no test file travels with the change")
    if sum(changed.values()) > 1200:
        reasons.append(f"{sum(changed.values())} changed lines; land it in slices")
    return {"files": len(changed), "changed_lines": sum(changed.values()),
            "source_modules": len(source_files), "has_tests": has_tests,
            "docs_only": docs_only,
            "verdict": "split" if reasons and len(source_files) > 5 or
                       (reasons and sum(changed.values()) > 1200) else
                       "committable" if not reasons else "finish-slice",
            "reasons": reasons}


def main(argv, root, *, env=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="hx discipline")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("profile"); p.add_argument("--repo", required=True); p.add_argument("--root", help=argparse.SUPPRESS)
    p = sub.add_parser("orient"); p.add_argument("--goal"); p.add_argument("--file"); p.add_argument("--root", help=argparse.SUPPRESS)
    p = sub.add_parser("commit"); p.add_argument("--repo", required=True); p.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "profile":
        result = profile(Path(args.repo))
    elif args.command == "orient":
        goal = Path(args.file).read_text() if args.file else args.goal
        if not goal:
            raise ValidationError("orient requires --goal or --file")
        result = orient(root, goal)
    else:
        result = commit_check(Path(args.repo))
    print(json.dumps(result, indent=2))
    return 0
