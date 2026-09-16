"""Controlled access to annotations -- PEP 649 and PEP 749, new in 3.14.

COVERAGE: `Format` (`VALUE`, `FORWARDREF`, `STRING`, with CPython's own integer
values so a program comparing against a literal `1`/`3`/`4` still agrees);
`get_annotations` for `format=Format.VALUE` (the default and the only format
this runtime can produce), on functions, classes and plain objects, with
`eval_str` un-stringizing an annotation a program wrote as a literal string
(`x: "SomeClass"`, `x: "list[int]"`); `call_annotate_function` and
`call_evaluate_function` for `Format.VALUE`; `get_annotate_from_class_namespace`;
`ForwardRef` -- construction, equality, repr, and `.evaluate()` in all three
formats.

NOT COVERED, and REFUSED BY NAME rather than accepted and given the wrong
answer: `format=Format.FORWARDREF` and `format=Format.STRING` in
`get_annotations` and `call_annotate_function` -- this runtime's
`__annotate__` thunk does not have what either needs, which is the one
limit here and is explained below.

## Why VALUE is what this runtime can give

`objects/c/_descriptors.py` shows the whole mechanism. A `def` with
annotations compiles to a THUNK -- a zero-argument function built from the
annotation expressions, stored as `__annotate__` -- and reading
`f.__annotations__` calls it: `apy_call_n(O(obj)->v.fn.annotate, NULL, 0)`.
For a class the same thunk exists, but reading `C.__annotations__` checks a
STORED dict first and falls back to the thunk only if nothing was stored --
`docs/STDLIB.md`'s dataclasses section is the place that fix is told in full;
`make_dataclass` is the reason it exists. Either way, the thunk EVALUATES: it
runs the annotation expressions as ordinary code and returns real objects.
There is no intermediate form -- no bytecode, no AST, no source string -- kept
anywhere alongside it. So `Format.VALUE` is not one implementation choice
among three; it is the only thing the thunk was ever built to produce.

`Format.STRING` would need the thunk to hand back source text instead of
values. CPython's compiler keeps that text (or defers via a special
`_Stringifier` evaluation environment, in `annotationlib.py` itself, that
turns every name lookup into a lazy stand-in and unparses the result).
Neither exists here: the frontend emits real expression code directly into
the thunk's body (`_dyn_emit_annotate_thunks`), and there is no unparser
bundled to hand a source-shaped answer back even if there were.

`Format.FORWARDREF` needs the thunk to keep going when ONE name in the
dict fails to resolve, wrapping only that entry in a `ForwardRef` and
returning every other entry as normal. This runtime's thunk cannot do that:
it builds the annotations dict UNRESOLVED-NAME-EAGERLY, in one pass, and
`_dyn_emit_annotate_thunks` raises the `NameError` and returns NULL the
moment it reaches a name that is not defined -- so a single bad name loses
the WHOLE dict, not just its own entry. This matches CPython's own
`Format.VALUE` behaviour exactly (`def f(x: Undefined)` is a legal
definition and only reading its annotations raises), which is why `VALUE`
needs nothing extra; there is simply no per-entry fallback to build
`FORWARDREF` out of.

Calling the thunk with an ARGUMENT does not raise here, even though
CPython's compiler-generated `__annotate__` functions take exactly one
parameter (`format`) and this runtime's take zero: `annotate(Format.VALUE)`
works and the extra argument is silently ignored, returning the same VALUE
dict regardless of what format was asked for. That is not this module's
protection -- it is a general property of this compiler's calling
convention (an ordinary user function accepts MORE positional arguments
than it declares without complaint, the same permissive-superset shape
`dataclasses.py` documents for `field(3)`) -- so `get_annotations` and
`call_annotate_function` check `format` themselves, BEFORE calling
anything, rather than trusting the thunk to refuse a format it cannot
honour. A thunk that silently answered VALUE data for a STRING request is
exactly the "accepted and ignored" failure this rebuild exists to catch.

## How a forward reference is resolved, and the compiler gap this module found

`ForwardRef.evaluate()` and `get_annotations(..., eval_str=True)` both turn a
piece of text back into a value, which is what `eval()` is for. When this
module was first written, calling `eval()` from here crashed the COMPILER
rather than the compiled program: `KeyError: 'eval'` out of
`frontends/python/dynamic.py`'s `_dyn_call`, for a name that is plainly a
Python builtin.

The cause was in `bundled.py`. `splice()` rewrites `eval`/`exec`/`compile` to
the bundled `_pyrun`/`_pycompile` implementations, and did so only in the
PROGRAM'S OWN source tree (`_Rewrite`, and the `uses` scan that decides
whether `_pyrun` is spliced in at all, both walk the caller's tree BEFORE any
bundled module is merged into it). A bundled module's OWN body is rewritten
by a DIFFERENT visitor, `_Rename`, whose job is mangling the module's own
top-level names and its references to other bundled modules --
`_RUNTIME_COMPILER` did not enter into it, and `_dependencies()` followed
only `import` statements, never a bare `eval`/`exec`/`compile` name. So a
program that never called `eval` ITSELF never got `_pyrun` spliced in even
when a bundled module it imported called `eval` internally, and the name
reached lowering unrewritten -- not a defined function and not a known
builtin, which is the `KeyError` above rather than a diagnostic. Nothing
caught it because no bundled module before this one called any of the three
from its own body.

THAT GAP IS CLOSED. `_dependencies()` now scans each bundled module's tree
for the same three names `splice()`'s own `uses` scan looks for and visits
the provider, and `_Rename` rewrites them alongside the names it already
mangled. `tests/stdlib/annotationlib.py` is what holds it closed: the
non-identifier cases below reach `eval()` for real.

A BARE IDENTIFIER STILL DOES NOT GO THROUGH `eval()`, and that is a choice
rather than a leftover. It is what almost every forward reference is
(`ForwardRef("SomeClass")`, not `ForwardRef("some_class.Attr[int]")`), it
needs no interpreter -- only `locals`, then `globals`, then a small table of
the builtin types an annotation is written with -- and it is the one path
whose namespace this module controls.

NO IMPLICIT BUILTINS FALLBACK BEYOND THAT SMALL TABLE, for a bare name.
CPython's own `ForwardRef.evaluate()` falls back to `hasattr(builtins, arg)`
for any builtin name, including ones this frontend refuses to let a program
name as a bare VALUE (`bytearray`, `range`, `memoryview`, `complex` -- see
`dataclasses.py`'s mutable-default check and `docs/STDLIB.md`'s
`collections.abc` entry for the same restriction from two other angles), so
the table here is the safe subset: `int`, `str`, `float`, `bool`, `bytes`,
`list`, `tuple`, `dict`, `set`, `frozenset`, `object`, `type`. A name outside
`locals`/`globals` and outside that table raises `NameError`, matching
CPython's own message text for an undefined name. Text that is NOT a bare
identifier goes to `eval()`, which resolves a free name against `_pyrun`'s
own wider builtins table -- so `"range[int]"` resolves where `"range"` alone
does not. That asymmetry is deliberate: the wide table is `_pyrun`'s to
define, and narrowing it from here would mean re-implementing it.
"""
import enum


