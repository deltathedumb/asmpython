"""`typing`'s runtime -- the part of the module that actually DOES something.

COVERAGE: `TypeVar` (construction, `__name__`, `__bound__`, `__constraints__`,
`__covariant__`/`__contravariant__`, `repr`); `Generic` with a real
`__class_getitem__` -- `class Box(Generic[T])` then `Box[int]` answers an
object with genuine `.__origin__`/`.__args__` attributes; `NewType` (a real
identity-wrapping callable, with `__name__` and `__supertype__`); `cast` (a
real no-op passthrough); `overload` (a real decorator -- see below for why it
needs almost no machinery); `Protocol` + `runtime_checkable`, structural
`isinstance` checking built the same way `bundled/abc.py` builds one;
`get_type_hints`, built on the now-bundled `annotationlib.get_annotations`,
with `include_extras=False` stripping `Annotated` the way CPython's does;
`ParamSpec` (with `.args`/`.kwargs`) and `TypeVarTuple`, both real objects
rather than the inert form the native table had; `dataclass_transform`, PEP
681's marker, which returns its argument and leaves
`__dataclass_transform__` on it; `TypedDict` in BOTH FORMS, class-based and
functional, with PEP 655's `Required`/`NotRequired` and PEP 705's `ReadOnly`;
`NamedTuple` in the FUNCTIONAL FORM only -- see below for what separates the
two.

EVERYTHING ELSE `typing` NAMES -- `Any`, `Union`, `Optional`, `Callable`,
`Literal`, the container aliases, `Annotated`, `Required` and the rest of
PEP 484's annotation-only vocabulary, plus `final`, `override`,
`no_type_check`, `get_origin` and `get_args` -- stays exactly where it was:
`frontends/python/modules.py`'s native `_TYPING` table. Those names are
markers a program writes in an annotation and, mostly, never inspects at
run time -- CPython gives most of them no runtime behaviour either, so
reimplementing them here would be inventing behaviour CPython itself does
not have. This module does not redefine any of them, and `import typing`
still reaches the native table for whichever half of the name this file
does not define -- see `bundled.py`'s own note that `typing` is "bundled IN
PART", written before this file existed, for exactly this arrangement.

## Precedence: a bundled member always wins over the native table entry

`bundled.py`'s `splice()` decides, name by name, whether `typing.X` points at
something this file defines or at the native `_TYPING` table: `_Rewrite`
checks `X in members["typing"]` -- the names THIS FILE defines -- before it
ever asks `modules.resolve("typing")` about the native table. So defining
`Generic`, `Protocol`, `NamedTuple`, `TypedDict`, `TypeVar`, `NewType`,
`cast`, `overload`, `runtime_checkable` or `get_type_hints` here makes every
reference to that name -- `typing.X`, `from typing import X` -- resolve HERE
instead, automatically, with no change needed to `modules.py`'s `_TYPING`
table at all: the entries there for these same names simply stop being
reached. Checked by reading `splice()`'s `_Rewrite.visit_Attribute` and the
`ImportFrom` handling in `splice()` itself, both of which test bundled
membership first and only fall back to `_resolve()` -- the native table --
for a name this file does NOT define. The `import typing` statement itself
also survives the splice (`_resolve("typing") is not None` keeps it), which
is what lets the untouched half of the name -- `typing.Optional` and the
rest -- keep resolving through the native path exactly as before.

## The class body a metaclass cannot see, and what `TypedDict` does about it

CPython builds both `NamedTuple` and `TypedDict` from a class body:
`class Point(NamedTuple): x: int` works because `NamedTupleMeta.__new__`
(or, in 3.14, a `__mro_entries__` hook) reads `x`'s annotation out of the
class namespace it is handed. THAT NAMESPACE DOES NOT CARRY IT HERE. A bare
annotation with no assigned value (`x: int`, as opposed to `y: int = 0`)
never enters the `ns` dict a custom metaclass's `__new__` receives -- only
names the class body actually BINDS do, which is CPython's behaviour too
under PEP 649. Reading `cls.__annotations__` immediately after
`super().__new__()` inside that same `__new__` answers `{}` here where
CPython answers both fields: the annotation thunk is wired onto the bound
class object in a step AFTER the whole `class` statement finishes, keyed to
the name the statement binds, so reading it from inside the metaclass call
that produces that very object is too early, every time.

`TypedDict` IS COVERED IN BOTH FORMS ANYWAY, because it does not need the
fields when the class is made -- only when a program ASKS. Its four key sets
(`__required_keys__`, `__optional_keys__`, `__readonly_keys__`,
`__mutable_keys__`) are PROPERTIES ON THE METACLASS, computed from
`cls.__annotations__` at the moment they are read, which is always after the
class statement completed. That is the whole trick, and it is why PEP 655's
`Required`/`NotRequired` and PEP 705's `ReadOnly` work here as well.

ONE THING DOES NOT FOLLOW. `Sub.__annotations__` for a TypedDict inheriting
from another answers only what `Sub` DECLARED, where CPython answers the
merge of the whole chain -- CPython's `TypedDict` overwrites the attribute
during class creation, and here it is answered by the compiler's own thunk,
which a metaclass property cannot displace (measured: the property is not
consulted at all). The key sets DO merge, and those are what a program asks
about inheritance with.

`NamedTuple`'s class form IS STILL REFUSED, and for a reason `TypedDict`'s
does not share: a namedtuple needs the field ORDER to build its tuple class,
which is needed AT construction and cannot be deferred to first use. A form
that happened to work whenever every field carried a default -- silently
dropping annotation-only ones -- is precisely the "accepted and ignored"
shape `docs/STDLIB.md` exists to prevent (see its `islice`/`frozen=True`
examples), so it is refused rather than half-shipped. The FUNCTIONAL form
needs none of this: `NamedTuple('P', [('x', int)])` receives its fields as an
ordinary argument.

## A native-callable `**kwargs`-forwarding gap, found and routed around

`dict(*args, **kwargs)` -- BOTH forwarded together, from inside a function
that collected them -- silently dropped every keyword. THE LITERAL CALL IS
FIXED: `frontends/python/dynamic.py` now builds the positional half of any
`dict(...)` and applies the keywords to it, in every shape, which is why
`_TypedDictMeta.__call__` below can write `dict(*args, **kwargs)` plainly.

THE VALUE FORM IS FIXED TOO. `forward(dict, a=1)`, where the callee is a
parameter, reaches a SYNTHESISED THUNK -- `dynamic._dyn_builtin_value` --
whose body is the call the frontend would have emitted, and a thunk with no
keyword slot had nowhere to put them: it answered `{}`, silently.
`_KEYWORD_THUNKS` names the builtins whose value form declares `**kw`, and
the four on it (`dict`, `sorted`, `min`, `max`) now carry their keywords.

A DECLARED SET RATHER THAN ALL OF THEM, deliberately: handing a thunk
keywords its own lowering does not expect could turn a call that works today
into an error, so a name joins that list only once its answer has been
checked against CPython. A builtin outside it still drops them, and that is
what to widen next.

## `get_origin`/`get_args` and a user `Generic[T]` subscript

The native `get_origin`/`get_args` (still reached through the untouched
half of `_TYPING`) knew ONE run-time shape: the alias kind
`runtime/alias.py` builds for `list[int]`, `dict[str, int]` and a union --
unchanged by this file. A `Box[int]` built by THIS module's own
`Generic.__class_getitem__` is an ordinary instance of a plain Python class
(`_GenericAlias`, below), not that native kind, so `get_origin(Box[int])`
answered `None` rather than `Box`.

IT IS COVERED NOW, and not by rewriting the two functions here. Both shapes
carry the SAME TWO ATTRIBUTES -- `__origin__` and `__args__` -- so the
native pair reads them off an instance when the value is not the alias kind,
which adds the user generic without changing one answer for the builtin one.
That way a program wanting only `get_origin` still pays nothing: rewriting
them in this module would have spliced the whole of `typing` into it. See
`runtime/alias.py`, `objects/c/_classes.py` and `objects/host.py`, each
of which has the same two-line instance branch.

## `Protocol` covers method-based structural protocols, not attribute ones

The SAME limitation above about annotation-only class members applies here:
a `Protocol` class collects its structural attributes from its own
`cls.__dict__`, which holds a `def area(self): ...` (an ordinary bound
name) correctly, but would never see a bare `x: int` protocol member. Every
protocol this module can check is method-based -- which is the common case
-- and an attribute-only protocol is out of scope for the reason given above
rather than silently checked against nothing.

## `overload`

CPython's own `@overload` at run time is almost inert: the decorated stub
is replaced entirely by the final, undecorated `def` of the same name, so
the decorator's only OBSERVABLE job is to make a body that is somehow still
called directly (a stub reached by accident, or the very last `@overload` in
a stub file with no real implementation following) fail loudly rather than
silently returning `None`. There is no dispatch-by-signature at run time in
CPython either -- that is entirely a static type checker's job -- so this
needs no registry.
"""
import annotationlib
from abc import ABCMeta
from collections import namedtuple


