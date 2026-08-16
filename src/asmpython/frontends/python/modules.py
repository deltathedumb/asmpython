"""The modules `import` can reach, and what each one holds.

A TABLE, not a search path. There is no file system at run time and no second
compilation unit: a module here is built at the `import` statement out of
constants and thin wrappers around runtime entry points, and bound to a
namespace value like any other object. `import math` costs a handful of
instructions where the statement is written, and `math.pi` afterwards is an
ordinary attribute lookup.

That is deliberately not a general import system. A general one needs to
compile a second source file into the same program -- which the analyser, with
one `functions` map and one module scope, does not model -- and it needs a
standard library written in Python to compile. Both are real work and neither
is what a program saying `import math` is asking for.

Each member is `(kind, payload)`:

* `("float", x)` / `("int", n)` / `("str", s)` -- a constant, emitted as a
  literal.
* `("call", symbol, arity)` -- a runtime function, wrapped in a callable value
  so that `math.sqrt` can be passed as well as called.
* `("call", symbol, arity, params, defaults)` -- the same, with PARAMETER
  NAMES so a keyword argument can find its slot, and float defaults for the
  trailing ones. `math.isclose(a, b, rel_tol=1e-9)` needs both.

Both analysis and lowering read this: analysis to know a name exists and
lowering to emit it. Two copies would drift, and the way they would drift is
a member that type-checks and then fails to link.
"""
from __future__ import annotations

import math as _math

#: `math`. The pure numeric functions, which is the whole module: everything
#: in it is a function of its arguments, so none of it needs state and all of
#: it can be a runtime call.
_MATH = {
    "pi": ("float", _math.pi),
    "e": ("float", _math.e),
    "tau": ("float", _math.tau),
    "inf": ("float", _math.inf),
    "nan": ("float", _math.nan),
    "sqrt": ("call", "apy_math_sqrt", 1),
    "floor": ("call", "apy_math_floor", 1),
    "ceil": ("call", "apy_math_ceil", 1),
    "trunc": ("call", "apy_math_trunc", 1),
    "fabs": ("call", "apy_math_fabs", 1),
    "isnan": ("call", "apy_math_isnan", 1),
    "isinf": ("call", "apy_math_isinf", 1),
    "isfinite": ("call", "apy_math_isfinite", 1),
    "isqrt": ("call", "apy_math_isqrt", 1),
    "factorial": ("call", "apy_math_factorial", 1),
    "exp": ("call", "apy_math_exp", 1),
    "log": ("call", "apy_math_log", 1),
    "log2": ("call", "apy_math_log2", 1),
    "log10": ("call", "apy_math_log10", 1),
    "sin": ("call", "apy_math_sin", 1),
    "cos": ("call", "apy_math_cos", 1),
    "tan": ("call", "apy_math_tan", 1),
    "atan": ("call", "apy_math_atan", 1),
    "degrees": ("call", "apy_math_degrees", 1),
    "radians": ("call", "apy_math_radians", 1),
    "gcd": ("call", "apy_math_gcd", 2),
    "lcm": ("call", "apy_math_lcm", 2),
    "copysign": ("call", "apy_math_copysign", 2),
    "pow": ("call", "apy_math_pow", 2),
    "atan2": ("call", "apy_math_atan2", 2),
    "hypot": ("call", "apy_math_hypot", 2),
    # The tolerances are KEYWORDS in every real use, so this one carries its
    # parameter names and their defaults -- see `_dyn_native_value`.
    "isclose": ("call", "apy_math_isclose", 4,
                ("a", "b", "rel_tol", "abs_tol"), (1e-09, 0.0)),
}

#: `__future__`. Every flag it names is either already the behaviour here or
#: is about syntax the parser already accepts, so importing one is a no-op --
#: which is exactly what it is in CPython 3 too, for all but
#: `annotations`.
_FUTURE = {
    name: ("int", 1)
    for name in ("annotations", "division", "print_function",
                 "absolute_import", "unicode_literals", "generator_stop",
                 "nested_scopes", "with_statement", "generators")
}

BUILTIN_MODULES = {
    "math": _MATH,
    "__future__": _FUTURE,
}

#: The selected backend's id, and the modules it offers. Published by the
#: driver before the frontend runs, because which backend is compiling decides
#: which names are importable -- a backend for a board can offer the board.
_BACKEND_ID = ""
_BACKEND_MODULES: dict[str, dict] = {}


def use_backend(backend_id: str, modules: dict) -> None:
    """Make `modules` importable for this compilation.

    Called once per compile, before the frontend runs. Replacing rather than
    merging: two compiles in one process must not see each other's backends,
    which is the shape every test that compiles twice has.
    """
    global _BACKEND_ID, _BACKEND_MODULES
    _BACKEND_ID = backend_id
    _BACKEND_MODULES = dict(modules or {})


def resolve(name: str):
    """The member table `import <name>` reaches, or None if nothing does.

    THREE WAYS A NAME RESOLVES, in this order:

      1. `<backend>.<x>` -- the backend's own module, ALWAYS available. The
         prefixed path is not a fallback for a collision, it is the real name
         and it works whether or not anything else wants `x`.
      2. a standard module -- `math`, `__future__`. These WIN a bare name, so
         a program that said `import math` before a backend grew one of its
         own keeps meaning what it meant.
      3. the backend's module under its bare name, when nothing above took it.

    That is the whole collision rule: a backend author picks any name, and a
    clash costs the backend's module its bare name and nothing else.
    """
    if _BACKEND_ID and name.startswith(_BACKEND_ID + "."):
        return _BACKEND_MODULES.get(name[len(_BACKEND_ID) + 1:])
    if name in BUILTIN_MODULES:
        return BUILTIN_MODULES[name]
    return _BACKEND_MODULES.get(name)


def importable() -> list:
    """Every name `import` can reach, for a diagnostic to suggest."""
    out = list(BUILTIN_MODULES)
    out += [f"{_BACKEND_ID}.{name}" for name in _BACKEND_MODULES]
    out += [name for name in _BACKEND_MODULES if name not in BUILTIN_MODULES]
    return sorted(out)


def member(module: str, name: str):
    """One member of a module `import` can reach, or None if it has none."""
    table = resolve(module)
    return None if table is None else table.get(name)
