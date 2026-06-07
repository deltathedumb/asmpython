"""os module: a small handful of POSIX/CRT primitives.

Cross-platform basics only. Avoid anything that would require structs or
opaque handles (file descriptors are fine since they're ints).
"""
from __future__ import annotations

from . import Func


BINDINGS = {
    # Returns the child process's exit status.
    "system": Func(arg_types=("str",), ret_type="int", c_name="system"),
    # getenv(name) — returns a pointer; we treat NULL as the empty string at
    # the moment (no nullability tracking yet).
    "getenv": Func(arg_types=("str",), ret_type="str", c_name="getenv"),
    # exit(code)
    "_exit":  Func(arg_types=("int",), ret_type="int", c_name="exit"),
}