# ── TypeVar ──────────────────────────────────────────────────────────────

class TypeVar:
    """A placeholder for a type in a generic signature.

    NOTHING HERE CHECKS ANYTHING -- exactly as in CPython, a `TypeVar` is a
    marker a type checker reasons about; the constructor's whole run-time
    job is to remember what it was built with.
    """

    def __init__(self, name, *constraints, bound=None, covariant=False,
                 contravariant=False, infer_variance=False):
        self.__name__ = name
        self.__constraints__ = constraints
        self.__bound__ = bound
        self.__covariant__ = covariant
        self.__contravariant__ = contravariant
        self.__infer_variance__ = infer_variance

    def __repr__(self):
        if self.__covariant__:
            prefix = "+"
        elif self.__contravariant__:
            prefix = "-"
        else:
            prefix = "~"
        return prefix + self.__name__


class _ParamSpecPart:
    """`P.args` or `P.kwargs` -- the two halves of a `ParamSpec`.

    A SEPARATE OBJECT, not a string, because what a program checks about one
    is `__origin__ is P`: PEP 612 says the pair belongs to the ParamSpec it
    came from, and that is the only run-time claim either makes.
    """

    def __init__(self, origin, part):
        self.__origin__ = origin
        self._part = part

    def __repr__(self):
        return self.__origin__.__name__ + "." + self._part


