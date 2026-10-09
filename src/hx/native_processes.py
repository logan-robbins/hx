"""Bounded process ownership probes; never inspect command lines or environments.

Darwin signals use an audit token (including pidversion); Linux uses pidfds.
A process-tree snapshot covers observed descendants, not historical orphans or
remote jobs. Callers must not mistake that scope for full execution quiescence.
"""

from __future__ import annotations

import ctypes
import errno
import os
import signal
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from .continuity_store import Conflict
from .errors import ValidationError

MAX_PROCESSES = 256


@lru_cache(maxsize=1)
def _darwin():
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    lib.mach_task_self.restype = ctypes.c_uint
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.task_name_for_pid.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    lib.task_info.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    lib.proc_signal_with_audittoken.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return lib


@lru_cache(maxsize=1)
def boot():
    if sys.platform == 'darwin':
        buffer = ctypes.create_string_buffer(128)
        size = ctypes.c_size_t(len(buffer))
        if _darwin().sysctlbyname(b'kern.bootsessionuuid', buffer, ctypes.byref(size), None, 0):
            raise OSError(ctypes.get_errno(), 'cannot identify the current boot')
        return buffer.value.decode('ascii')
    if sys.platform.startswith('linux'):
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    raise ValidationError('native process ownership requires Darwin audit tokens or Linux pidfds')


