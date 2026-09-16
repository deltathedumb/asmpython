"""The container protocols, as abstract base classes.

COVERAGE: `Hashable`, `Sized`, `Container`, `Iterable`, `Iterator`,
`Reversible`, `Generator`, `Callable`, `Collection`, `Sequence`,
`MutableSequence`, `Set`, `MutableSet`, `Mapping`, `MutableMapping`,
`MappingView`, `KeysView`, `ItemsView`, `ValuesView`, `Awaitable`,
`Coroutine`, `AsyncIterable`, `AsyncIterator`, `AsyncGenerator`, `Buffer`.

THE MIXIN METHODS ARE HERE, and they are what makes inheriting from one of
these worth doing: a class writing `__getitem__` and `__len__` gets `__iter__`,
`__contains__`, `__reversed__`, `index` and `count` from `Sequence`, and the
same arrangement holds for `MutableSequence`, `Set`, `MutableSet`, `Mapping`
and `MutableMapping`. Leaving them abstract would have made `class C(Sequence)`
refuse to instantiate over three names no user of a Sequence ever writes.

NOT COVERED: `MappingView` and its three subclasses are names rather than
working views -- `dict.keys()` already answers a live view of its own, and
nothing here produces one of these.

`ByteString` IS GONE, not missing: it was deprecated in 3.9 and removed in
3.14, which is the version this compiler targets.

TWO MECHANISMS, AND EACH COVERS WHAT THE OTHER CANNOT. A BUILTIN is declared
by REGISTRATION -- `Sequence.register(list)` -- because a builtin type cannot
be inspected here the way CPython inspects it: `getattr(list, "__hash__")`
answers a callable rather than the `None` that marks a list unhashable, so a
structural test would call every builtin hashable. A USER CLASS is decided
STRUCTURALLY, by looking for the methods the protocol names, which is what
makes `isinstance(MyThing(), Iterable)` True for a class that never heard of
this module. `__subclasshook__` answers NotImplemented for anything in
`_BUILTINS` so that the registry decides those, and that hand-off is the whole
design.
"""

from abc import ABCMeta, abstractmethod

#: The types whose protocol membership is DECLARED below rather than
#: inspected. See the module docstring for why inspecting them is not an
#: option here.
_BUILTINS = (list, tuple, str, bytes, dict, set, frozenset,
             int, float, bool)


def _has(C, name):
    """Whether `C` supplies `name` as something callable.

    `None` IS A REFUSAL, not an absence: a class that writes `__hash__ = None`
    is declaring itself unhashable, and CPython's own hooks treat that as a
    negative answer rather than a missing attribute.
    """
    got = getattr(C, name, None)
    return got is not None


def _structural(cls, want, C, names):
    """The shared body of every `__subclasshook__` here.

    `cls is not want` MEANS A SUBCLASS IS ASKING, and a subclass of an ABC
    gets the ordinary rules -- otherwise `class MyList(Sequence)` would answer
    True for anything with `__getitem__`, which is the opposite of what
    inheriting from an ABC is for.
    """
    if cls is not want:
        return NotImplemented
    for one in _BUILTINS:
        if C is one:
            return NotImplemented
    for name in names:
        if not _has(C, name):
            return NotImplemented
    return True


class Hashable(metaclass=ABCMeta):
    @abstractmethod
    def __hash__(self):
        return 0

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Hashable, C, ("__hash__",))


class Sized(metaclass=ABCMeta):
    @abstractmethod
    def __len__(self):
        return 0

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Sized, C, ("__len__",))


class Container(metaclass=ABCMeta):
    @abstractmethod
    def __contains__(self, x):
        return False

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Container, C, ("__contains__",))


class Iterable(metaclass=ABCMeta):
    @abstractmethod
    def __iter__(self):
        return iter(())

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Iterable, C, ("__iter__",))


class Iterator(Iterable):
    @abstractmethod
    def __next__(self):
        raise StopIteration

    def __iter__(self):
        return self

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Iterator, C, ("__iter__", "__next__"))


class Reversible(Iterable):
    @abstractmethod
    def __reversed__(self):
        return iter(())

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Reversible, C, ("__reversed__", "__iter__"))


class Generator(Iterator):
    @abstractmethod
    def send(self, value):
        raise StopIteration

    @abstractmethod
    def throw(self, typ, val=None, tb=None):
        raise StopIteration

    def close(self):
        return None

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Generator, C,
                           ("__iter__", "__next__", "send", "throw", "close"))


class Callable(metaclass=ABCMeta):
    @abstractmethod
    def __call__(self, *args, **kwargs):
        return None

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Callable, C, ("__call__",))


class Collection(Sized, Iterable, Container):
    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Collection, C,
                           ("__len__", "__iter__", "__contains__"))