class ParamSpec:
    """PEP 612's placeholder for a whole PARAMETER LIST.

    A `TypeVar` stands for one type; this stands for the arguments of a
    callable, which is what lets a decorator say it takes and returns the
    same signature. Like `TypeVar`, nothing here checks anything -- the
    constructor's whole run-time job is to remember the name and to hand back
    `.args` and `.kwargs` when asked.
    """

    def __init__(self, name, *, bound=None, covariant=False,
                 contravariant=False, infer_variance=False):
        self.__name__ = name
        self.__bound__ = bound
        self.__covariant__ = covariant
        self.__contravariant__ = contravariant
        self.__infer_variance__ = infer_variance

    @property
    def args(self):
        return _ParamSpecPart(self, "args")

    @property
    def kwargs(self):
        return _ParamSpecPart(self, "kwargs")

    def __repr__(self):
        if self.__covariant__:
            return "+" + self.__name__
        if self.__contravariant__:
            return "-" + self.__name__
        return "~" + self.__name__


class TypeVarTuple:
    """PEP 646's placeholder for ANY NUMBER of types.

    `Ts` stands for a whole run of type arguments, so `Arr[int, str]` binds
    two where `Box[int]` binds one. The run-time object is the name and
    nothing else, and its repr is the BARE name -- unlike `TypeVar`'s, which
    carries a variance sigil that a variadic has no room for.
    """

    def __init__(self, name):
        self.__name__ = name

    def __repr__(self):
        return self.__name__