class Format(enum.IntEnum):
    """Which shape `get_annotations` hands annotations back in.

    THE SAME INTEGER VALUES CPython USES -- `VALUE` is 1, `FORWARDREF` is 3,
    `STRING` is 4 -- so a program that compares a format against a literal
    int, or that this module refuses by number as well as by name, still
    agrees with the real one. `VALUE_WITH_FAKE_GLOBALS` (2) is CPython's own
    internal-use-only member and is not given a name here.
    """
    VALUE = 1
    FORWARDREF = 3
    STRING = 4


#: The safe subset of `hasattr(builtins, arg)`: builtin types a program may
#: freely name as a VALUE here. `bytearray`, `range`, `memoryview` and
#: `complex` are deliberately absent -- this frontend refuses each as a bare
#: value (`E0056`), the same restriction `dataclasses.py`'s mutable-default
#: check and `docs/STDLIB.md`'s `collections.abc` entry both work around --
#: so writing them into this table would fail to COMPILE, not just to match.
_BUILTIN_NAMES = {
    "int": int, "str": str, "float": float, "bool": bool, "bytes": bytes,
    "list": list, "tuple": tuple, "dict": dict, "set": set,
    "frozenset": frozenset, "object": object, "type": type,
}


def _resolve_name(text, globals, locals):
    """`locals`, then `globals`, then the safe builtin table for a BARE
    IDENTIFIER; `eval()` for anything else.

    THE BARE-IDENTIFIER PATH IS NOT AN OPTIMISATION. It is what almost every
    forward reference is (`ForwardRef("SomeClass")`), it needs no interpreter,
    and it is the one path whose namespace this module controls: `eval()`
    resolves a free name against `_pyrun`'s OWN builtins table, which is wider
    than `_BUILTIN_NAMES` on purpose (see the module docstring for why the
    table here is the narrow one). Sending a bare name to `eval()` instead
    would quietly widen that table for the commonest case.

    Anything with an operator, a call or a subscript in it -- `"list[int]"`,
    `"int | None"` -- is what `eval()` is for, and is what CPython resolves it
    with too. Whatever `eval()` cannot parse or cannot evaluate raises from
    there, with `_pyrun`'s own message; this function adds no refusal of its
    own.
    """
    if text.isidentifier():
        if text in locals:
            return locals[text]
        if text in globals:
            return globals[text]
        if text in _BUILTIN_NAMES:
            return _BUILTIN_NAMES[text]
        raise NameError("name '%s' is not defined" % (text,))
    return eval(text, globals, locals)