class Sequence(Reversible, Collection):
    """An ordered, indexable, finite collection.

    NO `__subclasshook__`, DELIBERATELY, and CPython has none either: a class
    with `__getitem__` and `__len__` is not automatically a Sequence, because
    a Mapping has both and means something entirely different by them. This
    one is claimed by INHERITING or by `register`, and nothing else.

    THE MIXINS ARE THE POINT OF INHERITING. A subclass writes `__getitem__`
    and `__len__`; the four below come for free and are what make it usable.
    Leaving them abstract -- which is what "protocols and nothing more" would
    mean -- made `class C(Sequence)` refuse to instantiate over `__iter__`,
    `__contains__` and `__reversed__` that no user of a Sequence ever writes.
    """

    @abstractmethod
    def __getitem__(self, index):
        raise IndexError

    def __iter__(self):
        """WALKED BY INDEX until it runs out, which is the older iteration
        protocol and exactly what `__getitem__` plus `__len__` describe."""
        i = 0
        n = len(self)
        while i < n:
            yield self[i]
            i = i + 1

    def __contains__(self, value):
        for one in self:
            if one is value or one == value:
                return True
        return False

    def __reversed__(self):
        i = len(self) - 1
        while i >= 0:
            yield self[i]
            i = i - 1

    def index(self, value, start=0, stop=None):
        end = len(self) if stop is None else stop
        i = start
        while i < end:
            got = self[i]
            if got is value or got == value:
                return i
            i = i + 1
        raise ValueError(repr(value) + " is not in sequence")

    def count(self, value):
        found = 0
        for one in self:
            if one is value or one == value:
                found = found + 1
        return found


class MutableSequence(Sequence):
    @abstractmethod
    def __setitem__(self, index, value):
        raise IndexError

    @abstractmethod
    def __delitem__(self, index):
        raise IndexError

    @abstractmethod
    def insert(self, index, value):
        return None

    def append(self, value):
        self.insert(len(self), value)
        return None

    def extend(self, values):
        for one in values:
            self.append(one)
        return None

    def pop(self, index=-1):
        got = self[index]
        del self[index]
        return got

    def remove(self, value):
        del self[self.index(value)]
        return None

    def clear(self):
        while len(self) > 0:
            self.pop()
        return None

    def reverse(self):
        n = len(self)
        i = 0
        while i < n // 2:
            self[i], self[n - 1 - i] = self[n - 1 - i], self[i]
            i = i + 1
        return None

    def __iadd__(self, values):
        self.extend(values)
        return self


class Set(Collection):
    """A finite set. Claimed by inheriting or by `register`; see `Sequence`
    for why there is no structural hook.

    THE OPERATORS ARE MIXINS and answer a set of the SAME class where they
    can: `_from_iterable` is the hook CPython gives a subclass whose
    constructor does not take one iterable, and it is what keeps `a & b` from
    silently becoming a plain set.
    """

    @classmethod
    def _from_iterable(cls, it):
        return cls(it)

    def __le__(self, other):
        if len(self) > len(other):
            return False
        for one in self:
            if one not in other:
                return False
        return True

    def __lt__(self, other):
        return len(self) < len(other) and self.__le__(other)

    def __gt__(self, other):
        return len(self) > len(other) and self.__ge__(other)

    def __ge__(self, other):
        for one in other:
            if one not in self:
                return False
        return True

    def __eq__(self, other):
        return len(self) == len(other) and self.__le__(other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __and__(self, other):
        out = []
        for one in self:
            if one in other:
                out.append(one)
        return self._from_iterable(out)

    def __or__(self, other):
        out = list(self)
        for one in other:
            if one not in out:
                out.append(one)
        return self._from_iterable(out)

    def __sub__(self, other):
        out = []
        for one in self:
            if one not in other:
                out.append(one)
        return self._from_iterable(out)

    def __xor__(self, other):
        out = []
        for one in self:
            if one not in other:
                out.append(one)
        for one in other:
            if one not in self:
                out.append(one)
        return self._from_iterable(out)

    def isdisjoint(self, other):
        for one in other:
            if one in self:
                return False
        return True


class MutableSet(Set):
    @abstractmethod
    def add(self, value):
        return None

    @abstractmethod
    def discard(self, value):
        return None

    def remove(self, value):
        """UNLIKE `discard`, an absent element is a KeyError. That is the only
        difference between the two, and it is why `remove` cannot just call
        `discard`."""
        if value not in self:
            raise KeyError(value)
        self.discard(value)
        return None

    def pop(self):
        for one in self:
            self.discard(one)
            return one
        raise KeyError("pop from an empty set")

    def clear(self):
        while len(self) > 0:
            self.pop()
        return None

    def __ior__(self, other):
        for one in other:
            self.add(one)
        return self

    def __iand__(self, other):
        for one in list(self):
            if one not in other:
                self.discard(one)
        return self

    def __isub__(self, other):
        for one in other:
            self.discard(one)
        return self

    def __ixor__(self, other):
        for one in list(other):
            if one in self:
                self.discard(one)
            else:
                self.add(one)
        return self


class Mapping(Collection):
    """A read-only mapping. Claimed by inheriting or by `register`; see
    `Sequence` for why there is no structural hook -- `__getitem__` plus
    `__len__` describes a sequence just as well."""

    @abstractmethod
    def __getitem__(self, key):
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        try:
            self[key]
        except KeyError:
            return False
        return True

    def keys(self):
        return list(self)

    def items(self):
        out = []
        for key in self:
            out.append((key, self[key]))
        return out

    def values(self):
        out = []
        for key in self:
            out.append(self[key])
        return out

    def __eq__(self, other):
        if not isinstance(other, Mapping) and not isinstance(other, dict):
            return NotImplemented
        return dict(self.items()) == dict(other.items()
                                          if isinstance(other, Mapping)
                                          else other)

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got


class MutableMapping(Mapping):
    @abstractmethod
    def __setitem__(self, key, value):
        return None

    @abstractmethod
    def __delitem__(self, key):
        return None

    def pop(self, key, *rest):
        try:
            got = self[key]
        except KeyError:
            if rest:
                return rest[0]
            raise
        del self[key]
        return got

    def popitem(self):
        for key in self:
            value = self[key]
            del self[key]
            return (key, value)
        raise KeyError("dictionary is empty")

    def clear(self):
        for key in list(self):
            del self[key]
        return None

    def setdefault(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            self[key] = default
        return default

    def update(self, other=None, **kwargs):
        if other is not None:
            if hasattr(other, "keys"):
                for key in other.keys():
                    self[key] = other[key]
            else:
                for pair in other:
                    self[pair[0]] = pair[1]
        for key in kwargs:
            self[key] = kwargs[key]
        return None


class MappingView(Sized):
    @classmethod
    def __subclasshook__(cls, C):
        return NotImplemented


class KeysView(MappingView, Set):
    @classmethod
    def __subclasshook__(cls, C):
        return NotImplemented


class ItemsView(MappingView, Set):
    @classmethod
    def __subclasshook__(cls, C):
        return NotImplemented


class ValuesView(MappingView, Collection):
    @classmethod
    def __subclasshook__(cls, C):
        return NotImplemented


class Awaitable(metaclass=ABCMeta):
    @abstractmethod
    def __await__(self):
        return iter(())

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Awaitable, C, ("__await__",))


class Coroutine(Awaitable):
    @abstractmethod
    def send(self, value):
        raise StopIteration

    @abstractmethod
    def throw(self, typ, val=None, tb=None):
        raise StopIteration

    def close(self):
        return None

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Coroutine, C,
                           ("__await__", "send", "throw", "close"))


