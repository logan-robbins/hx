"""Per-root job runner for the application loop's long work.

Acceptance checks run for up to an hour and a Companion pass for up to a
minute. The tick submits them here and reaps their outcomes on a later tick, so
one slow check no longer holds every other run's completion, restart, reset and
Companion behind it. `Jobs.inline()` runs each job at submission on the
caller's ledger; tests and `hx loop --once` use it. The service uses threads,
each opening its own ledger connection.

Profile settings live in config/hx.json: `loop.workers` bounds the threads and
`companion.max_concurrent` sizes the Companion slots every kind shares.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .errors import ValidationError

WORKERS = 4
COMPANIONS = 1


def profile(root):
    """The instance profile, config/hx.json; empty when there is none."""
    try:
        config = json.loads((Path(root) / "config" / "hx.json").read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValidationError("config/hx.json is unreadable: " + str(exc)) from exc
    return config if isinstance(config, dict) else {}


def setting(root, section, key, default, *, low, high):
    """An integer profile setting from config/hx.json, or its default when unset."""
    value = (profile(root).get(section) or {}).get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"config/hx.json {section}.{key} must be an integer from {low} to {high}")
    return value


def workers(root):
    return setting(root, "loop", "workers", WORKERS, low=1, high=64)


def companion_slots(root):
    return setting(root, "companion", "max_concurrent", COMPANIONS, low=1, high=64)


class Outcome:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def result(self):
        if self.error is not None:
            raise self.error
        return self.value


class Jobs:
    """Keyed jobs: a key runs at most once at a time and its outcome waits to be taken."""

    def __init__(self, root=None, *, workers=0):
        self.root = None if root is None else Path(root)
        self.pool = ThreadPoolExecutor(workers, thread_name_prefix="hx-loop-job") if workers else None
        self.lock = threading.Lock()
        self.running = {}
        self.done = {}
        self.owned = set()
        self.wake = threading.Event()

    @classmethod
    def inline(cls):
        return cls()

    def submit(self, key, function, store=None):
        """Start `function(store)` unless `key` is running or its outcome is untaken.

        Inline jobs run now on `store`. Threaded jobs open their own ledger.
        """
        with self.lock:
            if key in self.running or key in self.done:
                return False
            self.running[key] = True
        if self.pool is None:
            self._finish(key, self._call(function, store))
        else:
            self.pool.submit(self._thread, key, function)
        return True

    def _thread(self, key, function):
        from .continuity_store import ContinuityStore
        try:
            with ContinuityStore.attach(self.root) as store:
                outcome = self._call(function, store)
        except BaseException as exc:  # The opener itself failed.
            outcome = Outcome(error=exc)
        self._finish(key, outcome)

    @staticmethod
    def _call(function, store):
        try:
            return Outcome(function(store))
        except BaseException as exc:
            return Outcome(error=exc)

    def _finish(self, key, outcome):
        with self.lock:
            self.running.pop(key, None)
            self.done[key] = outcome
        self.wake.set()

    def take(self, key):
        """The finished outcome for `key`, once; None while it runs or was never submitted."""
        with self.lock:
            return self.done.pop(key, None)

    def finished(self, kind):
        """Take every finished outcome whose key starts with `kind`."""
        with self.lock:
            keys = [key for key in self.done if key[0] == kind]
            return [(key, self.done.pop(key)) for key in keys]

    def active(self, kind):
        with self.lock:
            return {key for key in (*self.running, *self.done) if key[0] == kind}

    def own(self, job_id):
        """A Companion job row this process executes; reconciliation leaves it alone."""
        with self.lock:
            self.owned.add(job_id)

    def release(self, job_id):
        with self.lock:
            self.owned.discard(job_id)

    def owns(self, job_id):
        with self.lock:
            return job_id in self.owned

    def close(self):
        """Let running jobs finish; queued jobs never start."""
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)