def _refuse(format, where):
    if format == Format.FORWARDREF:
        what = ("this runtime's __annotate__ thunk evaluates every name in "
                "one eager pass and raises on the first that cannot resolve "
                "-- there is no per-entry fallback to wrap only the bad name "
                "in a ForwardRef")
    elif format == Format.STRING:
        what = ("this runtime's __annotate__ thunk is compiled expression "
                "code, not kept source text, and there is no unparser here "
                "to rebuild one")
    else:
        raise ValueError("Unsupported format %r" % (format,))
    return NotImplementedError(
        "%s does not support format=%r: %s. See "
        "bundled/annotationlib.py and docs/STDLIB.md." % (where, format, what))


# ── ForwardRef ───────────────────────────────────────────────────────────────

class ForwardRef:
    """A deferred annotation, holding its own source text.

    Built BY HAND -- `get_annotations` never manufactures one, because this
    runtime's thunk has no per-entry failure to wrap (see the module
    docstring). What CPython's `typing` module does with a quoted annotation
    -- keep the text and evaluate it later, against a namespace that may not
    have existed yet at definition time -- this class still does, since that
    only needs the text the caller supplied, not anything the compiler kept.
    """

    def __init__(self, arg, *, module=None, owner=None, is_argument=True,
                 is_class=False):
        if not isinstance(arg, str):
            raise TypeError(
                "Forward reference must be a string -- got %r" % (arg,))
        self.__arg__ = arg
        self.__forward_is_argument__ = is_argument
        self.__forward_is_class__ = is_class
        self.__forward_module__ = module
        self.__owner__ = owner

    @property
    def __forward_arg__(self):
        return self.__arg__

    def evaluate(self, *, globals=None, locals=None, type_params=None,
                 owner=None, format=Format.VALUE):
        """Evaluate the held text and return the value.

        `Format.STRING` needs nothing but the text this object was built
        with. `Format.FORWARDREF` evaluates it and, if the ONE name it names
        cannot be found, returns `self` instead of raising -- which is exactly
        the case this runtime's own thunk cannot offer for a whole
        `__annotate__` dict (see the module docstring), but can here, because
        there is only one expression and it is not the compiler's to defer.
        `Format.VALUE` is the plain evaluation, raising if the name is
        undefined -- CPython's own answer for an annotation nothing was ever
        told to keep lazy past this point.

        `type_params` IS ACCEPTED AND IGNORED. CPython injects PEP 695 type
        parameters into the evaluation namespace with it; this frontend does
        not compile `type` statements or generic `class`/`def` parameter
        lists, so there is never one to inject.

        A BARE IDENTIFIER is resolved by lookup and anything else by
        `eval()` -- see `_resolve_name`, and the module docstring for why the
        two paths differ.
        """
        if format == Format.STRING:
            return self.__forward_arg__
        if format != Format.VALUE and format != Format.FORWARDREF:
            raise ValueError("Invalid format: %r" % (format,))
        is_forwardref_format = format == Format.FORWARDREF
        if owner is None:
            owner = self.__owner__
        if globals is None:
            globals = {}
            # A CALLABLE OWNER LENDS ITS OWN GLOBALS, the way CPython's does
            # -- there is no `sys.modules` lookup by module NAME here, because
            # this runtime keeps no live module registry to search (see
            # `docs/STDLIB.md`: "no module object, no import system"). A
            # caller naming a module by string has to pass `globals=`
            # itself; an owner that is a live function or class does not.
            if callable(owner) and not isinstance(owner, type):
                got = getattr(owner, "__globals__", None)
                if got is not None:
                    globals = got
        if locals is None:
            locals = {}
            if isinstance(owner, type):
                locals = dict(vars(owner))
        try:
            return _resolve_name(self.__forward_arg__, globals, locals)
        except NameError:
            if is_forwardref_format:
                return self
            raise

    def __eq__(self, other):
        if not isinstance(other, ForwardRef):
            return NotImplemented
        return (self.__forward_arg__ == other.__forward_arg__
                and self.__forward_module__ == other.__forward_module__
                and self.__forward_is_class__ == other.__forward_is_class__)

    def __hash__(self):
        return hash((self.__forward_arg__, self.__forward_module__,
                     self.__forward_is_class__))

    def __repr__(self):
        extra = ""
        if self.__forward_module__ is not None:
            extra = extra + (", module=%r" % (self.__forward_module__,))
        if self.__forward_is_class__:
            extra = extra + ", is_class=True"
        if self.__owner__ is not None:
            extra = extra + (", owner=%r" % (self.__owner__,))
        return "ForwardRef(%r%s)" % (self.__forward_arg__, extra)