class AsyncIterable(metaclass=ABCMeta):
    @abstractmethod
    def __aiter__(self):
        return self

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, AsyncIterable, C, ("__aiter__",))


class AsyncIterator(AsyncIterable):
    @abstractmethod
    def __anext__(self):
        raise StopAsyncIteration

    def __aiter__(self):
        return self

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, AsyncIterator, C, ("__anext__", "__aiter__"))


class AsyncGenerator(AsyncIterator):
    @abstractmethod
    def asend(self, value):
        raise StopAsyncIteration

    @abstractmethod
    def athrow(self, typ, val=None, tb=None):
        raise StopAsyncIteration

    def aclose(self):
        return None

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, AsyncGenerator, C,
                           ("__aiter__", "__anext__", "asend", "athrow",
                            "aclose"))


class Buffer(metaclass=ABCMeta):
    """PEP 688, added in 3.12. Nothing here implements the buffer protocol,
    so the registry below is empty -- the class exists so that a program that
    NAMES it gets a class rather than an ImportError."""

    @abstractmethod
    def __buffer__(self, flags):
        return None

    @classmethod
    def __subclasshook__(cls, C):
        return _structural(cls, Buffer, C, ("__buffer__",))


# ── what the builtins are ───────────────────────────────────────────────────
#
# DECLARED, ONE LINE PER CLAIM, because the alternative here is a structural
# test that answers wrongly -- see the module docstring. These are the
# registrations CPython's own `collections.abc` makes at import, restricted to
# the types this frontend can name as VALUES.
#
# TEN TYPES, NOT FOURTEEN. `bytearray`, `range`, `memoryview`, `complex` and
# `NoneType` are builtins that cannot travel as values here (`E0056`), so
# nothing can be registered for them and `isinstance(range(3), Iterable)`
# answers False where CPython says True. That is the one divergence in this
# module and it is a frontend limit rather than a choice -- when those become
# values, the fix is four more lines here.

for _one in (list, tuple, str, bytes, dict, set, frozenset):
    Sized.register(_one)
    Container.register(_one)
    Iterable.register(_one)
    Collection.register(_one)

# `bytes` IS HASHABLE AND `bytearray` IS NOT -- the pair most likely to be got
# wrong by pattern, since the two share a layout in this runtime and differ in
# exactly the way that matters here. Only `bytes` can be named, which happens
# to make the omission harmless.
for _one in (str, bytes, bool, int, float, tuple, frozenset):
    Hashable.register(_one)

for _one in (list, tuple, str, bytes):
    Sequence.register(_one)
    Reversible.register(_one)

MutableSequence.register(list)

Set.register(frozenset)
Set.register(set)
MutableSet.register(set)

Mapping.register(dict)
MutableMapping.register(dict)
Reversible.register(dict)
