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

    # --- File I/O (C stdio) --------------------------------------------------
    # These bind directly to libc/msvcrt stdio. A FILE* is an opaque pointer,
    # which is just an 8-byte int slot in asmpython; 0 means NULL (failure).
    # Char-at-a-time fgetc avoids needing a caller-allocated buffer, so a whole
    # file can be read with pure FFI by appending each char to a string.
    #
    # fopen(path, mode) -> FILE* handle. Typed `str` because the result is a
    # full 64-bit pointer (not a 32-bit C int): an int return would be sign-
    # extended from EAX and truncate the pointer. A NULL (0) handle = failure.
    "fopen":  Func(arg_types=("str", "str"), ret_type="str", c_name="fopen"),
    # fgetc(FILE*) -> next byte (0..255), or -1 (EOF). The handle is a pointer,
    # passed as `str`; the return is a real C int.
    "fgetc":  Func(arg_types=("str",), ret_type="int", c_name="fgetc"),
    # fputc(char, FILE*) -> char written, or EOF on error.
    "fputc":  Func(arg_types=("int", "str"), ret_type="int", c_name="fputc"),
    # fputs(str, FILE*) -> non-negative on success, EOF on error.
    "fputs":  Func(arg_types=("str", "str"), ret_type="int", c_name="fputs"),
    # fclose(FILE*) -> 0 on success.
    "fclose": Func(arg_types=("str",), ret_type="int", c_name="fclose"),
    # access(path, mode) -> 0 if the path is accessible for `mode`
    # (mode 0 = existence). On Windows the CRT spells it `_access`; the
    # `asmpython.stdlib.ospath` helpers hide that difference.
    "_access": Func(arg_types=("str", "int"), ret_type="int", c_name="access"),
}
