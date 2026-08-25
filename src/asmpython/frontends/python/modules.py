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
* `("callv", symbol)` -- VARIADIC: the arguments arrive as one tuple, which is
  how `asyncio.gather(a, b, c)` reaches an entry point with no fixed arity.
* `("tuple", (n, ...))` -- a tuple of integers, emitted element by element.
* `("form", name)` -- a `typing` special form: an object a program names in an
  annotation and never inspects.
* `("ns", {members})` -- a NESTED NAMESPACE, built as a type object with
  attributes. `sys.implementation.name` is one; its members follow the same
  rules as the top level, through the same code, so the two cannot drift.

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

#: `typing`. THE PART OF IT THAT EXISTS AT RUN TIME, WHICH IS NEARLY NONE.
#: Almost everything here is inert once the program runs: a special form is
#: an object a program names in an annotation and never inspects, and `final`
#: and `override` are decorators whose entire effect is to set one attribute
#: and hand the argument straight back.
#:
#: NOT HERE, deliberately: `get_type_hints`, `get_origin`, `get_args`. Those
#: read annotations as live objects and need `int | None` and `list[str]` to
#: be values with a repr, which is real machinery rather than a table entry.
#: Listing them as inert would turn honest refusals into wrong answers.
_TYPING = {
    name: ("form", name)
    for name in ("Any", "AnyStr", "Final", "LiteralString", "Never",
                 "NoReturn", "Self", "TypeAlias", "Optional", "Union",
                 "ClassVar", "Callable", "Iterable", "Iterator", "Sequence",
                 "Mapping", "List", "Dict", "Set", "Tuple", "Type", "Text",
                 "Hashable", "Sized", "Annotated", "Literal", "Protocol",
                 "Generic", "TypedDict", "Required", "NotRequired",
                 "ReadOnly", "Unpack", "Concatenate", "ParamSpec",
                 "TypeVarTuple", "TypeGuard", "TypeIs", "Counter",
                 "DefaultDict", "Deque", "FrozenSet", "NamedTuple")
}
_TYPING.update({
    # PEP 484's runtime introspection. Both read a generic alias, which is
    # what subscripting a form or a builtin type already produces.
    "get_origin": ("call", "apy_get_origin", 1),
    "get_args": ("call", "apy_get_args", 1),
    "final": ("call", "apy_typing_final", 1),
    "override": ("call", "apy_typing_override", 1),
    # `runtime_checkable` and `no_type_check` are the same shape: a decorator
    # that marks and returns. `dataclass_transform` takes only keywords and
    # returns a decorator, which is not this shape, so it stays out.
    "runtime_checkable": ("call", "apy_typing_mark", 1),
    "no_type_check": ("call", "apy_typing_mark", 1),
})

#: `asyncio`. A COROUTINE IS A GENERATOR here, so the scheduler is small: it
#: drives a frame that already knows how to suspend and resume.
#:
#: `sleep` DOES NOT WAIT, and no program here can observe duration: there is
#: no clock and no I/O in a freestanding image, so `sleep(10)` returns as fast
#: as `sleep(0)`.
#:
#: WHAT IT DOES GUARANTEE IS ORDER. The loop keeps a virtual clock and moves
#: it only to the next moment something can happen, so two coroutines sleeping
#: for different times wake shortest-first -- which is observable, and which
#: an implementation that merely suspended once got wrong while passing every
#: conformance case in the suite.
_ASYNCIO = {
    "run": ("call", "apy_asyncio_run", 1),
    "sleep": ("call", "apy_asyncio_sleep", 1),
    # Variadic: `gather(a, b, c)` has no fixed arity, so its arguments arrive
    # as one tuple. See `_dyn_native_value`.
    "gather": ("callv", "apy_asyncio_gather"),
    # A TASK IS A COROUTINE THE LOOP OWNS, and that is the whole difference
    # from `await`: it runs in the gaps, whenever whatever is being driven
    # suspends. See the task layer in objects/csource.py.
    "create_task": ("call", "apy_asyncio_create_task", 1),
    # THE PARAMETER NAMES, because `wait_for(c, timeout=1)` is how every
    # program writes it -- the keyword is not optional in practice even
    # though the parameter is positional.
    "wait_for": ("call", "apy_asyncio_wait_for", 2, ("fut", "timeout")),
    "TaskGroup": ("call", "apy_asyncio_taskgroup", 0),
    # THE EXCEPTIONS, as values. `except asyncio.CancelledError:` matches on
    # the name and never needs one -- see `_one_handler_name` -- but
    # `asyncio.TimeoutError is TimeoutError` is a question about objects, and
    # a program that raises one writes it out.
    "CancelledError": ("exc", "CancelledError"),
    "TimeoutError": ("exc", "TimeoutError"),
    "InvalidStateError": ("exc", "InvalidStateError"),
}