# ── calling an __annotate__-shaped function ─────────────────────────────────

def call_annotate_function(annotate, format, *, owner=None):
    """Call an `__annotate__` function and return its dict.

    ONLY `Format.VALUE` -- see the module docstring for why the other two
    are refused here rather than left to the thunk, which would not refuse
    them itself and would answer VALUE data regardless of what was asked.
    `format` is passed through positionally, matching CPython's own
    `__annotate__` signature, even though this runtime's compiler-generated
    thunks take no parameter at all: calling one with an extra argument does
    not raise here (see the module docstring), so the call is written the
    portable way and works whether `annotate` is one of those thunks or an
    ordinary one-parameter function a program wrote by hand.
    """
    if format != Format.VALUE:
        raise _refuse(format, "call_annotate_function")
    return annotate(format)


def call_evaluate_function(evaluate, format, *, owner=None):
    """`call_annotate_function`, for the evaluate functions PEP 649 also
    generates for a type alias's value and a type parameter's bound. Only
    `Format.VALUE` differs between the two in CPython, in how a STRING or
    FORWARDREF result is reshaped afterwards -- and this module does not
    reach that code, so the two functions are identical here.
    """
    return call_annotate_function(evaluate, format, owner=owner)


def get_annotate_from_class_namespace(obj):
    """The `__annotate__` a class BODY defined, read from its namespace dict
    before the class object exists -- for a metaclass's `__new__`. Ordinary
    dict access; nothing about this needs the compiler's cooperation."""
    try:
        return obj["__annotate__"]
    except KeyError:
        return obj.get("__annotate_func__", None)


