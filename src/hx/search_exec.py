"""Isolated execution: run a snippet, return only what matters.

Think-in-Code (context-mode ``ctx_execute`` equivalent): code runs in a fresh
temp dir as a subprocess, and only its stdout comes back — bounded. Large
output is indexed via ``hx.content_store`` (imported lazily; until that store
lands, sections are chunked in memory) so the caller gets the sections that
match an intent, or a pointer, never the raw dump. This is context isolation,
not a security boundary: hx runs with the caller's full permissions.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from .errors import ValidationError
from .file_inspection import require_non_file_code

SMALL = 5_000
HUGE = 102_400
MAX_RETURN = 6_000
_LANGUAGES = {"python": ".py", "javascript": ".js", "typescript": ".js", "shell": ".sh"}


def _run(language, code, *, cwd=None, timeout=30, env_extra=None):
    """Run code, return (exit_code, stdout_bytes, stderr_bytes, timed_out)."""
    lang = language
    if lang not in _LANGUAGES:
        raise ValueError(f"unsupported language: {language}")
    if lang in ("javascript", "typescript") and not shutil.which("node"):
        raise ValueError("node is not available for javascript/typescript execution")
    tmp = Path(tempfile.mkdtemp(prefix="hx-exec-"))
    script = tmp / f"script{_LANGUAGES[lang]}"
    script.write_text(code, encoding="utf-8")
    cmd = {"python": [sys.executable, str(script)],
           "javascript": ["node", str(script)],
           "typescript": ["node", str(script)],
           "shell": ["bash", str(script)]}[lang]
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(cmd, cwd=str(cwd or tmp), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err, False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, err = proc.communicate()
        return 124, out, err, True


def _chunk_sections(text: str, lines_per: int = 20) -> list[str]:
    lines = text.splitlines()
    if not lines:
        return []
    return ["\n".join(lines[i:i + lines_per]) for i in range(0, len(lines), lines_per)]


def _match_sections(text: str, query: str, limit: int = 5) -> list[str]:
    terms = [t.lower() for t in query.split() if t]
    if not terms:
        return []
    scored = []
    for idx, section in enumerate(_chunk_sections(text)):
        low = section.lower()
        score = sum(low.count(t) for t in terms)
        if score:
            scored.append((-score, idx, section))
    scored.sort()
    return [s[:1_000] for _, _, s in scored[:limit]]


def _hit_text(hit) -> str:
    if isinstance(hit, str):
        return hit
    if isinstance(hit, dict):
        for key in ("text", "document", "section", "content", "snippet"):
            if hit.get(key):
                return str(hit[key])
    return str(hit)


def _index_and_search(root, output, *, source, query):
    """Index output, return (indexed, n_sections, matching_sections)."""
    try:
        from hx.content_store import index_content, search  # lazy: avoids cycles
    except Exception:
        sections = _chunk_sections(output)
        return True, max(len(sections), 1), (_match_sections(output, query) if query else [])
    try:
        res = index_content(root, output, source=source)
        if isinstance(res, int):
            n = res
        elif isinstance(res, dict):
            n = int(res.get("sections") or res.get("count") or res.get("chunks") or 0)
        else:
            n = 0
        if not n:
            n = max(len(_chunk_sections(output)), 1)
    except Exception:
        sections = _chunk_sections(output)
        return True, max(len(sections), 1), (_match_sections(output, query) if query else [])
    if not query:
        return True, n, []
    try:
        hits = search(root, query)
    except TypeError:
        try:
            hits = search(root, query, k=5)
        except TypeError:
            hits = search(root, query, 5)
    except Exception:
        return True, n, _match_sections(output, query)
    return True, n, [_hit_text(h)[:1_000] for h in list(hits or [])[:5]]


def _cap(text: str) -> str:
    if len(text) > MAX_RETURN:
        return text[:MAX_RETURN - 100] + "\n[truncated]"
    return text


def _error(message: str) -> dict:
    return {"exit_code": None, "stdout_returned": "", "bytes_produced": 0,
            "indexed": False, "error": message}


def execute(root, language, code, *, cwd=None, timeout=30, intent=None, background=False) -> dict:
    lang = language
    if lang not in _LANGUAGES:
        return _error(f"unsupported language: {language}")
    if lang in ("javascript", "typescript") and not shutil.which("node"):
        return _error("node is not available for javascript/typescript execution")
    try:
        require_non_file_code(lang, code)
    except ValidationError as exc:
        return _error(str(exc))
    if background:
        tmp = Path(tempfile.mkdtemp(prefix="hx-exec-"))
        script = tmp / f"script{_LANGUAGES[lang]}"
        script.write_text(code, encoding="utf-8")
        cmd = {"python": [sys.executable, str(script)], "javascript": ["node", str(script)],
               "typescript": ["node", str(script)], "shell": ["bash", str(script)]}[lang]
        output_path = tmp / "output.txt"
        with open(output_path, "wb") as fh:
            proc = subprocess.Popen(cmd, cwd=str(cwd or tmp), stdout=fh,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        return {"pid": proc.pid, "output_path": str(output_path), "exit_code": None,
                "stdout_returned": "", "bytes_produced": 0, "indexed": False,
                "background": True, "language": lang}
    try:
        exit_code, out_b, err_b, timed_out = _run(lang, code, cwd=cwd, timeout=timeout)
    except ValueError as exc:
        return _error(str(exc))
    produced = len(out_b)
    text = out_b.decode("utf-8", errors="replace")
    indexed = False
    truncated = False
    if produced <= SMALL:
        returned = text
    elif produced > HUGE:
        indexed, n, _ = _index_and_search(root, text, source=f"execute:{lang}", query=None)
        q = intent or lang
        returned = (f"Indexed {n} sections; hx search \"{q}\" to retrieve them.\n"
                    f"Output too large to return raw ({produced} bytes).")
        truncated = True
    elif intent:
        indexed, n, sections = _index_and_search(root, text, source=f"execute:{lang}", query=intent)
        body = "\n---\n".join(sections)
        pointer = f"Indexed {n} sections; hx search \"{intent}\" for the full output."
        returned = _cap(f"{body}\n{pointer}" if body else pointer)
        truncated = True
    else:
        returned = out_b[:SMALL].decode("utf-8", errors="ignore") + (
            f"\n[truncated: produced {produced} bytes; re-run with intent=... "
            f"or hx search to retrieve the rest]")
        truncated = True
    result = {"exit_code": exit_code, "stdout_returned": _cap(returned),
              "bytes_produced": produced, "indexed": indexed, "language": lang,
              "timed_out": timed_out, "truncated": truncated}
    if exit_code not in (0, None):
        err_text = err_b.decode("utf-8", errors="replace")
        if err_text:
            result["stderr"] = err_text[:2_000]
    return result


def execute_file(root, path, language, code, *, timeout=30, intent=None) -> dict:
    target = Path(path)
    if not target.is_file():
        return _error(f"file not found: {path}")
    lang = language
    try:
        require_non_file_code(lang, code, bound_file=True)
    except ValidationError as exc:
        return _error(str(exc))
    # The file's bytes reach the script only via HX_FILE_CONTENT_PATH; the
    # wrapper binds FILE_CONTENT before user code. Recognized raw dumps are
    # refused above; derived, non-file output keeps the bounded result path.
    prelude = {
        "python": "import os as _os, pathlib as _pl\n"
                  "FILE_CONTENT = _pl.Path(_os.environ['HX_FILE_CONTENT_PATH'])"
                  ".read_text(errors='replace')\n",
        "javascript": "const FILE_CONTENT = require('fs')"
                      ".readFileSync(process.env.HX_FILE_CONTENT_PATH, 'utf8');\n",
        "typescript": "const FILE_CONTENT = require('fs')"
                      ".readFileSync(process.env.HX_FILE_CONTENT_PATH, 'utf8');\n",
        "shell": 'FILE_CONTENT=$(cat "$HX_FILE_CONTENT_PATH")\n',
    }.get(lang)
    if prelude is None:
        return _error(f"unsupported language: {language}")
    try:
        exit_code, out_b, err_b, timed_out = _run(
            lang, prelude + code, timeout=timeout,
            env_extra={"HX_FILE_CONTENT_PATH": str(target.resolve())})
    except ValueError as exc:
        return _error(str(exc))
    produced = len(out_b)
    text = out_b.decode("utf-8", errors="replace")
    indexed = False
    truncated = False
    if produced <= SMALL:
        returned = text
    elif produced > HUGE:
        indexed, n, _ = _index_and_search(root, text, source=f"execute:{lang}", query=None)
        returned = (f"Indexed {n} sections; hx search \"{intent or lang}\" to retrieve them.\n"
                    f"Output too large to return raw ({produced} bytes).")
        truncated = True
    elif intent:
        indexed, n, sections = _index_and_search(root, text, source=f"execute:{lang}", query=intent)
        body = "\n---\n".join(sections)
        pointer = f"Indexed {n} sections; hx search \"{intent}\" for the full output."
        returned = _cap(f"{body}\n{pointer}" if body else pointer)
        truncated = True
    else:
        returned = out_b[:SMALL].decode("utf-8", errors="ignore") + (
            f"\n[truncated: produced {produced} bytes; re-run with intent=... "
            f"or hx search to retrieve the rest]")
        truncated = True
    result = {"exit_code": exit_code, "stdout_returned": _cap(returned),
              "bytes_produced": produced, "indexed": indexed, "language": lang,
              "timed_out": timed_out, "truncated": truncated}
    if exit_code not in (0, None):
        err_text = err_b.decode("utf-8", errors="replace")
        if err_text:
            result["stderr"] = err_text[:2_000]
    return result


def batch_execute(root, commands, *, queries=None, concurrency=1, timeout=60) -> dict:
    def one(cmd):
        label = str(cmd.get("label") or cmd.get("command", "")[:40] or "cmd")
        try:
            require_non_file_code('shell', str(cmd.get("command", "")))
            exit_code, out_b, err_b, timed_out = _run(
                "shell", str(cmd.get("command", "")), timeout=timeout)
        except (ValueError, ValidationError) as exc:
            return {"label": label, "exit_code": None, "summary": str(exc),
                    "indexed": False, "error": str(exc)}, (label, "")
        text = out_b.decode("utf-8", errors="replace")
        indexed, _, _ = _index_and_search(root, text, source=f"batch:{label}", query=None) \
            if text else (False, 0, [])
        summary = (text.strip() or err_b.decode("utf-8", errors="replace").strip())[:500]
        return {"label": label, "exit_code": exit_code, "summary": summary,
                "indexed": indexed, "timed_out": timed_out}, (label, text)

    if concurrency and concurrency > 1 and len(commands) > 1:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(int(concurrency), 8)) as pool:
            pairs = list(pool.map(one, commands))
    else:
        pairs = [one(c) for c in commands]
    search_map: dict = {}
    if queries:
        for query in queries:
            found = []
            for label, text in (p[1] for p in pairs):
                for section in _match_sections(text, query, limit=3):
                    found.append({"label": label, "section": section})
            search_map[query] = found[:10]
    return {"results": [p[0] for p in pairs], "search": search_map}


def main(argv, root, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx exec", add_help=True,
                                     epilog='example: hx exec --language python --code "print(1)"')
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("exec", help="run code in isolated execution",
                        epilog='example: hx exec --language python --code "print(1)"')
    ex.add_argument("--language", required=True)
    ex.add_argument("--code", required=True)
    ex.add_argument("--intent", default=None)
    ex.add_argument("--timeout", type=int, default=30)
    ex.add_argument("--root", help=argparse.SUPPRESS)

    ef = sub.add_parser("file", help="run code against FILE_CONTENT",
                        epilog='example: hx exec file app.log --language python --code "print(len(FILE_CONTENT))"')
    ef.add_argument("path")
    ef.add_argument("--language", required=True)
    ef.add_argument("--code", required=True)
    ef.add_argument("--intent", default=None)
    ef.add_argument("--timeout", type=int, default=30)
    ef.add_argument("--root", help=argparse.SUPPRESS)

    bt = sub.add_parser("batch", help="run shell commands and search outputs",
                        epilog="example: hx exec batch --file commands.json --queries timeout,refused")
    bt.add_argument("--file", required=True)
    bt.add_argument("--queries", default=None, help="comma-separated queries")
    bt.add_argument("--concurrency", type=int, default=1)
    bt.add_argument("--timeout", type=int, default=60)
    bt.add_argument("--root", help=argparse.SUPPRESS)

    argv = list(argv)
    if argv and argv[0] not in {"file", "batch", "-h", "--help"}:
        argv = ["exec", *argv]  # `hx exec --language ...` is the bare form
    args = parser.parse_args(argv)
    root = Path(root)
    if args.command == "exec":
        result = execute(root, args.language, args.code,
                         timeout=args.timeout, intent=args.intent)
    elif args.command == "file":
        result = execute_file(root, args.path, args.language, args.code,
                              timeout=args.timeout, intent=args.intent)
    else:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        cmds = raw.get("commands", []) if isinstance(raw, dict) else raw
        queries = [q.strip() for q in args.queries.split(",") if q.strip()] \
            if args.queries else None
        result = batch_execute(root, cmds, queries=queries,
                               concurrency=args.concurrency, timeout=args.timeout)
    print(json.dumps(result, indent=2))
    if isinstance(result, dict) and result.get("error"):
        return 1
    if any(item.get('error') for item in result.get('results', [])):
        return 1
    code = result.get("exit_code") if isinstance(result, dict) else None
    return 0 if code in (0, None) else 1
