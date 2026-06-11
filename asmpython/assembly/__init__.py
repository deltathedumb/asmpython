"""Public API for writing assembly that asmpython can compile and call.

Two entry points, both recognised specially by the compiler:

    from asmpython.assembly import assembly_func, include

`@assembly_func`
----------------
Marks a function whose *body* is raw NASM x86-64. The Python signature carries
the contract the compiler needs — the symbol name, the parameter types, and the
return type — while the triple-quoted docstring carries the instructions that
are emitted verbatim as the function's body:

    @assembly_func
    def add(a: int, b: int) -> int:
        \"\"\"
        ; args arrive per the target ABI (rdi/rsi on SysV, rcx/rdx on Win64)
        mov rax, rdi
        add rax, rsi
        ret
        \"\"\"

Ordinary asmpython code then calls `add(2, 3)` like any other function; the
compiler routes the call to the inline-asm body instead of generating one.

`include(name)`
---------------
Pulls in an *assembly package* — a ``<name>.asmpkg`` directory or file that
ships a blob of NASM plus a manifest of the symbols it exports (see
``asmpython/assembly/pkgformat.py``). Included packages lay the groundwork for
``--freestanding`` builds, where the whole runtime is supplied as .asmpkg
instead of linked from libc.

Both functions are *compile-time directives*. Under plain CPython they are
inert: `assembly_func` returns a stub that raises if you actually call it (the
NASM body only means something to the compiler), and `include` just records the
request in a module-level registry so tooling can introspect it. This keeps a
source file importable by linters and type-checkers even though only the
asmpython compiler gives the directives meaning.
"""

from __future__ import annotations

from typing import Callable


__all__ = ["assembly_func", "include", "AssemblyFunc", "included_packages"]


# Packages requested via include(), in source order. The compiler reads its own
# copy out of the AST; this list is what a CPython import sees, so tooling can
# inspect which packages a module pulls in.
_INCLUDES: list[str] = []


class AssemblyFunc:
    """Marker wrapper the `@assembly_func` decorator returns under CPython.

    Holds the NASM body and the declared symbol so introspection works, but
    raises on call: the body is machine code meant for the asmpython compiler,
    not something CPython can execute.
    """

    def __init__(self, fn: Callable, *, symbol: str | None = None) -> None:
        self._fn = fn
        self.name = fn.__name__
        self.symbol = symbol or fn.__name__
        # The raw NASM body lives in the function's docstring.
        self.asm = (fn.__doc__ or "").strip()
        # Preserve dunders so the stub still looks like the original function.
        self.__name__ = fn.__name__
        self.__doc__ = fn.__doc__
        self.__wrapped__ = fn

    def __call__(self, *args, **kwargs):
        raise RuntimeError(
            f"@assembly_func {self.name!r} has a raw-NASM body and can only run "
            f"after compilation with asmpython; it is not callable under CPython."
        )

    def __repr__(self) -> str:
        return f"<assembly_func {self.name!r} symbol={self.symbol!r}>"


def assembly_func(fn: Callable | None = None, *, symbol: str | None = None):
    """Mark a function as having a raw-NASM body (see module docstring).

    Usable bare (`@assembly_func`) or parameterised
    (`@assembly_func(symbol="my_add")`) to override the emitted symbol name.
    """
    if fn is None:
        # Called with arguments: @assembly_func(symbol=...). Return the real
        # decorator.
        def _decorate(real_fn: Callable) -> AssemblyFunc:
            return AssemblyFunc(real_fn, symbol=symbol)

        return _decorate
    return AssemblyFunc(fn, symbol=symbol)


def include(name: str) -> None:
    """Request that the assembly package ``name`` be linked into the program.

    A compile-time directive: under CPython it only records the request. The
    asmpython compiler resolves ``name`` to a ``.asmpkg`` and links its symbols.
    """
    if not isinstance(name, str) or not name:
        raise TypeError("include() takes a non-empty package name string")
    _INCLUDES.append(name)


def included_packages() -> list[str]:
    """The package names requested via include() so far, in order."""
    return list(_INCLUDES)
