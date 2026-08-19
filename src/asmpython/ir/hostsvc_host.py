# The host services, for the IR interpreter.
#
# `link/hostsvc.py` names the operations and says why they exist; this answers
# them. The interpreter is a backend like any other in this respect -- it
# declares what it can do and a program needing more is refused -- and it can
# do a great deal, because it is running inside CPython and CPython has a
# filesystem, a clock, entropy and an environment.
#
# WHY THIS IS A FILE OF ITS OWN AND NOT PART OF `natives_host.py`. That one
# binds the symbols a `ctypes` declaration names -- `_open`, `GetFileAttributesA`
# -- which is the arrangement `hostsvc` exists to REPLACE: a promise to the
# linker that only a linking backend can keep. The two will overlap for as
# long as `bundled/pathlib.py` still uses ctypes, and keeping them apart is
# what makes it obvious which one a module went through.
#
# THE POINTERS ARE OFFSETS INTO `interp.mem.buf`, exactly as in
# `natives_host.py`, so every buffer is marshalled rather than handed to
# CPython as an address. The comment there explains why at length.

import os
import time as _time

#: Returned for a name this file does not implement, so the caller falls
#: through to its own trap. The same sentinel arrangement as
#: `objects_host.NOT_MINE` and `natives_host.NOT_MINE`.
NOT_MINE = object()

#: WHAT THE INTERPRETER CAN DO. Everything but `net` and `text`: the first is
#: not written yet on any backend, and the second wants the Unicode table
#: wired to these names rather than a second copy of it here.
GROUPS = frozenset({"file", "time", "random", "env"})

#: `link/hostsvc.py`'s error table, which is NOT errno -- see there for why.
_ERR, _ENOENT, _EACCES, _EEXIST = -1, -2, -3, -4
_ENOTDIR, _ENOTEMPTY, _EAGAIN, _EPIPE, _EINVAL = -5, -6, -7, -8, -9

_ERRNO = {
    getattr(__import__("errno"), n, None): v for n, v in (
        ("ENOENT", _ENOENT), ("EACCES", _EACCES), ("EPERM", _EACCES),
        ("EEXIST", _EEXIST), ("ENOTDIR", _ENOTDIR),
        ("ENOTEMPTY", _ENOTEMPTY), ("EAGAIN", _EAGAIN), ("EPIPE", _EPIPE),
        ("EINVAL", _EINVAL))
}
_ERRNO.pop(None, None)


def _err(exc: OSError) -> int:
    """One `OSError` as this layer's code.

    TRANSLATED ONCE, HERE, for the same reason the C translates once: the
    whole point of the layer is that a caller never sees an errno, because
    those numbers differ between platforms.
    """
    return _ERRNO.get(exc.errno, _ERR)


def _bytes(interp, addr: int, n: int) -> bytes:
    return bytes(interp.mem.buf[addr:addr + n])


def _path(interp, addr: int, n: int):
    """A path from a pointer and a length, or None if it holds a NUL.

    REJECTED RATHER THAN TRUNCATED. A path containing a NUL is a real class of
    security bug -- the platform call would stop at it and act on a shorter
    path than the caller asked about -- and catching it costs one test. The C
    does the same in `apy_host_path`.
    """
    raw = _bytes(interp, addr, n)
    if b"\x00" in raw:
        return None
    return os.fsdecode(raw)


def call(interp, name: str, args: list):
    """`name(*args)` if this file implements it, else `NOT_MINE`."""
    fn = _TABLE.get(name)
    return NOT_MINE if fn is None else fn(interp, args)


# ── file ────────────────────────────────────────────────────────────────────
#
# THE HANDLE IS A REAL PYTHON FILE OBJECT, kept in a table on the interpreter.
# The C widens a `FILE *`; this cannot, because a Python object has no address
# a program may hold. So the table is the interpreter's equivalent and the
# numbers it hands out are what a program sees -- which is fine, because a
# handle is opaque by contract and a program only ever gives it back.
#
# NUMBERED FROM 3, so the standard three keep their meaning and
# `host_file_write(1, ...)` writes where `plat_write(1, ...)` writes.

_MODES = {0: "rb", 1: "wb", 2: "ab", 3: "r+b"}


def _files(interp) -> dict:
    got = getattr(interp, "_hostsvc_files", None)
    if got is None:
        got = {}
        interp._hostsvc_files = got
    return got


def _stream(interp, fd: int):
    """The object `fd` names, or None. 0/1/2 are the standard three."""
    if fd in (0, 1, 2):
        return None                      # handled by the caller; see below
    return _files(interp).get(fd)


def _host_file_open(interp, a):
    path = _path(interp, int(a[0]), int(a[1]))
    mode = _MODES.get(int(a[2]))
    if path is None or mode is None:
        return _EINVAL
    try:
        f = open(path, mode)
    except OSError as exc:
        return _err(exc)
    table = _files(interp)
    fd = 3
    while fd in table:
        fd += 1
    table[fd] = f
    return fd


def _host_file_read(interp, a):
    fd, addr, n = int(a[0]), int(a[1]), int(a[2])
    if n < 0:
        return _EINVAL
    try:
        if fd == 0:
            got = os.read(0, n)
        else:
            f = _stream(interp, fd)
            if f is None:
                return _EINVAL
            got = f.read(n)
    except OSError as exc:
        return _err(exc)
    interp.mem.buf[addr:addr + len(got)] = got
    return len(got)


