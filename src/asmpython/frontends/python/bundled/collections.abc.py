"""Abstract base classes for containers.

These are the ONLY classes in the standard library whose whole purpose is to
answer `isinstance` -- nothing inherits from them, and a list is a `Sequence`
because of what it can do rather than because of where it came from. So each
one here is a class whose metaclass answers the question structurally, by
asking whether the object has the methods the protocol names.

That is what CPython does too, through `__subclasshook__`. The difference is
that CPython can also consult a registry of builtin types; here the structural
test is the whole answer, which is why `Hashable` asks whether hashing the
object actually works rather than whether its type is on a list.
"""


class _ProtocolMeta(type):
    """A metaclass whose classes answer `isinstance` by CAPABILITY.

    The names to look for live on the class as `_methods_`, so every protocol
    below is one line plus that tuple.
    """

    def __instancecheck__(cls, instance):
        return cls.__subclasshook__(instance)

    def __subclasscheck__(cls, subclass):
        # A CLASS, not an instance. `issubclass(dict, Mapping)` asks whether
        # every instance of `dict` would qualify, which for a structural test
        # means asking the methods of the class itself.
        return _has_all(subclass, cls._methods_)

    def __subclasshook__(cls, instance):
        return _has_all(instance, cls._methods_)


def _has_all(obj, names):
    for name in names:
        if not hasattr(obj, name):
            return False
        # SET TO None MEANS WITHDRAWN. `[].__hash__` is None rather than
        # missing -- that is how a mutable container says it cannot be hashed
        # -- so presence alone would call every list hashable.
        if getattr(obj, name) is None:
            return False
    return True


class Hashable(metaclass=_ProtocolMeta):
    _methods_ = ("__hash__",)


class Iterable(metaclass=_ProtocolMeta):
    _methods_ = ("__iter__",)


class Iterator(Iterable):
    _methods_ = ("__iter__", "__next__")


class Sized(metaclass=_ProtocolMeta):
    _methods_ = ("__len__",)


class Container(metaclass=_ProtocolMeta):
    _methods_ = ("__contains__",)


class Collection(metaclass=_ProtocolMeta):
    _methods_ = ("__len__", "__iter__", "__contains__")


class Sequence(metaclass=_ProtocolMeta):
    _methods_ = ("__getitem__", "__len__", "__iter__", "index", "count")


class MutableSequence(Sequence):
    _methods_ = ("__getitem__", "__len__", "__setitem__", "append", "insert")


class Mapping(metaclass=_ProtocolMeta):
    _methods_ = ("__getitem__", "__len__", "__iter__", "keys", "items",
                 "values")


class MutableMapping(Mapping):
    _methods_ = ("__getitem__", "__setitem__", "__len__", "keys", "items")


class Set(metaclass=_ProtocolMeta):
    _methods_ = ("__contains__", "__len__", "__iter__", "isdisjoint")


class MutableSet(Set):
    _methods_ = ("__contains__", "__len__", "__iter__", "add", "discard")


class Callable(metaclass=_ProtocolMeta):
    _methods_ = ("__call__",)


class Generator(metaclass=_ProtocolMeta):
    _methods_ = ("send", "throw", "close", "__iter__", "__next__")
