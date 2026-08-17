"""The parts of `typing` that are OBJECTS rather than special forms.

`Optional`, `Union`, `Literal`, `Final` and the rest of the special forms are
already runtime values -- the compiler builds them, and `int | None` prints as
CPython prints it. What was missing is the half of `typing` that is ordinary
Python: classes a program inherits from and functions it calls.

This module is spliced IN PART. A name it does not define keeps its import and
reaches the builtin `typing` table as before, so the two halves coexist rather
than one replacing the other.

WHAT `typing` IS AT RUN TIME: almost nothing. `Final` does not freeze,
`override` does not check, and a `Protocol` that is not `@runtime_checkable`
refuses `isinstance` rather than answering it. Every one of those is CPython's
behaviour and not a shortcut -- the checking lives in a type checker, and the
runtime's job is to carry the annotation and stay out of the way.
"""


class TypeVar:
    """A named stand-in for a type. At run time it is its name and nothing."""

    def __init__(self, name, *constraints, bound=None, covariant=False,
                 contravariant=False, default=None):
        self.__name__ = name
        self.__constraints__ = constraints
        self.__bound__ = bound
        self.__covariant__ = covariant
        self.__contravariant__ = contravariant
        # PEP 696: a type parameter may carry a DEFAULT, which is inert here
        # exactly as the bound is -- both are read back and never enforced.
        self.__default__ = default

    def has_default(self):
        return self.__default__ is not None

    def __repr__(self):
        return "~" + self.__name__

    def __or__(self, other):
        return _Union(self, other)

    def __ror__(self, other):
        return _Union(other, self)


class ParamSpec:
    """PEP 612: a stand-in for a whole PARAMETER LIST rather than one type."""

    def __init__(self, name, bound=None, covariant=False,
                 contravariant=False, default=None):
        self.__name__ = name
        self.__bound__ = bound
        self.__default__ = default

    @property
    def args(self):
        return ParamSpecArgs(self)

    @property
    def kwargs(self):
        return ParamSpecKwargs(self)

    def has_default(self):
        return self.__default__ is not None

    def __repr__(self):
        return "~" + self.__name__


class ParamSpecArgs:
    def __init__(self, origin):
        self.__origin__ = origin

    def __repr__(self):
        return self.__origin__.__name__ + ".args"


class ParamSpecKwargs:
    def __init__(self, origin):
        self.__origin__ = origin

    def __repr__(self):
        return self.__origin__.__name__ + ".kwargs"


class TypeVarTuple:
    """PEP 646: a stand-in for an arbitrary NUMBER of types."""

    def __init__(self, name, default=None):
        self.__name__ = name
        self.__default__ = default

    def has_default(self):
        return self.__default__ is not None

    def __repr__(self):
        return "*" + self.__name__


class _Union:
    """`A | B` between things the runtime's own union does not cover."""

    def __init__(self, *args):
        self.__args__ = args

    def __repr__(self):
        return " | ".join([_name_of(a) for a in self.__args__])


def _name_of(value):
    if hasattr(value, "__name__"):
        return value.__name__
    return repr(value)


class _GenericAlias:
    """`Box[int]` -- what subscripting a generic class answers.

    Keeps the ORIGIN and the ARGUMENTS, which is exactly what `get_origin` and
    `get_args` are asked for, and prints the way CPython prints one.
    """

    def __init__(self, origin, args):
        self.__origin__ = origin
        self.__args__ = args if isinstance(args, tuple) else (args,)

    def __call__(self, *a, **kw):
        # `Box[int](1)` BUILDS A BOX. The parameterisation is annotation-only,
        # so calling the alias is calling the class it came from.
        return self.__origin__(*a, **kw)

    def __repr__(self):
        inner = ", ".join([_name_of(a) for a in self.__args__])
        return _name_of(self.__origin__) + "[" + inner + "]"


class _GenericMeta(type):
    def __getitem__(cls, item):
        return _GenericAlias(cls, item)


class Generic(metaclass=_GenericMeta):
    """A base that makes a class SUBSCRIPTABLE and does nothing else."""


class _ProtocolMeta(type):
    def __getitem__(cls, item):
        return _GenericAlias(cls, item)

    def __instancecheck__(cls, instance):
        # A PROTOCOL REFUSES `isinstance` UNLESS IT OPTED IN. Structural
        # checking at run time is a cost and a surprise, so PEP 544 made it
        # explicit; answering anyway would accept programs CPython rejects.
        if not getattr(cls, "_is_runtime_protocol", False):
            raise TypeError("Instance and class checks can only be used with "
                            "@runtime_checkable protocols")
        for name in cls._protocol_members_:
            if not hasattr(instance, name):
                return False
        return True

    def __subclasscheck__(cls, subclass):
        if not getattr(cls, "_is_runtime_protocol", False):
            raise TypeError("Instance and class checks can only be used with "
                            "@runtime_checkable protocols")
        for name in cls._protocol_members_:
            if not hasattr(subclass, name):
                return False
        return True


class Protocol(metaclass=_ProtocolMeta):
    """A class describing what an object CAN DO rather than what it is."""

    _is_runtime_protocol = False
    _protocol_members_ = ()


def runtime_checkable(cls):
    """Let a protocol be used with `isinstance`.

    The members are collected HERE rather than in the metaclass because that
    is the moment the body is complete and the decision to check has been
    made; a protocol nobody decorates never pays for the walk.
    """
    members = []
    for name in dir(cls):
        if name.startswith("_"):
            continue
        members.append(name)
    cls._protocol_members_ = tuple(members)
    cls._is_runtime_protocol = True
    return cls


