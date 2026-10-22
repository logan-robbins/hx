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


def _judge(changed: dict) -> dict:
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
    verdict = "split" if (len(source_files) > 5 or sum(changed.values()) > 1200) else \
        "committable" if not reasons else "finish-slice"
    return {"files": len(changed), "changed_lines": sum(changed.values()),
            "source_modules": len(source_files), "has_tests": has_tests,
            "docs_only": docs_only, "verdict": verdict, "reasons": reasons}


def commit_range(repo) -> dict:
    """Granularity of everything committed on this branch since its fork point.

    This is the sign-off measurement for eng/qa/rel workers: the unit's whole
    committed change, not the last commit. Base is the merge-base with the
    repository's principal branch, or the root commit when there is none.
    """
    repo = Path(repo)
    base = None
    for ref in ("dev", "origin/dev", "main", "master", "origin/main", "origin/master"):
        result = subprocess.run(["git", "-C", str(repo), "merge-base", "HEAD", ref],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            base = result.stdout.strip()
            break
    head0 = _git(repo, "rev-parse", "HEAD").strip()
    if base is None or base == head0:
        # Work committed directly on the principal branch: measure the tip
        # commit, the smallest honest range available.
        parent = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD~1"],
                                capture_output=True, text=True, timeout=30)
        base = parent.stdout.strip() if parent.returncode == 0 else \
            _git(repo, "rev-list", "--max-parents=0", "HEAD").strip().splitlines()[0]
    head = _git(repo, "rev-parse", "HEAD").strip()
    commits = int(_git(repo, "rev-list", "--count", f"{base}..HEAD").strip() or 0) \
        if base != head else 0
    changed = {}
    if base != head:
        for line in _git(repo, "diff", "--numstat", f"{base}..HEAD").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                added = int(parts[0]) if parts[0].isdigit() else 0
                deleted = int(parts[1]) if parts[1].isdigit() else 0
                changed[parts[2]] = added + deleted
    result = _judge(changed)
    result.update({"commits": commits, "base": base[:12]})
    return result


def granularity_gate(repo) -> dict:
    """May this unit sign off as one behaviour unit?

    `split` is refused: the Partner revises the plan into smaller units.
    `finish-slice` signs off with the reason recorded. This is how module /
    class / function granularity happens when running hx — measured from
    the repo's own distribution and the committed diff, not requested in
    prose and hoped for.
    """
    result = commit_range(repo)
    allowed = result["verdict"] != "split"
    return {"allowed": allowed, **result,
            "instruction": "sign off" if allowed else
            "split this unit: ask the Partner to revise the plan into module-level "
            "units, one behaviour each, and land them separately"}


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
    return _judge(changed)


#: The principal branches, in the order `commit_range` and `pull` try them.
BASE_REFS = ("dev", "origin/dev", "main", "master", "origin/main", "origin/master")


def pull(repo) -> dict:
    """How far behind its principal branch this worktree is.

    Read-only git on local refs (it never fetches: `git fetch` first for a remote base).
    The base is the first of `BASE_REFS` that has a merge-base with HEAD, as `commit_range`
    chooses it; `behind` is `rev-list --count HEAD..BASE`, `diverged_files` is
    `diff --name-only HEAD...BASE` (what the base changed since the fork point), and
    `base_age_s` is the age of that merge-base commit.
    """
    import time

    repo = Path(repo)
    _git(repo, "rev-parse", "HEAD")
    base = merge_base = None
    for ref in BASE_REFS:
        result = subprocess.run(["git", "-C", str(repo), "merge-base", "HEAD", ref],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            base, merge_base = ref, result.stdout.strip()
            break
    if base is None:
        raise ValidationError("discipline pull needs a principal branch: one of "
                              + ", ".join(BASE_REFS))
    behind = int(_git(repo, "rev-list", "--count", f"HEAD..{base}").strip() or 0)
    ahead = int(_git(repo, "rev-list", "--count", f"{base}..HEAD").strip() or 0)
    diverged = [line for line in _git(repo, "diff", "--name-only", f"HEAD...{base}").splitlines() if line]
    committed = int(_git(repo, "log", "-1", "--format=%ct", merge_base).strip() or 0)
    return {"base": base, "base_sha": _git(repo, "rev-parse", base).strip()[:12],
            "merge_base": merge_base[:12], "behind": behind, "ahead": ahead,
            "diverged_files": diverged, "base_age_s": max(0, int(time.time()) - committed),
            "fresh": behind == 0,
            "instruction": "fresh" if behind == 0 else
            f"pull {base}: {behind} commit(s) behind, {len(diverged)} file(s) changed there"}


# --- post-seam orientation ------------------------------------------------------------------------

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch", "edit", "write",
              "search_replace", "str_replace", "create_file"}
READ_TOOLS = {"Read", "read", "read_file", "view", "open_file"}
SEARCH_TOOLS = {"Grep", "Glob", "grep", "glob", "find", "search", "codebase_search", "file_search"}
SHELL_TOOLS = {"Bash", "bash", "exec_command", "shell", "local_shell", "run_terminal_cmd", "exec",
               "run_command"}
_SHELL_SEARCH = re.compile(
    r"(?:^|[\s;&|(])(?:\S*/)?(?:grep|rg|ag|ack|fd|find)\s"
    r"|git\s+grep\b"
    r"|(?:^|[\s;&|(])(?:\S*/)?hx\s+(?:code\s+(?:search|symbols|impact|edges|callers|type|signature)"
    r"|memory\s+search|content\s+search|recall)\b")
_SHELL_EDIT = re.compile(r"\bapply_patch\b|\bsed\s+(?:-[a-zA-Z]*i|--in-place)|\bperl\s+-[a-z]*i"
                         r"|\bcat\s+>>?\s*[\w./~-]|\btee\s+(?:-a\s+)?[\w./~-]")