# ── Generic ──────────────────────────────────────────────────────────────

class _GenericAlias:
    """`Box[int]` -- what subscripting a `Generic` subclass answers.

    A PLAIN OBJECT WITH TWO REAL ATTRIBUTES, not the native alias kind
    `list[int]` builds -- see the module docstring for what that costs.
    """

    def __init__(self, origin, args):
        self.__origin__ = origin
        self.__args__ = args

    def __repr__(self):
        names = [getattr(a, "__name__", repr(a)) for a in self.__args__]
        return getattr(self.__origin__, "__name__",
                       repr(self.__origin__)) + "[" + ", ".join(names) + "]"


class Generic:
    """The base a program subclasses to make its own class subscriptable.

    `Generic[T]` USED AS A BASE COLLAPSES TO PLAIN `Generic`: this compiler
    has no `__mro_entries__` (PEP 560), which is what CPython uses to
    substitute a parameterised base for a real one at class-creation time.
    Returning `cls` itself when `cls is Generic` reaches the same class
    statement outcome -- `class Box(Generic[T])` ends up an ordinary
    `class Box(Generic)` -- at the cost of not recording which TypeVar was
    bound; nothing here reads that back, so it is not missed by anything
    this module implements.
    """

    def __class_getitem__(cls, params):
        if cls is Generic:
            return cls
        if not isinstance(params, tuple):
            params = (params,)
        return _GenericAlias(cls, params)


# ── NewType, cast ────────────────────────────────────────────────────────

class NewType:
    """`UserId = NewType("UserId", int)` -- a real identity wrapper.

    CALLING IT ANSWERS THE ARGUMENT UNCHANGED, which is CPython's own
    run-time behaviour: `UserId(5)` is `5`, an `int`, not some wrapped
    value -- the "new type" is a fiction for a type checker only.
    """

    def __init__(self, name, tp):
        self.__name__ = name
        self.__supertype__ = tp

    def __call__(self, x):
        return x


def cast(typ, val):
    """A real no-op: the whole of CPython's own run-time `cast`."""
    return val


def overload(func):
    """Mark a stub as one a real implementation must follow.

    See the module docstring: CPython does no dispatch here either. The
    wrapper exists so that a stub somehow reached directly fails loudly
    instead of returning `None`, which is the one observable behaviour to
    preserve.
    """
    def _overload_dummy(*args, **kwargs):
        raise NotImplementedError(
            "You should not call an overloaded function. "
            "A series of @overload-decorated functions outside a stub "
            "module should always be followed by an implementation that "
            "is not @overload-ed.")
    return _overload_dummy


# ── Protocol ─────────────────────────────────────────────────────────────

#: Names never counted as part of a protocol's structural surface, whether
#: they come from `object`, from `type`'s own machinery, or from the
#: bookkeeping this module and `ABCMeta` add.
_PROTO_SKIP = frozenset((
    "__module__", "__qualname__", "__doc__", "__dict__", "__weakref__",
    "__init__", "__new__", "__class_getitem__", "__subclasshook__",
    "__abstractmethods__", "_is_protocol", "_is_runtime_protocol",
    "__protocol_attrs__", "__parameters__", "__annotations__",
))


def _protocol_attrs(cls):
    """Every method name a `Protocol` (or one it builds on) declares.

    WALKS THE MRO so one protocol extending another still checks the whole
    surface -- but only what a class BODY BINDS, which is `def` and a
    plain assignment; see the module docstring for why a bare-annotation
    attribute member cannot be seen here.
    """
    attrs = set()
    for base in cls.__mro__:
        if base is object or not getattr(base, "_is_protocol", False):
            continue
        for name in base.__dict__:
            if name.startswith("_abc_") or name in _PROTO_SKIP:
                continue
            attrs.add(name)
    return attrs


