# The C library and kernel32 symbols a ctypes declaration reaches, bound for
# the interpreter.
#
# WHY THIS FILE HAS TO EXIST. A `ctypes` declaration in this compiler is a
# promise to the LINKER: `frontends/python/analysis.py` records the signature,
# `lower.py` emits an external, and the backend writes an `extern` the system
# linker resolves. Nothing is opened at run time -- there is no `dlopen` here.
# That works for a compiled binary and leaves the IR interpreter with nothing
# to call, so `pathlib.read_text()` ran under `asmpython build` and trapped
# under `asmpython run` with "call to undefined function '_open'".
#
# The interpreter is the ORACLE the C backend is measured against. A module the
# oracle cannot execute is a module whose compiled behaviour nothing checks,
# which is the one arrangement the corpus exists to prevent -- so the symbols
# get host bindings, the same way `sqrt`, `fmod` and `print_str` already do in
# `interpreter.py`. This file is those bindings and nothing else.
#
# THE POINTERS ARE NOT ADDRESSES. Everywhere else in this compiler a `ptr` is
# an offset into `interp.mem.buf`, a `bytearray` belonging to the interpreter's
# process. Handing one to a real `os.read` would write over an unrelated part
# of this process. Every function below therefore MARSHALS: a string argument
# is copied out of `mem.buf` to the NUL, and a buffer argument is filled by
# copying back. That is what makes these bindings equivalent to the call a
# compiled program makes rather than an approximation of it.
#
# WHY `os` AND NOT `ctypes`. Calling the real `_open` through ctypes from here
# would be closer to the letter of what the program asked for and further from
# the point: the interpreter must produce the ANSWER a compiled program
# produces, and `os.open` produces it through the same C library, on any
# platform, without this file having to be right about a calling convention.

import os

#: Returned by `call` for a name this file does not implement, so the caller
#: can fall through to its own trap. `None` cannot serve -- it is what a void
#: function returns. The same sentinel arrangement as `objects_host.NOT_MINE`.
NOT_MINE = object()

#: `GetFileAttributesA` for a path that is not there, and the bit that means
#: "directory". The compiled path gets these from Windows itself; here they are
#: written out, and `bundled/pathlib.py` writes the same two numbers.
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_NORMAL = 0x80


def _cstr(interp, addr: int) -> str:
    """The NUL-terminated string at `addr` in the interpreter's memory."""
    end = interp.mem.buf.index(0, addr)
    return bytes(interp.mem.buf[addr:end]).decode("utf-8", "surrogateescape")


def call(interp, name: str, args: list):
    """`name(*args)` if this file implements it, else `NOT_MINE`.

    THE ERRORS ARE RETURN VALUES, never exceptions. Each of these functions
    reports failure the way its C original does -- a negative fd, a zero
    BOOL, `INVALID_FILE_ATTRIBUTES` -- and `bundled/pathlib.py` reads those
    codes and raises the Python exception. Letting an `OSError` out of here
    instead would raise it from the wrong place, with a message the compiled
    path never produces, and the two paths would stop agreeing on the very
    cases (a missing file, a directory that exists) that most need checking.
    """
    handler = _TABLE.get(name)
    if handler is None:
        return NOT_MINE
    return handler(interp, args)


# ── the descriptor calls (msvcrt's `_open` family) ──────────────────────────
#
# THE FLAG NUMBERS PASS STRAIGHT THROUGH. `O_RDONLY`, `O_WRONLY`, `O_CREAT`,
# `O_TRUNC` and `O_BINARY` have the same values in Python's `os` as in the C
# runtime the compiled path calls, because Python takes them from that header.
# Translating them would be a second place to be wrong about a constant.


def _open(interp, args):
    path = _cstr(interp, int(args[0]))
    flags = int(args[1])
    mode = int(args[2]) if len(args) > 2 else 0o666
    try:
        return os.open(path, flags, mode)
    except OSError:
        return -1


def _close(interp, args):
    try:
        os.close(int(args[0]))
    except OSError:
        return -1
    return 0


def _read(interp, args):
    fd, addr, want = int(args[0]), int(args[1]), int(args[2])
    try:
        got = os.read(fd, want)
    except OSError:
        return -1
    # THE COPY BACK IS THE CALL. `os.read` filled a bytes object in this
    # process; the program asked for its buffer at `addr` to be filled.
    interp.mem.buf[addr:addr + len(got)] = got
    _writeback(interp, addr, got)
    return len(got)


def _writeback(interp, addr: int, data: bytes) -> None:
    """Put `data` into the host object whose bytes live at `addr`, if any.

    THE SECOND HALF OF THE MARSHALLING, and the half that is easy to forget
    because leaving it out fails QUIETLY. `objects_host._apy_str_bytes` COPIES
    a host `bytearray` into interpreter memory and answers where -- so a
    native call writes into the copy, `read_bytes` reads the original, and the
    program gets a buffer of zeroes of exactly the right length. Nothing
    errors; the answer is just wrong, which is the failure this compiler's
    two-path arrangement exists to catch and the one it is worst at noticing.

    A buffer with no entry is a `str` or `bytes` -- immutable, never written
    to -- and needs nothing.
    """
    buffers = getattr(interp.objects, "_native_buffers", None)
    if not buffers:
        return
    target = buffers.get(addr)
    if target is not None:
        target[:len(data)] = data


def _write(interp, args):
    fd, addr, n = int(args[0]), int(args[1]), int(args[2])
    try:
        return os.write(fd, bytes(interp.mem.buf[addr:addr + n]))
    except OSError:
        return -1


def _lseek(interp, args):
    try:
        return os.lseek(int(args[0]), int(args[1]), int(args[2]))
    except OSError:
        return -1


# ── the kernel32 calls ──────────────────────────────────────────────────────
#
# BOUND ON EVERY PLATFORM, not only on Windows. `bundled/pathlib.py` names
# these because the C runtime's own headers already declare `_unlink` and
# `_mkdir` and the backend's `extern` for them conflicts -- a fact about this
# compiler, not about the operating system. The interpreter answering them
# through `os` on a Linux host is the same answer, so the oracle keeps working
# where the compiled path would need a different declaration.


def _get_file_attributes(interp, args):
    path = _cstr(interp, int(args[0]))
    if os.path.isdir(path):
        return _FILE_ATTRIBUTE_DIRECTORY
    if os.path.exists(path):
        return _FILE_ATTRIBUTE_NORMAL
    return _INVALID_FILE_ATTRIBUTES


def _create_directory(interp, args):
    try:
        os.mkdir(_cstr(interp, int(args[0])))
    except OSError:
        return 0
    return 1


def _delete_file(interp, args):
    try:
        os.remove(_cstr(interp, int(args[0])))
    except OSError:
        return 0
    return 1


def _remove_directory(interp, args):
    try:
        os.rmdir(_cstr(interp, int(args[0])))
    except OSError:
        return 0
    return 1


#: THE WHOLE SURFACE, and deliberately a short one. A symbol reaches this file
#: only because a bundled module declared it, so the table grows when the
#: standard library does and not before -- it is not an attempt to bind libc.
_TABLE = {
    "_open": _open,
    "_close": _close,
    "_read": _read,
    "_write": _write,
    "_lseek": _lseek,
    "GetFileAttributesA": _get_file_attributes,
    "CreateDirectoryA": _create_directory,
    "DeleteFileA": _delete_file,
    "RemoveDirectoryA": _remove_directory,
}
