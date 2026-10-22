"""UI readers share the CLI's read-only ledger projections."""
from __future__ import annotations
import functools
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

WAKE_PREFIX = 'HX-WAKE partner'
WAKE_ACCEPTED = 'accepted'
WAKE_STATUSES = ('accepted', 'no-socket', 'refused')
WAKE_NOT_DELIVERED = 3
SCOPE_BOARD = 'board'
SCOPE_GOALS = 'goals'
SCOPE_TASKS = 'tasks'
VIEW_SCOPES = (SCOPE_BOARD, SCOPE_GOALS, SCOPE_TASKS)


class SourceError(Exception):
    pass


class SourceUnavailable(SourceError):
    pass


class NotFound(SourceError):
    pass


class CommandError(SourceError):
    def __init__(self, message, returncode=None):
        super().__init__(message)
        self.returncode = returncode


def valid_identity(identity):
    return isinstance(identity, str) and 0 < len(identity) <= 512 and identity not in {'.', '..'} and not any(ord(c) < 32 or c in '/\\' for c in identity)


class Source:
    def board(self):
        raise NotImplementedError

    def show(self, identity):
        raise NotImplementedError

    def goals(self):
        raise NotImplementedError

    def scan(self):
        raise NotImplementedError

    def wake_partner(self, text):
        return self.wake_partner_status(text) == WAKE_ACCEPTED

    def wake_partner_status(self, text):
        raise NotImplementedError


class FixtureSource(Source):
    def __init__(self, directory):
        self.directory = Path(directory)
        self.woken = []
        self.wake_accepts = True
        self.wake_status = None

    def _read(self, name):
        try:
            value = json.loads((self.directory / name).read_text())
        except FileNotFoundError as exc:
            raise NotFound(f'no fixture {name}') from exc
        except (OSError, ValueError) as exc:
            raise SourceError(str(exc)) from exc
        if not isinstance(value, dict):
            raise SourceError('expected a packet projection object')
        return value

    def board(self):
        return self._read('board.json')

    def show(self, identity):
        if not valid_identity(identity):
            raise NotFound('invalid ledger identity')
        return self._read(f'show-{identity}.json')

    def goals(self):
        return self._read('goals.json')

    def wake_partner_status(self, text):
        self.woken.append(text)
        return self.wake_status or ('accepted' if self.wake_accepts else 'refused')

    def scan(self):
        return {p.stem.removeprefix('show-'): p.stat().st_mtime_ns for p in self.directory.glob('*.json')}


def _hx_binary():
    candidate = Path(sys.executable).parent / 'hx'
    return str(candidate) if candidate.exists() else 'hx'


def run_hx(root, args, *, binary=None, document=False, env=None):
    environment = {**os.environ, **(env or {}), 'HARNESS_ROOT': str(root)}
    for key in ('HARNESS_ID', 'HX_CONTINUITY_RUN', 'HX_CONTINUITY_LAUNCH'):
        environment.pop(key, None)
    try:
        result = subprocess.run([binary or _hx_binary(), *args], capture_output=True, text=True, env=environment)
    except OSError as exc:
        raise SourceUnavailable(str(exc)) from exc
    if result.returncode:
        raise CommandError((result.stderr or result.stdout).strip(), result.returncode)
    return result.stdout


PUBLISHED = {'board': ('hx.board', 'collect'), 'show': ('hx.show', 'collect'),
             'goals': ('hx.goals', 'collect'), 'metrics': ('hx.metrics', 'collect_fleet'),
             'wake': ('hx.wake', 'wake_partner_status')}


@functools.cache
def published(name):
    module, attribute = PUBLISHED[name]
    return getattr(importlib.import_module(module), attribute)


def harness_errors():
    from hx.errors import HxError, NotFound as HxNotFound
    return HxNotFound, HxError


class InstanceSource(Source):
    def __init__(self, root, *, binary=None, tmux_socket=None, prefer_subprocess=False, env=None):
        self.root = Path(root)
        self.binary = binary
        self.env = env
        self.prefer_subprocess = prefer_subprocess

    def bound(self, name):
        return None if self.prefer_subprocess else published(name)

    @staticmethod
    def _translate(call):
        not_found, hx_error = harness_errors()
        try:
            return call()
        except not_found as exc:
            raise NotFound(str(exc)) from exc
        except (hx_error, OSError) as exc:
            raise CommandError(str(exc)) from exc

    def _json(self, args):
        try:
            result = json.loads(run_hx(self.root, args, binary=self.binary, document=True, env=self.env))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if not isinstance(result, dict):
            raise CommandError('expected a packet projection object')
        return result

    def board(self):
        fn = self.bound('board')
        return self._translate(lambda: fn(self.root, env=self.env)) if fn else self._json(['board', '--json'])

    def show(self, identity):
        if not valid_identity(identity):
            raise NotFound('invalid ledger identity')
        fn = self.bound('show')
        return self._translate(lambda: fn(self.root, identity, env=self.env)) if fn else self._json(['show', identity, '--json'])

    def goals(self):
        fn = self.bound('goals')
        return self._translate(lambda: fn(self.root)) if fn else self._json(['goals', '--json'])

    def wake_partner_status(self, text):
        fn = self.bound('wake')
        if fn:
            return self._translate(lambda: fn(self.root, text))
        try:
            output = run_hx(self.root, ['wake', 'partner', text], binary=self.binary, env=self.env)
        except CommandError as exc:
            if exc.returncode != WAKE_NOT_DELIVERED:
                raise
            output = str(exc)
        for line in reversed(output.splitlines()):
            if line.startswith(WAKE_PREFIX):
                status = line.removeprefix(WAKE_PREFIX).strip()
                if status in WAKE_STATUSES:
                    return status
        return 'refused'

    def scan(self):
        # WAL commits need not move the main database. Include its nanosecond
        # mtime and size so rapid updates and checkpoint truncation invalidate views.
        stamps = []
        for name in ('continuity.sqlite', 'continuity.sqlite-wal'):
            try:
                stat = (self.root / 'state' / name).stat()
                stamps.append((name, stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                stamps.append((name, 0, 0))
        return {scope: tuple(stamps) for scope in VIEW_SCOPES}