#: `inspect`. ONLY THE COROUTINE QUESTIONS -- what kind of frame is this, and
#: does calling that build one. A program importing `inspect` in async code is
#: nearly always asking one of them; the rest of the module is introspection
#: over source text and signatures that this compiler does not keep at run
#: time, and claiming more would be worse than claiming less.
_INSPECT = {
    "iscoroutine": ("call", "apy_inspect_iscoroutine", 1),
    "isgenerator": ("call", "apy_inspect_isgenerator", 1),
    "isasyncgen": ("call", "apy_inspect_isasyncgen", 1),
    "iscoroutinefunction": ("call", "apy_inspect_iscoroutinefunction", 1),
}

#: `sys`. ONLY WHAT CAN BE ANSWERED HONESTLY: this is a compiler, and most of
#: `sys` describes a running interpreter that is not there -- no `argv` for a
#: freestanding image, no `modules` because there is no import machinery, no
#: `settrace` because there are no frames to trace. What it does know is which
#: implementation compiled the program.
_SYS = {
    "implementation": ("ns", {
        "name": ("str", "asmpython"),
        "version": ("tuple", (3, 14, 0)),
        "hexversion": ("int", 0x030E00F0),
        "cache_tag": ("str", "asmpython-314"),
    }),
    "maxsize": ("int", 2 ** 63 - 1),
    "byteorder": ("str", "little"),
    "platform": ("str", "asmpython"),
}

BUILTIN_MODULES = {
    "math": _MATH,
    "typing": _TYPING,
    "asyncio": _ASYNCIO,
    "inspect": _INSPECT,
    "sys": _SYS,
    "__future__": _FUTURE,
}

#: The selected backend's id, and the modules it offers. Published by the
#: driver before the frontend runs, because which backend is compiling decides
#: which names are importable -- a backend for a board can offer the board.
_BACKEND_ID = ""
_BACKEND_MODULES: dict[str, dict] = {}
#: How to reach one of the backend's types by its own internal name, or None.
#: A namespace lists the types in a PACKAGE; an instance method call starts
#: from a value whose type names a class directly, which is a different
#: question and needs a different door.
_BACKEND_TYPE = None


def type_of(internal: str):
    """The backend's table for one of its own types, or None."""
    return None if _BACKEND_TYPE is None else _BACKEND_TYPE(internal)


def use_backend(backend_id: str, modules: dict, type_lookup=None) -> None:
    """Make `modules` importable for this compilation.

    Called once per compile, before the frontend runs. Replacing rather than
    merging: two compiles in one process must not see each other's backends,
    which is the shape every test that compiles twice has.
    """
    global _BACKEND_ID, _BACKEND_MODULES, _BACKEND_TYPE
    _BACKEND_ID = backend_id
    _BACKEND_MODULES = dict(modules or {})
    _BACKEND_TYPE = type_lookup


def backend_modules() -> dict:
    """Every module the selected backend offers, by name.

    For the lowerer, which has to DECLARE an intrinsic as an external before
    any body that calls one is lowered -- and a member is only discovered
    while lowering, which is too late.
    """
    return dict(_BACKEND_MODULES)


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
