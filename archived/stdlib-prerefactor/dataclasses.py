"""Classes whose boilerplate is generated from their annotations.

`@dataclass` reads the class's `__annotations__` and writes `__init__`,
`__repr__` and `__eq__` from them. Here it does not write source -- there is
no `exec` -- it installs closures that walk the field list, which is the same
behaviour reached a different way.

THE `default_factory` RULE IS THE POINT OF `field()`: a mutable default
written as `tags: list = []` would be ONE list shared by every instance, so
dataclasses refuse it and take a callable instead, called once per instance.
"""


class _Missing:
    def __repr__(self):
        return "MISSING"


MISSING = _Missing()


class Field:
    def __init__(self, default=MISSING, default_factory=MISSING, init=True,
                 repr=True, compare=True, kw_only=False):
        self.name = None
        self.type = None
        self.default = default
        self.default_factory = default_factory
        self.init = init
        self.repr = repr
        self.compare = compare
        self.kw_only = kw_only

    def __repr__(self):
        return "Field(name=" + repr(self.name) + ")"


def field(default=MISSING, default_factory=MISSING, init=True, repr=True,
          compare=True, kw_only=False):
    if default is not MISSING and default_factory is not MISSING:
        raise ValueError("cannot specify both default and default_factory")
    return Field(default, default_factory, init, repr, compare, kw_only)


def dataclass(cls=None, init=True, repr=True, eq=True, order=False,
              frozen=False):
    """Turn a class of annotations into one with the methods written out."""
    def build(target):
        return _build(target, init, repr, eq, order, frozen)
    if cls is None:
        return build
    return build(cls)


def _build(cls, want_init, want_repr, want_eq, want_order, frozen):
    hints = getattr(cls, "__annotations__", {})
    fields = []
    for name in hints:
        spec = getattr(cls, name, MISSING)
        if not isinstance(spec, Field):
            spec = Field(default=spec)
        spec.name = name
        spec.type = hints[name]
        fields.append(spec)
        # THE CLASS ATTRIBUTE GOES when the field has no plain default: a
        # `Field` object left there would be what an unset attribute reads as.
        if spec.default is MISSING:
            try:
                delattr(cls, name)
            except AttributeError:
                pass
        else:
            setattr(cls, name, spec.default)
    cls.__dataclass_fields__ = fields

    if want_init:
        def __init__(self, *args, **kwargs):
            i = 0
            for spec in fields:
                if not spec.init:
                    if spec.default_factory is not MISSING:
                        setattr(self, spec.name, spec.default_factory())
                    continue
                if i < len(args):
                    value = args[i]
                    i = i + 1
                elif spec.name in kwargs:
                    value = kwargs[spec.name]
                elif spec.default_factory is not MISSING:
                    # ONE CALL PER INSTANCE, which is the whole reason the
                    # factory is a callable rather than a value.
                    value = spec.default_factory()
                elif spec.default is not MISSING:
                    value = spec.default
                else:
                    raise TypeError(cls.__name__ + ".__init__() missing "
                                    "required argument: " + repr(spec.name))
                setattr(self, spec.name, value)
        cls.__init__ = __init__

    if want_repr:
        def __repr__(self):
            parts = []
            for spec in fields:
                if spec.repr:
                    parts.append(spec.name + "="
                                 + repr(getattr(self, spec.name)))
            return type(self).__name__ + "(" + ", ".join(parts) + ")"
        cls.__repr__ = __repr__

    if want_eq:
        def __eq__(self, other):
            if type(other) is not type(self):
                return NotImplemented
            return _key(self, fields) == _key(other, fields)

        def __ne__(self, other):
            got = __eq__(self, other)
            return got if got is NotImplemented else not got
        cls.__eq__ = __eq__
        cls.__ne__ = __ne__

    if want_order:
        cls.__lt__ = lambda self, other: _key(self, fields) < _key(other,
                                                                   fields)
        cls.__le__ = lambda self, other: _key(self, fields) <= _key(other,
                                                                    fields)
        cls.__gt__ = lambda self, other: _key(self, fields) > _key(other,
                                                                   fields)
        cls.__ge__ = lambda self, other: _key(self, fields) >= _key(other,
                                                                    fields)
    return cls


def _key(obj, fields):
    return tuple([getattr(obj, spec.name) for spec in fields if spec.compare])


def fields(obj):
    target = obj if isinstance(obj, type) else type(obj)
    return tuple(getattr(target, "__dataclass_fields__", ()))


def is_dataclass(obj):
    target = obj if isinstance(obj, type) else type(obj)
    return hasattr(target, "__dataclass_fields__")


def asdict(obj):
    out = {}
    for spec in fields(obj):
        out[spec.name] = getattr(obj, spec.name)
    return out


def astuple(obj):
    return tuple([getattr(obj, spec.name) for spec in fields(obj)])


def replace(obj, **changes):
    values = {}
    for spec in fields(obj):
        values[spec.name] = changes[spec.name] if spec.name in changes \
            else getattr(obj, spec.name)
    return type(obj)(**values)