class _ProtocolMeta(ABCMeta):
    def __new__(mcls, name, bases, ns):
        cls = super().__new__(mcls, name, bases, ns)
        # A DIRECT SUBCLASS OF `Protocol` IS ONE TOO -- checked by NAME
        # rather than by identity, because `Protocol` itself is still being
        # built the one time this runs before that name exists at all.
        cls._is_protocol = any(
            getattr(b, "__name__", None) == "Protocol" for b in bases)
        if cls._is_protocol:
            cls.__protocol_attrs__ = _protocol_attrs(cls)
        return cls

    def __instancecheck__(cls, instance):
        if not getattr(cls, "_is_protocol", False):
            return super().__instancecheck__(instance)
        if not getattr(cls, "_is_runtime_protocol", False):
            raise TypeError(
                "Instance and class checks can only be used with "
                "@runtime_checkable protocols")
        for attr in cls.__protocol_attrs__:
            if not hasattr(instance, attr):
                return False
        return True


class Protocol(metaclass=_ProtocolMeta):
    """A structural interface -- see the module docstring for its scope."""
    _is_runtime_protocol = False


def runtime_checkable(cls):
    """Allow `isinstance`/`issubclass` against a `Protocol` subclass."""
    cls._is_runtime_protocol = True
    return cls


# ── get_type_hints ───────────────────────────────────────────────────────

def _strip_extras(hint):
    """`Annotated[X, ...]` down to `X`, wherever it appears.

    WHAT `include_extras=False` MEANS, and it is the default: CPython hands
    back `int` for `x: Annotated[int, "positive"]` unless the caller asks for
    the metadata, so a program that only wants the type does not have to know
    the annotation carried any.

    THROUGH THE ATTRIBUTES RATHER THAN `get_origin`/`get_args`, which are
    native names this module does not import -- and the two spellings of a
    parameterised type carry the same pair either way. `_name` is what the
    `typing` special forms are interned under, so it is what tells
    `Annotated` from `Literal` without naming either.

    REBUILT BY SUBSCRIPTING THE ORIGIN when an inner argument changed, so a
    nested one goes too: `list[Annotated[int, "m"]]` is `list[int]`. The
    object is handed back UNCHANGED when nothing under it was annotated,
    which keeps identity for the common case.
    """
    origin = getattr(hint, "__origin__", None)
    if origin is None:
        return hint
    args = getattr(hint, "__args__", ())
    if getattr(origin, "_name", None) == "Annotated":
        return _strip_extras(args[0])
    inner = []
    changed = False
    for one in args:
        got = _strip_extras(one)
        if got is not one:
            changed = True
        inner.append(got)
    if not changed:
        return hint
    return origin[tuple(inner)]


def get_type_hints(obj, globalns=None, localns=None, include_extras=False):
    """The resolved annotations of a function or class, as real objects.

    BUILT ON `annotationlib.get_annotations`, which is what this runtime's
    PEP 649 thunks were ever able to give -- see `bundled/annotationlib.py`
    for exactly what that covers and refuses.

    `include_extras=False` STRIPS `Annotated`, which is the only thing the
    flag changes and is the default -- see `_strip_extras`.

    A CLASS MERGES ITS WHOLE `__mro__`, reversed so a subclass's own
    annotation wins over a base's -- CPython does the same. Every class here
    is read the ordinary way, through `getattr`, never through a custom
    metaclass's namespace -- so this does NOT hit the limitation the module
    docstring describes for `NamedTuple`/`TypedDict`/`Protocol`: those need
    the data DURING class construction, and this needs it afterwards, which
    is exactly when it is available.
    """
    if isinstance(obj, type):
        hints = {}
        for base in reversed(obj.__mro__):
            ann = annotationlib.get_annotations(
                base, globals=globalns, locals=localns, eval_str=True)
            hints.update(ann)
    else:
        hints = annotationlib.get_annotations(
            obj, globals=globalns, locals=localns, eval_str=True)
    if include_extras:
        return hints
    out = {}
    for key in hints:
        out[key] = _strip_extras(hints[key])
    return out


# ── NamedTuple, functional form only ─────────────────────────────────────