# ── get_annotations ─────────────────────────────────────────────────────────

def _get_dunder_annotations(obj):
    """`obj.__annotations__`, or None if it is genuinely absent.

    A PLAIN `getattr` is enough here -- unlike CPython, which special-cases a
    class to bypass a possible instance-level shadow of the class attribute.
    This runtime's own class-attribute read (`objects/c/_descriptors.py`,
    `APY_TYPE_K`) already answers only the class's OWN dict, never an
    instance's and never a base's, so there is no shadow to bypass.
    """
    ann = getattr(obj, "__annotations__", None)
    if ann is None:
        return None
    if not isinstance(ann, dict):
        raise ValueError(
            "%r.__annotations__ is neither a dict nor None" % (obj,))
    return ann


def _get_and_call_annotate(obj, format):
    annotate = getattr(obj, "__annotate__", None)
    if annotate is None:
        return None
    ann = call_annotate_function(annotate, format, owner=obj)
    if not isinstance(ann, dict):
        raise ValueError("%r.__annotate__ returned a non-dict" % (obj,))
    return ann


def get_annotations(obj, *, globals=None, locals=None, eval_str=False,
                    format=Format.VALUE):
    """The annotations of a function, class, or other annotated object.

    `obj` may be anything with `__annotations__` or `__annotate__` -- a
    function and a class both already have the runtime's own PEP 649
    machinery behind `getattr(obj, "__annotations__", ...)` (see the module
    docstring), and any other object is handled the general way, by calling
    its `__annotate__` if `__annotations__` is not already there.

    ONLY `format=Format.VALUE`, the default, is supported: it is what this
    runtime's `__annotate__` thunk was ever built to produce. Passing
    `Format.FORWARDREF` or `Format.STRING` raises `NotImplementedError`
    naming which piece is missing, rather than silently answering VALUE data
    for either -- see the module docstring for exactly what each would need.

    `eval_str` un-stringizes a `str`-valued entry -- which covers both a
    program's own quoted annotation (`x: "SomeClass"`) and every entry when
    the module used `from __future__ import annotations` (PEP 563 is a real
    compiler rewrite here; see `frontends/python/__init__.py`'s `_Stringify`
    -- it replaces each annotation with its own `ast.unparse`d source, so
    under it every value already IS a string before this function ever sees
    it). A bare identifier is resolved by lookup and anything else --
    `"list[int]"`, `"int | None"` -- by `eval()`; see `_resolve_name`.
    `globals`/`locals` are
    consulted the same way `ForwardRef.evaluate` consults them; unlike
    CPython, this runtime keeps no live module registry to default them from
    (see `docs/STDLIB.md`: "no module object, no import system"), so a class
    defaults `locals` to its own namespace and nothing defaults `globals` --
    pass it explicitly for a name defined outside the class or function being
    asked about.
    """
    if format != Format.VALUE:
        raise _refuse(format, "get_annotations")

    ann = _get_dunder_annotations(obj)
    if ann is None:
        ann = _get_and_call_annotate(obj, format)
    if ann is None:
        if isinstance(obj, type) or callable(obj):
            return {}
        raise TypeError("%r does not have annotations" % (obj,))
    if not ann:
        return {}
    if not eval_str:
        return dict(ann)

    if globals is None:
        globals = {}
    if locals is None:
        locals = dict(vars(obj)) if isinstance(obj, type) else {}
    out = {}
    for key in ann:
        value = ann[key]
        out[key] = value if not isinstance(value, str) \
            else _resolve_name(value, globals, locals)
    return out


__all__ = [
    "Format", "ForwardRef", "call_annotate_function", "call_evaluate_function",
    "get_annotate_from_class_namespace", "get_annotations",
]