class _BSDInfo(ctypes.Structure):
    # Public proc_bsdinfo ABI from <sys/proc_info.h>. Names occupy their ABI
    # slots but are never returned or persisted.
    _fields_ = [(key, ctypes.c_uint32) for key in
        ('flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid', 'rgid', 'svuid', 'svgid', 'reserved')] + [
        ('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)] + [
        (key, ctypes.c_uint32) for key in ('nfiles', 'pgid', 'jobc', 'tdev', 'tpgid')] + [
        ('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64), ('start_usec', ctypes.c_uint64)]


def probe(pid):
    """Return process-instance identity and minimal liveness/parent metadata."""
    if type(pid) is not int or pid <= 1:
        raise ValidationError('process ownership requires a non-system PID')
    if sys.platform == 'darwin':
        lib = _darwin()
        info = _BSDInfo()
        amount = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if not amount and ctypes.get_errno() == errno.ESRCH:
            return None
        if amount != ctypes.sizeof(info):
            raise Conflict('cannot read the original native process identity')
        if info.status == 5:  # SZOMB; no longer able to execute.
            return None
        port, count = ctypes.c_uint(), ctypes.c_uint(8)
        token = (ctypes.c_uint32 * 8)()
        result = lib.task_name_for_pid(lib.mach_task_self(), pid, ctypes.byref(port))
        if result:
            # Distinguish a concurrent exit from inaccessible live ownership.
            amount = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
            if (not amount and ctypes.get_errno() == errno.ESRCH) or (amount == ctypes.sizeof(info) and info.status == 5):
                return None
            raise Conflict('native process audit token is unavailable')
        try:
            if lib.task_info(port.value, 15, token, ctypes.byref(count)) or count.value != 8 or token[5] != pid:
                amount = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
                if (not amount and ctypes.get_errno() == errno.ESRCH) or (amount == ctypes.sizeof(info) and info.status == 5):
                    return None
                raise Conflict('native process audit token is unavailable')
        finally:
            lib.mach_port_deallocate(lib.mach_task_self(), port.value)
        after = _BSDInfo()
        if lib.proc_pidinfo(pid, 3, 0, ctypes.byref(after), ctypes.sizeof(after)) != ctypes.sizeof(after):
            return None if ctypes.get_errno() == errno.ESRCH else _unavailable()
        if (info.start_sec, info.start_usec) != (after.start_sec, after.start_usec):
            raise Conflict('native process changed during identity capture')
        return {'identity': {'platform': 'darwin', 'boot': boot(), 'pid': pid, 'token': list(token)},
                'ppid': after.ppid, 'uid': after.uid, 'stopped': after.status == 4}
    if sys.platform.startswith('linux'):
        path = Path('/proc') / str(pid) / 'stat'
        try:
            with path.open('rb') as source:
                raw = source.read(8193)
            uid = path.stat().st_uid
        except FileNotFoundError:
            return None
        if len(raw) > 8192:
            raise Conflict('native process metadata exceeds its bound')
        fields = raw[raw.rfind(b')') + 2:].split()
        if len(fields) < 20:
            raise Conflict('native process metadata is incomplete')
        if fields[0] in {b'Z', b'X'}:
            return None
        return {'identity': {'platform': 'linux', 'boot': boot(), 'pid': pid, 'start': int(fields[19])},
                'ppid': int(fields[1]), 'uid': uid, 'stopped': fields[0] in {b'T', b't'}}
    boot()  # Explicit unsupported-platform error.


def _unavailable():
    raise Conflict('cannot revalidate native process identity')


def current(identity):
    if identity.get('boot') != boot():
        return None
    actual = probe(identity['pid'])
    return actual if actual and actual['identity'] == identity else None


def send(identity, sig):
    """Signal the exact instance; stale identities never target a reused PID."""
    if sig not in {signal.SIGSTOP, signal.SIGKILL}:
        raise ValidationError('native shutdown permits only stop and kill signals')
    if identity['pid'] == os.getpid():
        raise Conflict('native shutdown cannot target its controller process')
    actual = current(identity)
    if actual is None:
        return False
    if actual['uid'] != os.geteuid():
        raise Conflict('native shutdown cannot signal another user’s process')
    if sys.platform == 'darwin':
        code = _darwin().proc_signal_with_audittoken((ctypes.c_uint32 * 8)(*identity['token']), int(sig))
        if code == errno.ESRCH:
            return False
        if code:
            raise OSError(code, 'native process signal was refused')
    else:
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise ValidationError('native shutdown requires kernel pidfd signalling support')
        try:
            descriptor = os.pidfd_open(identity['pid'])
        except ProcessLookupError:
            return False
        try:
            if current(identity) is None:
                return False
            signal.pidfd_send_signal(descriptor, sig)
        except ProcessLookupError:
            return False
        finally:
            os.close(descriptor)
    return True


def descendants(frozen, *, supervisor=None):
    """Discover children of stopped, exact parent instances in one bounded census.

    Callers stop newly discovered children before their next census. A historical
    detached process can be outside this ancestry and requires separate evidence.
    """
    if len(frozen) > MAX_PROCESSES:
        raise Conflict('native shutdown process bound exceeded')
    parents = {}
    for identity in frozen:
        actual = current(identity)
        if actual is not None:
            if not actual['stopped'] and identity != supervisor:
                raise Conflict('native parent must be stopped before descendant discovery')
            parents[identity['pid']] = identity
    if not parents:
        return []
    # Kernel process metadata only; disk output is bounded before parsing.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid='], stdout=output,
                                stderr=subprocess.DEVNULL, timeout=10, check=False)
        output.seek(0)
        raw = output.read(262145)
    if result.returncode or len(raw) > 262144:
        raise Conflict('native process census failed or exceeded its bound')
    found = []
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) != 2 or not all(value.isdigit() for value in fields):
            raise Conflict('native process census is malformed')
        pid, parent = map(int, fields)
        if parent not in parents or pid <= 1:
            continue
        actual = probe(pid)
        if actual is None or actual['ppid'] != parent:
            continue
        if current(parents[parent]) is None:
            raise Conflict('native parent disappeared during descendant discovery')
        if actual['uid'] != os.geteuid():
            raise Conflict('native descendant ownership crosses user identities')
        found.append(actual['identity'])
        if len(found) > MAX_PROCESSES:
            raise Conflict('native shutdown process bound exceeded')
    return found