class _TypedDictMeta(type):
    """The metaclass a TypedDict's key sets are read off.

    The four sets are PROPERTIES and not attributes set when the class is
    built, because the annotations do not exist yet at that moment: PEP 649
    makes them lazy, and the thunk that builds them is installed after the
    class object is. Computing them on the first read is what makes them see
    anything at all.
    """

    def _keys(cls, want):
        required, optional, readonly, mutable = [], [], [], []
        total = getattr(cls, "__total__", True)
        hints = getattr(cls, "__annotations__", {})
        for key in hints:
            wrapper = _form_of(hints[key])
            if wrapper == "Required":
                required.append(key)
            elif wrapper == "NotRequired":
                optional.append(key)
            elif total:
                required.append(key)
            else:
                optional.append(key)
            # READONLY IS ORTHOGONAL to required: a key may be both, and a
            # plain one is mutable.
            if wrapper == "ReadOnly":
                readonly.append(key)
            else:
                mutable.append(key)
        if want == "required":
            return frozenset(required)
        if want == "optional":
            return frozenset(optional)
        if want == "readonly":
            return frozenset(readonly)
        return frozenset(mutable)

    @property
    def __required_keys__(cls):
        return cls._keys("required")

    @property
    def __optional_keys__(cls):
        return cls._keys("optional")

    @property
    def __readonly_keys__(cls):
        return cls._keys("readonly")

    @property
    def __mutable_keys__(cls):
        return cls._keys("mutable")

    def __call__(cls, *args, **kwargs):
        # A TypedDict CONSTRUCTOR BUILDS A PLAIN DICT. `type(m).__name__` is
        # `dict` -- there is no TypedDict instance, and that is the point of
        # the form: it annotates a dict without changing what one is.
        made = {}
        if args:
            made.update(args[0])
        made.update(kwargs)
        return made


class TypedDict(metaclass=_TypedDictMeta):
    """A dict whose KEYS are known and annotated.

    Subclassing this records the annotations and totality on the class; it
    creates no new kind of object.
    """

    __total__ = True


def _form_of(annotation):
    """Which `typing` wrapper an annotation was written with, or "".

    Read off the ORIGIN's repr, because a special form prints as
    `typing.Name` and that name is the only thing distinguishing one form
    from another at run time.
    """
    origin = get_origin(annotation)
    if origin is None:
        return ""
    shown = repr(origin)
    return shown[7:] if shown.startswith("typing.") else ""


def get_origin(tp):
    """What `X[...]` was subscripted FROM, or None if it was not."""
    if isinstance(tp, _GenericAlias):
        return tp.__origin__
    if hasattr(tp, "__origin__"):
        return tp.__origin__
    return None


def get_args(tp):
    """The arguments a parameterised type carries, as a tuple."""
    if isinstance(tp, _GenericAlias):
        return tp.__args__
    if hasattr(tp, "__args__"):
        return tuple(tp.__args__)
    return ()


def get_type_hints(obj, globalns=None, localns=None, include_extras=False):
    """The annotations of a function or class, RESOLVED.

    PEP 649 already builds them lazily and hands back objects rather than
    strings, so there is nothing left here to evaluate -- which is why this is
    a read of `__annotations__` and not an `eval` loop.

    `include_extras` DECIDES WHETHER `Annotated` SURVIVES. Off by default,
    because the metadata is for whoever put it there and a caller asking for
    types wants the type: `Annotated[int, "positive"]` answers `int`.
    """
    out = {}
    hints = getattr(obj, "__annotations__", {})
    for key in hints:
        value = hints[key]
        if not include_extras:
            value = _strip_extras(value)
        out[key] = value
    return out


def _strip_extras(tp):
    """`Annotated[X, ...]` becomes `X`; anything else is itself."""
    origin = get_origin(tp)
    if origin is not None and repr(origin) == "typing.Annotated":
        args = get_args(tp)
        return _strip_extras(args[0]) if args else tp
    return tp


def overload(fn):
    return fn


def no_type_check(fn):
    return fn


def cast(typ, val):
    """A promise to the type checker. At run time it is the value, unchanged."""
    return val


def assert_type(val, typ):
    return val


def assert_never(value):
    raise AssertionError("Expected code to be unreachable, but got: "
                         + repr(value))


def reveal_type(value):
    return value


def dataclass_transform(eq_default=True, order_default=False,
                        kw_only_default=False, frozen_default=False,
                        field_specifiers=(), **kwargs):
    """PEP 681: a MARKER a type checker reads. Inert at run time.

    THE DEFAULTS ARE PART OF THE MARKER, which is why they are written out:
    a program reads `__dataclass_transform__["eq_default"]` back and expects
    True whether or not the decorator was given one.
    """
    held = {
        "eq_default": eq_default,
        "order_default": order_default,
        "kw_only_default": kw_only_default,
        "frozen_default": frozen_default,
        "field_specifiers": field_specifiers,
        "kwargs": kwargs,
    }

    def keep(cls_or_fn):
        cls_or_fn.__dataclass_transform__ = held
        return cls_or_fn
    return keep


def override(fn):
    """PEP 698: says a method replaces a base's. Checked by a type checker."""
    fn.__override__ = True
    return fn


def final(obj):
    """PEP 591: says nothing may subclass or override this. Inert at run time."""
    obj.__final__ = True
    return obj


class NamedTuple:
    """The class form of `collections.namedtuple`.

    Inheriting from it is how a program writes a named tuple with annotations,
    and what it gets is the same object `namedtuple` builds.
    """