_SHELL_READ = re.compile(r"(?:^|[\s;&|(])(?:cat|head|tail|less|bat|nl)\s+((?:[^|;&\s]+\s*)+)")
_HX_TASK = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?hx\s+task\b")


def tool_input(record: dict) -> dict:
    """The tool input of a `post_tool` record: its JSON excerpt, or what can be read from a
    truncated one (`command`, `file_path`, `path`, `pattern`)."""
    raw = record.get("input")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return {"command": parsed} if isinstance(parsed, str) else {}
    except json.JSONDecodeError:
        found = {}
        for key in ("command", "cmd", "file_path", "path", "pattern"):
            match = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)', raw)
            if match:
                found[key] = match.group(1).encode().decode("unicode_escape", "ignore")
        return found


def shell_command(record: dict) -> str | None:
    """The command line of a shell-type tool call, or None for any other tool."""
    if record.get("tool") not in SHELL_TOOLS:
        return None
    data = tool_input(record)
    command = data.get("command", data.get("cmd"))
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    return command if isinstance(command, str) else None


def classify_call(record: dict) -> tuple[str, list[str]]:
    """(`edit`|`search`|`read`|`task`|`other`, paths read) for one `post_tool` record."""
    tool = record.get("tool")
    data = tool_input(record)
    if tool in EDIT_TOOLS:
        return "edit", []
    if tool in SEARCH_TOOLS:
        return "search", []
    if tool in READ_TOOLS:
        path = data.get("file_path") or data.get("path")
        return "read", [path] if isinstance(path, str) else []
    command = shell_command(record)
    if command is None:
        return "other", []
    if _SHELL_EDIT.search(command):
        return "edit", []
    if _HX_TASK.search(command):
        return "task", []
    if _SHELL_SEARCH.search(command):
        return "search", []
    reads = []
    for match in _SHELL_READ.finditer(command):
        reads += [t for t in match.group(1).split() if not t.startswith("-") and ("/" in t or "." in t)]
    return ("read", reads) if reads else ("other", [])


def post_seam_counts(records: list[dict], item_id: str | None = None) -> dict:
    """Orientation after a boundary, from the `post_tool` records that follow it.

    `searches_before_first_edit` and `modules_read_before_first_write` stop at the first
    edit (the whole window when there is none); `task_reprints` counts every `hx task`.
    A read of PARTNER.md is orchestration context, not a source module read.
    """
    own = "PARTNER.md" if item_id == "partner" else None
    calls = [r for r in records if r.get("event") == "post_tool"]
    searches, modules, reprints = 0, set(), 0
    first_edit = None
    for record in calls:
        kind, paths = classify_call(record)
        if kind == "task":
            reprints += 1
        if first_edit is not None:
            continue
        if kind == "edit":
            first_edit = record.get("seq")
        elif kind == "search":
            searches += 1
        elif kind == "read":
            modules.update(p for p in paths if not (own and (p == own or p.endswith("/" + own))))
    return {"tool_calls": len(calls), "first_edit_seq": first_edit,
            "searches_before_first_edit": searches,
            "modules_read_before_first_write": len(modules), "task_reprints": reprints}


def post_seam(root, item_id: str) -> dict:
    """Partner orchestration orientation after its last context reset."""
    if item_id != "partner":
        raise ValidationError("worker context is ledger-owned; inspect its checkpoints with hx show RUN")
    from . import streams

    path = streams.main_stream(Path(root), item_id)
    records = list(streams.iter_records(path)) if path.is_file() else []
    seams = [i for i, r in enumerate(records) if r.get("event") == "seam"]
    if not seams:
        raise ValidationError(f"{item_id}: no seam record in {path.name}; nothing to measure yet")
    seam = records[seams[-1]]
    return {"id": item_id, "stream": path.name.removesuffix(".jsonl"), "seam_seq": seam.get("seq"),
            "seam_ts": seam.get("ts"), "source": seam.get("source"),
            **post_seam_counts(records[seams[-1] + 1:], item_id)}


def main(argv, root, *, env=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="hx discipline")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("profile"); p.add_argument("--repo", required=True); p.add_argument("--root", help=argparse.SUPPRESS)
    p = sub.add_parser("orient"); p.add_argument("--goal"); p.add_argument("--file"); p.add_argument("--root", help=argparse.SUPPRESS)
    p.add_argument("--post-seam", metavar="ID", help="orientation after ID's last seam, from its stream")
    p = sub.add_parser("commit", epilog="example: hx discipline commit --repo ."); p.add_argument("--repo", required=True); p.add_argument("--root", help=argparse.SUPPRESS)
    p = sub.add_parser("pull", help="how far behind the principal branch this worktree is",
                       epilog="example: hx discipline pull --repo . --expect fresh")
    p.add_argument("--repo", required=True)
    p.add_argument("--expect", choices=("fresh",), help="fresh: exit 3 when behind > 0")
    p.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    code = 0
    if args.command == "profile":
        result = profile(Path(args.repo))
    elif args.command == "orient" and args.post_seam:
        result = post_seam(root, args.post_seam)
    elif args.command == "orient":
        goal = Path(args.file).read_text() if args.file else args.goal
        if not goal:
            raise ValidationError("orient requires --goal, --file or --post-seam")
        result = orient(root, goal)
    elif args.command == "pull":
        result = pull(Path(args.repo))
        if args.expect == "fresh" and not result["fresh"]:
            code = 3
    else:
        result = commit_check(Path(args.repo))
    print(json.dumps(result, indent=2))
    if code == 3:
        import sys
        print(f"hx: discipline pull: --expect fresh: {result['instruction']}", file=sys.stderr)
    return code
