"""errno module: standard system error codes.

Provides the numeric error code constants that C's errno.h defines.
POSIX values are used; Windows CRT uses the same numbers for the shared
subset.

`errorcode` is a dict[str,str] keyed by the string form of the code
(e.g. errorcode["2"] == "ENOENT"), matching cpython's int-keyed dict
semantics as closely as the compiler's string-key-only dict allows.
Use errno.strerror(code) for a description string.
"""
from __future__ import annotations

import sys

# Shared POSIX / Windows CRT subset
EPERM: int = 1
ENOENT: int = 2
ESRCH: int = 3
EINTR: int = 4
EIO: int = 5
ENXIO: int = 6
E2BIG: int = 7
ENOEXEC: int = 8
EBADF: int = 9
ECHILD: int = 10
EAGAIN: int = 11
ENOMEM: int = 12
EACCES: int = 13
EFAULT: int = 14
EBUSY: int = 16
EEXIST: int = 17
EXDEV: int = 18
ENODEV: int = 19
ENOTDIR: int = 20
EISDIR: int = 21
EINVAL: int = 22
ENFILE: int = 23
EMFILE: int = 24
ENOTTY: int = 25
EFBIG: int = 27
ENOSPC: int = 28
ESPIPE: int = 29
EROFS: int = 30
EMLINK: int = 31
EPIPE: int = 32
EDOM: int = 33
ERANGE: int = 34

if sys.platform == "win32":
    EDEADLK: int = 36
    ENAMETOOLONG: int = 38
    ENOLCK: int = 39
    ENOSYS: int = 40
    ENOTEMPTY: int = 41
    EILSEQ: int = 42
    EADDRINUSE: int = 100
    EADDRNOTAVAIL: int = 101
    EAFNOSUPPORT: int = 102
    EALREADY: int = 103
    ECONNABORTED: int = 106
    ECONNREFUSED: int = 107
    ECONNRESET: int = 108
    EDESTADDRREQ: int = 109
    EHOSTUNREACH: int = 110
    EINPROGRESS: int = 112
    EISCONN: int = 113
    ELOOP: int = 114
    EMSGSIZE: int = 115
    ENETDOWN: int = 116
    ENETRESET: int = 117
    ENETUNREACH: int = 118
    ENOBUFS: int = 119
    ENOPROTOOPT: int = 123
    ENOTCONN: int = 126
    ENOTSOCK: int = 128
    EOPNOTSUPP: int = 130
    EPROTONOSUPPORT: int = 135
    EPROTOTYPE: int = 136
    ETIMEDOUT: int = 138
    EWOULDBLOCK: int = 140
    ENOTSUP: int = 129
else:
    EDEADLK: int = 35
    ENAMETOOLONG: int = 36
    ENOLCK: int = 37
    ENOSYS: int = 38
    ENOTEMPTY: int = 39
    ELOOP: int = 40
    EWOULDBLOCK: int = 11
    ENOMSG: int = 42
    EIDRM: int = 43
    ENOSTR: int = 60
    ENODATA: int = 61
    ETIME: int = 62
    ENOSR: int = 63
    EREMOTE: int = 66
    ENOLINK: int = 67
    EPROTO: int = 71
    EMULTIHOP: int = 72
    EBADMSG: int = 74
    EOVERFLOW: int = 75
    EILSEQ: int = 84
    EUSERS: int = 87
    ENOTSOCK: int = 88
    EDESTADDRREQ: int = 89
    EMSGSIZE: int = 90
    EPROTOTYPE: int = 91
    ENOPROTOOPT: int = 92
    EPROTONOSUPPORT: int = 93
    EAFNOSUPPORT: int = 97
    EADDRINUSE: int = 98
    EADDRNOTAVAIL: int = 99
    ENETDOWN: int = 100
    ENETUNREACH: int = 101
    ENETRESET: int = 102
    ECONNABORTED: int = 103
    ECONNRESET: int = 104
    ENOBUFS: int = 105
    EISCONN: int = 106
    ENOTCONN: int = 107
    ETIMEDOUT: int = 110
    ECONNREFUSED: int = 111
    EHOSTUNREACH: int = 113
    EALREADY: int = 114
    EINPROGRESS: int = 115
    EOPNOTSUPP: int = 95
    ENOTSUP: int = 95

# String-keyed lookup: errorcode[str(n)] -> name string.
# (asmpython dicts use string keys; CPython's errno.errorcode is int-keyed.)
errorcode: dict[str, str] = {
    "1": "EPERM", "2": "ENOENT", "3": "ESRCH", "4": "EINTR",
    "5": "EIO", "6": "ENXIO", "7": "E2BIG", "8": "ENOEXEC",
    "9": "EBADF", "10": "ECHILD", "11": "EAGAIN", "12": "ENOMEM",
    "13": "EACCES", "14": "EFAULT", "16": "EBUSY", "17": "EEXIST",
    "18": "EXDEV", "19": "ENODEV", "20": "ENOTDIR", "21": "EISDIR",
    "22": "EINVAL", "23": "ENFILE", "24": "EMFILE", "25": "ENOTTY",
    "27": "EFBIG", "28": "ENOSPC", "29": "ESPIPE", "30": "EROFS",
    "31": "EMLINK", "32": "EPIPE", "33": "EDOM", "34": "ERANGE",
}


def strerror(code: int) -> str:
    """Return a string describing error code *code*."""
    name: str = errorcode.get(str(code), "")
    if len(name) > 0:
        return name
    return "Unknown error " + str(code)