def NamedTuple(typename, fields):
    """`Point = NamedTuple("Point", [("x", int), ("y", int)])`.

    BUILT ON THE NOW-BUNDLED `collections.namedtuple`, exactly as CPython's
    own `typing.NamedTuple` is -- the type annotations decide the field
    ORDER and become `__annotations__`; nothing here checks that a field's
    value actually matches its declared type, which CPython does not either.

    THE CLASS-BASED FORM IS NOT HERE -- see the module docstring for why.
    """
    names = [pair[0] for pair in fields]
    types = {pair[0]: pair[1] for pair in fields}
    made = namedtuple(typename, names)
    made.__annotations__ = types
    return made


# ── TypedDict, both forms ────────────────────────────────────────────────

#: PEP 655's `Required`/`NotRequired` and PEP 705's `ReadOnly` -- the three
#: forms that wrap a field's type to say something about the KEY rather than
#: about the value. Each stays in the native `_TYPING` table; what is read
#: here is only which one was written.
_QUALIFIERS = ("Required", "NotRequired", "ReadOnly")


def _qualifiers_of(hint):
    """Which of the three wrap `hint`, outermost first.

    THROUGH THE ATTRIBUTES, not `get_origin`: those are native names this
    module does not import, and a parameterised type carries `__origin__`
    and `__args__` either way. `_name` is what a `typing` special form is
    interned under, so it tells `Required` from `Literal` without naming
    either.

    THEY NEST. `Required[ReadOnly[int]]` is legal and means both, so this
    unwraps until it reaches something that is not one of the three.
    """
    out = []
    at = hint
    while True:
        origin = getattr(at, "__origin__", None)
        name = getattr(origin, "_name", None)
        if name not in _QUALIFIERS:
            return out
        out.append(name)
        args = getattr(at, "__args__", ())
        if not args:
            return out
        at = args[0]


def _sorted_into(into, out_of, key):
    """Put `key` in one list of a pair and take it out of the other.

    A SUBCLASS MAY RE-DECLARE A KEY with a different qualifier -- a base's
    `NotRequired[str]` becoming a plain `str` makes it required -- so the
    later declaration has to be able to move it, not merely add it again.
    """
    if key in out_of:
        out_of.remove(key)
    if key not in into:
        into.append(key)


def _typed_dict_keys(cls):
    """`(required, optional, readonly, mutable)` for a TypedDict class.

    COMPUTED WHEN ASKED, and that is what makes the class-based form
    possible here at all. A bare annotation never enters the namespace a
    metaclass `__new__` receives, and this frontend wires the PEP 649
    annotation thunk onto the class object only after the whole `class`
    statement finishes -- so `__new__` cannot see the fields no matter when
    it looks. Reading them from a PROPERTY ON THE METACLASS defers the
    question to the first time a program asks it, which is always after the
    statement completed. The module docstring records the refusal this
    replaced.

    BASE FIRST, so a subclass's own declaration wins -- and the `total` that
    decides a key is the one belonging to the class that DECLARED it, which
    is CPython's rule and the reason this walks the MRO rather than merging
    the annotations first and asking about `cls` once.
    """
    required, optional, readonly, mutable = [], [], [], []
    for owner in reversed(cls.__mro__):
        ann = getattr(owner, "__annotations__", None)
        if not ann:
            continue
        total = getattr(owner, "__total__", True)
        for key in ann:
            marks = _qualifiers_of(ann[key])
            if "Required" in marks:
                wanted = True
            elif "NotRequired" in marks:
                wanted = False
            else:
                wanted = total
            if wanted:
                _sorted_into(required, optional, key)
            else:
                _sorted_into(optional, required, key)
            if "ReadOnly" in marks:
                _sorted_into(readonly, mutable, key)
            else:
                _sorted_into(mutable, readonly, key)
    return (frozenset(required), frozenset(optional),
            frozenset(readonly), frozenset(mutable))


