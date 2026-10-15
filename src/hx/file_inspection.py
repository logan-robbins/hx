"""Recognized file inspections must not enter a lossy command-output reducer.

This classifies supported command shapes; it is not a sandbox for arbitrary
programs. File inspection belongs to the complete native read broker, while
build/test/search output keeps the isolated command and reduction path.
"""
from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

from .errors import ValidationError

READERS = {'cat', 'tac', 'head', 'tail', 'less', 'more', 'nl', 'bat'}
SHELLS = {'sh', 'bash', 'zsh', 'dash', 'fish'}


def _commands(script):
    try:
        lexer = shlex.shlex(script, posix=True, punctuation_chars=';&|()')
        lexer.whitespace_split = True
        parts = list(lexer)
    except ValueError as exc:
        raise ValidationError('cannot verify malformed shell command output as non-file inspection') from exc
    current = []
    for part in parts:
        if part and all(char in ';&|()' for char in part):
            if current:
                yield current
                current = []
        else:
            current.append(part)
    if current:
        yield current


def inspection(argv):
    """Return the recognized file reader, or None for other command output."""
    parts = list(argv)
    while parts and (parts[0] in {'command', 'exec', 'env'} or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', parts[0])):
        parts.pop(0)
    if not parts:
        return None
    program = Path(parts[0]).name
    if program in READERS:
        return program
    if program == 'sed' and not any(part == '-i' or part.startswith('-i') or part == '--in-place' or part.startswith('--in-place=') for part in parts[1:]):
        return program
    if program in {'awk', 'gawk', 'mawk'} and any(re.search(r'\b(?:NR|FNR|getline|print|printf)\b', part) for part in parts[1:]):
        return program
    if program == 'git' and len(parts) > 1:
        if parts[1] == 'show' and any(':' in part and not part.startswith('--') for part in parts[2:]):
            return 'git show REV:path'
        if parts[1] == 'cat-file' and any(part in {'-p', '--batch', '--batch-command'} for part in parts[2:]):
            return 'git cat-file'
    if program in SHELLS:
        for index, part in enumerate(parts[1:], 1):
            if part.startswith('-') and 'c' in part[1:] and index + 1 < len(parts):
                return next((found for command in _commands(parts[index + 1]) if (found := inspection(command))), None)
    if program in {'python', 'python3', 'node', 'ruby', 'perl'} and len(parts) > 2 and parts[1] in {'-c', '-e'}:
        if re.search(r'(?:\bprint|\bputs|sys\.stdout(?:\.buffer)?\.write|process\.stdout\.write|console\.log)\s*\([^;\n]*(?:\.read_(?:text|bytes)\s*\(|\breadFile(?:Sync)?\s*\(|\bopen\s*\([^\n]+\.read\s*\(|\bFile\.read\s*\()', parts[2]):
            return program + ' file extraction'
    return None


def require_non_file_output(argv):
    reader = inspection(argv)
    if reader:
        raise ValidationError(f'{reader} is a file inspection and cannot use bounded command capture or output reduction; '
                              'use the native complete-file Read/read/read_file operation without offsets or limits')


def require_non_file_code(language, code, *, bound_file=False):
    """Reject recognized raw-file output before an isolated snippet executes.

    FILE_CONTENT computations remain useful here. This recognizes direct dumps,
    slices, simple aliases and text transformations, rather than claiming to
    sandbox arbitrary programs or infer every possible data flow.
    """
    if language == 'shell':
        for command in _commands(code):
            require_non_file_output(command)
        raw = bound_file and re.search(r'\$(?:FILE_CONTENT\b|\{FILE_CONTENT(?:\}|[:/]))', code)
    elif language == 'python':
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return  # The interpreter reports invalid code without running it.
        aliases = {'FILE_CONTENT'} if bound_file else set()
        reducers = {'len', 'sum', 'min', 'max', 'any', 'all', 'hash'}
        scalar_methods = {'count', 'find', 'rfind', 'index', 'rindex', 'startswith', 'endswith'}

        def raw_value(node):
            if isinstance(node, ast.Name):
                return node.id in aliases
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in reducers:
                    return False
                if isinstance(node.func, ast.Name) and node.func.id in {'str', 'repr', 'list', 'tuple', 'sorted'}:
                    return any(raw_value(arg) for arg in node.args)
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in scalar_methods:
                        return False
                    if node.func.attr in {'read_text', 'read_bytes', 'read', 'readlines', 'readline'}:
                        return True
                    return raw_value(node.func.value) or (
                        node.func.attr == 'join' and any(raw_value(arg) for arg in node.args))
                return False
            return any(raw_value(child) for child in ast.iter_child_nodes(node))

        raw = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if raw_value(node.value):
                    aliases.update(target.id for target in targets if isinstance(target, ast.Name))
            if isinstance(node, ast.Call):
                sink = ast.unparse(node.func)
                if sink == 'print' or sink in {
                    'sys.stdout.write', 'sys.stdout.buffer.write', 'sys.stdout.writelines',
                    'sys.stderr.write', 'sys.stderr.buffer.write', 'sys.stderr.writelines',
                }:
                    raw = raw or any(raw_value(arg) for arg in node.args)
    else:
        require_non_file_output(['node', '-e', code])
        # .length is derived output; direct FILE_CONTENT text/slices are not.
        raw = bound_file and re.search(
            r'(?:console\.(?:log|error)|process\.(?:stdout|stderr)\.write)\s*\('
            r'[^;\n]*\bFILE_CONTENT\b(?!\s*\.\s*length\b)', code)
    if raw:
        raise ValidationError('raw file content cannot use bounded command capture or output reduction; '
                              'use the native complete-file Read/read/read_file operation without offsets or limits')