def _host_file_write(interp, a):
    fd, addr, n = int(a[0]), int(a[1]), int(a[2])
    if n < 0:
        return _EINVAL
    data = _bytes(interp, addr, n)
    if fd in (1, 2):
        # THROUGH THE INTERPRETER'S OWN WRITER, not `os.write`. A host may
        # have redirected the interpreter's output -- the test harness does --
        # and bypassing that would send a program's output somewhere the
        # harness cannot see, which reads as a program that printed nothing.
        return interp._plat_write(fd, addr, n)
    f = _stream(interp, fd)
    if f is None:
        return _EINVAL
    try:
        return f.write(data)
    except OSError as exc:
        return _err(exc)


def _host_file_close(interp, a):
    fd = int(a[0])
    if fd in (0, 1, 2):
        return 0                          # never close the standard three
    f = _files(interp).pop(fd, None)
    if f is None:
        return _EINVAL
    try:
        f.close()
    except OSError as exc:
        return _err(exc)
    return 0


def _host_file_seek(interp, a):
    fd, off, whence = int(a[0]), int(a[1]), int(a[2])
    if whence not in (0, 1, 2):
        return _EINVAL
    f = _stream(interp, fd)
    if f is None:
        return _EINVAL
    try:
        return f.seek(off, whence)
    except OSError as exc:
        return _err(exc)


def _host_file_kind(interp, a):
    path = _path(interp, int(a[0]), int(a[1]))
    if path is None:
        return _EINVAL
    if os.path.isdir(path):
        return 2
    if os.path.isfile(path):
        return 1
    if os.path.exists(path):
        return 3
    return 0


def _host_file_size(interp, a):
    path = _path(interp, int(a[0]), int(a[1]))
    if path is None:
        return _EINVAL
    try:
        return os.path.getsize(path)
    except OSError as exc:
        return _err(exc)


def _host_file_remove(interp, a):
    path = _path(interp, int(a[0]), int(a[1]))
    if path is None:
        return _EINVAL
    try:
        os.remove(path)
    except OSError as exc:
        return _err(exc)
    return 0


def _host_dir_make(interp, a):
    path = _path(interp, int(a[0]), int(a[1]))
    if path is None:
        return _EINVAL
    try:
        os.mkdir(path)
    except OSError as exc:
        return _err(exc)
    return 0


def _host_dir_remove(interp, a):
    path = _path(interp, int(a[0]), int(a[1]))
    if path is None:
        return _EINVAL
    try:
        os.rmdir(path)
    except OSError as exc:
        return _err(exc)
    return 0


# ── time, random, env ───────────────────────────────────────────────────────


def _host_time_unix(interp, a):
    return _time.time_ns()


def _host_time_monotonic(interp, a):
    return _time.monotonic_ns()


def _host_sleep(interp, a):
    nanos = int(a[0])
    if nanos > 0:
        _time.sleep(nanos / 1e9)
    return 0


def _host_random_bytes(interp, a):
    addr, n = int(a[0]), int(a[1])
    if n < 0:
        return _EINVAL
    interp.mem.buf[addr:addr + n] = os.urandom(n)
    return n


def _host_env_get(interp, a):
    name = _path(interp, int(a[0]), int(a[1]))
    out, cap = int(a[2]), int(a[3])
    if name is None:
        return _EINVAL
    got = os.environ.get(name)
    if got is None:
        # ABSENT AND EMPTY STAY DISTINCT, which is why this is not 0.
        return _ENOENT
    raw = os.fsencode(got)
    interp.mem.buf[out:out + min(len(raw), cap)] = raw[:cap]
    # THE LENGTH IT NEEDED, not the length written -- a caller that guessed
    # too small sees a number larger than its buffer and calls again.
    return len(raw)


def _host_arg_count(interp, a):
    return len(getattr(interp, "argv", ()) or ())


def _host_arg_get(interp, a):
    i, out, cap = int(a[0]), int(a[1]), int(a[2])
    argv = getattr(interp, "argv", ()) or ()
    if i < 0 or i >= len(argv):
        return _EINVAL
    raw = os.fsencode(str(argv[i]))
    interp.mem.buf[out:out + min(len(raw), cap)] = raw[:cap]
    return len(raw)


_TABLE = {
    "host_file_open": _host_file_open,
    "host_file_read": _host_file_read,
    "host_file_write": _host_file_write,
    "host_file_close": _host_file_close,
    "host_file_seek": _host_file_seek,
    "host_file_kind": _host_file_kind,
    "host_file_size": _host_file_size,
    "host_file_remove": _host_file_remove,
    "host_dir_make": _host_dir_make,
    "host_dir_remove": _host_dir_remove,
    "host_time_unix": _host_time_unix,
    "host_time_monotonic": _host_time_monotonic,
    "host_sleep": _host_sleep,
    "host_random_bytes": _host_random_bytes,
    "host_env_get": _host_env_get,
    "host_arg_count": _host_arg_count,
    "host_arg_get": _host_arg_get,
}
