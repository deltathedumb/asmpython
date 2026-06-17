"""asmpython's standard-library bindings.

Each submodule here defines a `BINDINGS` dict:

    BINDINGS = {
        "name_in_asmpython": Func(arg_types=(...), ret_type="...", c_name="..."),
        "constant_name": Const(ty="float", value=3.14),
    }

The compiler reads these at import-resolution time and uses them to:
  - emit `extern <c_name>` declarations in the .asm
  - dispatch calls with the correct argument types into the C ABI
  - intern constants in .rodata and substitute them at use sites
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Func:
    """Foreign function: maps a asmpython name to a C ABI symbol with typed args."""
    arg_types: tuple[str, ...]   # each "int" | "float" | "str"
    ret_type: str                # "int" | "float" | "str"
    c_name: str
    c_name_windows: str | None = None  # override for Windows/msvcrt if symbol differs
    # When the C function returns a double (in xmm0) but `ret_type` is "int"
    # -- e.g. libm's trunc/floor/ceil, which CPython's math.trunc/floor/ceil
    # narrow to int -- set this to "f2i" so codegen truncates xmm0 to rax via
    # cvttsd2si instead of reading (garbage) eax/rax.
    ret_conv: str | None = None


@dataclass(frozen=True)
class Const:
    ty: str
    value: object
    # For ty == "list", the element type ("str", "int", ...). Unused otherwise.
    el_type: str | None = None
    # Override for Windows when the constant's value (e.g. a struct field
    # offset/size) differs by target, mirroring Func.c_name_windows.
    value_windows: object | None = None


# Static registry of every stdlib module's BINDINGS, keyed by the name the
# user writes (`import math` -> "math"). This is deliberately a plain dict
# built from explicit imports rather than `importlib.import_module(name)`:
# a string-keyed dynamic import is an interpreter-only feature the compiler
# cannot compile, so relying on it would make the compiler un-self-hostable.
#
# The bindings are pulled in with `from .<mod> import BINDINGS as ...` (plain
# name imports) rather than `from . import <mod>` + `<mod>.BINDINGS`: a module
# object with attribute access is itself an interpreter concept the compiler
# can't lower, so we keep this file inside the compilable subset too.
# Adding a stdlib module = add an import + one line here.
from .math          import BINDINGS as _MATH_BINDINGS          # noqa: E402
from .os            import BINDINGS as _OS_BINDINGS            # noqa: E402
from .sys           import BINDINGS as _SYS_BINDINGS           # noqa: E402
from .time          import BINDINGS as _TIME_BINDINGS          # noqa: E402
from .random        import BINDINGS as _RANDOM_BINDINGS        # noqa: E402
from .socket        import BINDINGS as _SOCKET_BINDINGS        # noqa: E402
from ._threadingffi import BINDINGS as _THREADINGFFI_BINDINGS  # noqa: E402
from .gui           import BINDINGS as _GUI_BINDINGS           # noqa: E402
from .network       import BINDINGS as _NETWORK_BINDINGS       # noqa: E402
from .hardware      import BINDINGS as _HARDWARE_BINDINGS      # noqa: E402

STDLIB_BINDINGS: dict[str, dict] = {
    "math":          _MATH_BINDINGS,
    "os":            _OS_BINDINGS,
    "sys":           _SYS_BINDINGS,
    "time":          _TIME_BINDINGS,
    "random":        _RANDOM_BINDINGS,
    "socket":        _SOCKET_BINDINGS,
    "_threadingffi": _THREADINGFFI_BINDINGS,
    "gui":           _GUI_BINDINGS,
    "network":       _NETWORK_BINDINGS,
    "hardware":      _HARDWARE_BINDINGS,
}
