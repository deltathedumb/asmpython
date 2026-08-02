"""Internal FFI bindings for the threading module.

These are the raw OS-level thread/lock primitives exposed by the
target backends (target_windows.py / target_linux.py).  User code
should import `threading`, not this module directly.

Pointer-sized return values (HANDLE, CRITICAL_SECTION*) use ret_type="str"
so the full 64-bit value is preserved — the same convention as os.fopen.
"""
from __future__ import annotations

from . import Func

BINDINGS: dict = {
    # _threading_create(thread_obj: any) -> handle: str
    # Spawn a new OS thread that boots through the stdlib threading trampoline
    # using the passed Thread instance. Returns a 64-bit HANDLE (str slot).
    "_threading_create": Func(
        arg_types=("any",), ret_type="str",
        c_name="_threading_create",
    ),
    # _threading_join(handle: str) -> int
    "_threading_join": Func(
        arg_types=("str",), ret_type="int",
        c_name="_threading_join",
    ),
    # _threading_is_alive(handle: str) -> int (1=alive, 0=done)
    "_threading_is_alive": Func(
        arg_types=("str",), ret_type="int",
        c_name="_threading_is_alive",
    ),
    # _threading_lock_init(dummy: int) -> cs_ptr: str
    # Allocate + initialise an OS mutex; returns 64-bit pointer (str slot).
    "_threading_lock_init": Func(
        arg_types=("int",), ret_type="str",
        c_name="_threading_lock_init",
    ),
    # _threading_lock_acquire(cs_ptr: str) -> int
    "_threading_lock_acquire": Func(
        arg_types=("str",), ret_type="int",
        c_name="_threading_lock_acquire",
    ),
    # _threading_lock_release(cs_ptr: str) -> int
    "_threading_lock_release": Func(
        arg_types=("str",), ret_type="int",
        c_name="_threading_lock_release",
    ),
    # _threading_lock_destroy(cs_ptr: str) -> int
    "_threading_lock_destroy": Func(
        arg_types=("str",), ret_type="int",
        c_name="_threading_lock_destroy",
    ),
    # _threading_get_ident() -> int  (current thread ID — fits in 32 bits)
    "_threading_get_ident": Func(
        arg_types=(), ret_type="int",
        c_name="_threading_get_ident",
    ),
    # _threading_active_count() -> int
    "_threading_active_count": Func(
        arg_types=(), ret_type="int",
        c_name="_threading_active_count",
    ),
}