class _TypedDictMeta(type):
    """What makes `TypedDict` both a base class and a factory.

    `total` ARRIVES AS A CLASS KEYWORD -- `class C(TypedDict, total=False)`
    -- which is why `__new__` takes it by name; the functional form puts the
    same value in the namespace instead, and that one is not overwritten.
    """

    def __new__(mcls, name, bases, ns, total=True):
        cls = super().__new__(mcls, name, bases, ns)
        if "__total__" not in ns:
            cls.__total__ = total
        return cls

    def __call__(cls, *args, **kwargs):
        # `TypedDict(...)` ITSELF IS THE FUNCTIONAL FORM and every other
        # class built on it BUILDS A DICT. One `__call__` for both, because
        # `TypedDict` has to be a real class for `class Movie(TypedDict)` to
        # work and a real callable for `TypedDict("Movie", {...})` to.
        if cls is TypedDict:
            return _typed_dict(*args, **kwargs)
        return dict(*args, **kwargs)

    @property
    def __required_keys__(cls):
        return _typed_dict_keys(cls)[0]

    @property
    def __optional_keys__(cls):
        return _typed_dict_keys(cls)[1]

    @property
    def __readonly_keys__(cls):
        return _typed_dict_keys(cls)[2]

    @property
    def __mutable_keys__(cls):
        return _typed_dict_keys(cls)[3]


class TypedDict(metaclass=_TypedDictMeta):
    """A dict whose keys are known -- PEP 589, and PEP 655 and 705 with it.

    BOTH FORMS. `class Movie(TypedDict): title: str` and
    `Movie = TypedDict("Movie", {"title": str})` build the same thing, and
    calling it returns a PLAIN `dict` -- there is no validation at run time,
    exactly as in CPython.

    `__annotations__` IS THE CLASS'S OWN, not the merge of its bases'.
    CPython's `TypedDict` overwrites the attribute with the merged mapping
    during class creation; this frontend answers it from the PEP 649 thunk
    the compiler wires on afterwards, which a metaclass cannot displace. So
    `Sub.__annotations__` has only what `Sub` declared. The four KEY SETS
    below do merge, because those are computed here rather than stored --
    and they are what a program asks about inheritance with.
    """


def _typed_dict(typename, fields, total=True):
    """`Movie = TypedDict("Movie", {"title": str, "year": int})`.

    THE SAME OBJECT the class form builds, made by calling the metaclass
    directly -- the fields go into the namespace as `__annotations__`, which
    is where a class body's would have ended up, so both forms answer the
    four key sets through one implementation.
    """
    body = {"__annotations__": dict(fields), "__total__": total}
    return _TypedDictMeta(typename, (TypedDict,), body)


def dataclass_transform(*, eq_default=True, order_default=False,
                        kw_only_default=False, frozen_default=False,
                        field_specifiers=(), **kwargs):
    """PEP 681's marker -- INERT AT RUN TIME, and that is the whole of it.

    It says to a TYPE CHECKER that the decorated class, function or metaclass
    builds dataclass-like classes. Nothing here reads it, and nothing in
    CPython does either: `dataclasses.dataclass` is not affected by it, and
    `@dataclass_transform()` on a decorator does not make that decorator do
    anything. What it MUST do is return its argument unchanged and leave
    `__dataclass_transform__` on it, because a program can read that back --
    which is exactly what makes "inert" testable.

    NOT IN THE NATIVE `_TYPING` TABLE, unlike `runtime_checkable` and
    `no_type_check`: those are one-argument mark-and-return decorators and
    this takes only keywords and returns a decorator, which is a different
    shape. See `frontends/python/modules.py`, which says so at the table.

    `**kwargs` IS PART OF THE SPECIFIED SIGNATURE. PEP 681 lets a checker
    accept extra keywords it understands, and CPython files whatever it was
    given under `"kwargs"` rather than refusing it.
    """
    def decorator(cls_or_fn):
        cls_or_fn.__dataclass_transform__ = {
            "eq_default": eq_default,
            "order_default": order_default,
            "kw_only_default": kw_only_default,
            "frozen_default": frozen_default,
            "field_specifiers": field_specifiers,
            "kwargs": kwargs,
        }
        return cls_or_fn
    return decorator
