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
    #
    # When the C function returns a real 64-bit pointer/handle (a
    # SDL_Window*, SDL_Renderer*, etc.) rather than a genuine 32-bit C `int`
    # -- asmpython has no separate pointer type, so these are still declared
    # ret_type="int" -- set this to "ptr" so codegen keeps the full 64-bit
    # RAX from the callee instead of sign-extending just EAX. Sign-extending
    # a real pointer truncates it to its low 32 bits (and can flip sign on
    # the high half), corrupting any heap address that doesn't happen to
    # fit and round-trip through a signed 32-bit value -- which most don't.
    ret_conv: str | None = None
    # Trailing default argument VALUES, so a short call is padded rather than
    # rejected. An FFI binding declares a fixed C arity, but the CPython
    # function it stands in for very often has optional tail parameters
    # (`math.isclose(a, b, rel_tol=1e-09, abs_tol=0.0)`,
    # `random.randrange(start, stop)`). Sema fills these in at the call site,
    # so one mechanism covers every such binding instead of a per-function
    # special case. `defaults[-k:]` line up with `arg_types[-k:]`.
    defaults: tuple = ()
    # This binding's CPython counterpart is VARIADIC and associative
    # (`math.gcd(*integers)`, `math.hypot(*coordinates)`, `math.lcm`). The C
    # symbol is binary, so sema folds an N-argument call left-to-right into
    # nested binary calls -- mathematically identical for all three.
    variadic_fold: bool = False
    # The CPython function this stands in for returns a `bool`. asmpython has
    # no separate bool type (bool IS int everywhere), so the binding still
    # declares ret_type="int" -- this only tells print()/str()/f-strings to
    # render True/False instead of 1/0, via `is_bool_expr`.
    ret_bool: bool = False
    # This binding returns an ELEMENT of one of its arguments, not a value of a
    # fixed kind -- `random.choice(seq)` gives back one of seq's items. The
    # value is the 0-based index of that argument; sema types the call as that
    # sequence's element kind. `ret_type` stays the fallback for a source whose
    # element kind isn't tracked.
    ret_from_element: "int | None" = None


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
from ._gui_sdl      import BINDINGS as _GUI_BINDINGS           # noqa: E402
from ._gui_ttf      import BINDINGS as _TTF_BINDINGS           # noqa: E402
from ._audio_sdl    import BINDINGS as _AUDIO_BINDINGS         # noqa: E402
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
    "_gui_sdl":      _GUI_BINDINGS,
    "_gui_ttf":      _TTF_BINDINGS,
    "_audio_sdl":    _AUDIO_BINDINGS,
    "network":       _NETWORK_BINDINGS,
    "hardware":      _HARDWARE_BINDINGS,
}


# A bindings dict may map this key to `callable(subpath) -> dict | None`,
# letting the module answer for its own subpaths. That is what makes a
# namespace too large to enumerate importable: nothing can list every class in
# every Java package, but the module owning that namespace can resolve one on
# request. The registry itself names no namespace -- whoever registered the
# prefix decides.
SUBMODULE_RESOLVER = "__resolve_submodule__"


def register_bindings(module: str, bindings: dict, *, replace: bool = False) -> None:
    """Contribute an FFI binding module from outside this package.

    A BACKEND is the expected caller. Its intrinsics are its own business --
    calling Java is meaningful to the JVM backend and meaningless to x86-64 --
    so the module that declares them belongs next to the backend, not in this
    package. What lives here is the registry, which knows nothing about who
    fills it.

    `_backends/arm64/source_build.py` already swaps `math` for an arm64 build
    by assigning into STDLIB_BINDINGS directly; this is the same move with a
    name, so a backend adding a module does not have to reach into a dict and
    hope about ordering.

    Registering the same name twice is refused unless `replace` says so: two
    backends silently fighting over one module name is a bug that would only
    surface as the wrong symbol being called.
    """
    existing = STDLIB_BINDINGS.get(module)
    if existing is not None and existing is not bindings and not replace:
        raise ValueError(
            f"binding module {module!r} is already registered; "
            "pass replace=True to override it deliberately"
        )
    STDLIB_BINDINGS[module] = bindings
