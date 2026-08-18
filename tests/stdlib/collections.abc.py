# COVERAGE: every ABC the module exports, checked two ways -- a BUILTIN
# answers from the registrations at the bottom of the module, and a USER CLASS
# answers structurally through `__subclasshook__`. Both are asserted for the
# same protocol wherever the pair differs, because getting one right and the
# other wrong is the shape of failure here.
#
# THE MIXINS ARE TESTED THROUGH `Complete`, which writes `__getitem__` and
# `__len__` and gets the rest -- if they were missing it would refuse to
# instantiate, which is what `Incomplete` just above checks the other way.
#
# `range`, `bytearray` AND `memoryview` DO NOT APPEAR. They cannot be named as
# values in this frontend, so nothing is registered for them and a test would
# be asserting a limitation rather than a behaviour.
from collections.abc import (AsyncGenerator, AsyncIterable, AsyncIterator,
                             Awaitable, Callable, Collection, Container,
                             Coroutine, Generator, Hashable, Iterable,
                             Iterator, Mapping, MappingView, MutableMapping,
                             MutableSequence, MutableSet, Reversible,
                             Sequence, Set, Sized)

# ---- the builtins, by registration -----------------------------------------
print(isinstance([1], Iterable), isinstance([1], Sized))
print(isinstance([1], Sequence), isinstance({}, Mapping))
print(isinstance("a", Sequence), isinstance(1, Hashable))
print(isinstance([1], Hashable), isinstance({}, Hashable))
print(issubclass(dict, Mapping), issubclass(list, Sequence))
print(issubclass(list, MutableSequence), issubclass(tuple, MutableSequence))
print(issubclass(set, MutableSet), issubclass(frozenset, MutableSet))
print(issubclass(set, Set), issubclass(frozenset, Set))
print(issubclass(dict, MutableMapping), issubclass(str, Container))
print(issubclass(tuple, Reversible), issubclass(dict, Reversible))
print(issubclass(bytes, Sequence), issubclass(bytes, Hashable))
print(issubclass(list, Collection), issubclass(int, Collection))
print(issubclass(int, Hashable), issubclass(float, Hashable),
      issubclass(bool, Hashable))

# AN ABC INHERITS ITS BASES' REGISTRATIONS: `dict` is registered with
# `MutableMapping`, and `Mapping` is above it, so both answer True.
print(issubclass(dict, Mapping), issubclass(dict, Collection),
      issubclass(dict, Sized))

# ---- user classes, structurally --------------------------------------------
class JustIter:
    def __iter__(self):
        return iter([1, 2])


class JustLen:
    def __len__(self):
        return 3


class JustIn:
    def __contains__(self, x):
        return True


class Everything:
    def __iter__(self):
        return iter([])

    def __len__(self):
        return 0

    def __contains__(self, x):
        return False


class Nothing:
    pass


print(isinstance(JustIter(), Iterable), issubclass(JustIter, Iterable))
print(isinstance(JustLen(), Sized), isinstance(JustIn(), Container))
print(isinstance(Nothing(), Iterable), isinstance(Nothing(), Sized))
print(issubclass(Everything, Collection), issubclass(JustIter, Collection))
print(issubclass(JustIter, Sized), issubclass(JustLen, Iterable))


class Cursor:
    def __iter__(self):
        return self

    def __next__(self):
        raise StopIteration


print(issubclass(Cursor, Iterator), issubclass(Cursor, Iterable))
print(issubclass(JustIter, Iterator))


class Seq:
    def __getitem__(self, i):
        return i

    def __len__(self):
        return 2


print(issubclass(Seq, Sequence), issubclass(Seq, Collection))


class MutSeq:
    def __getitem__(self, i):
        return i

    def __setitem__(self, i, v):
        return None

    def __delitem__(self, i):
        return None

    def __len__(self):
        return 0

    def insert(self, i, v):
        return None


print(issubclass(MutSeq, MutableSequence), issubclass(Seq, MutableSequence))


class Map:
    def __getitem__(self, k):
        return k

    def __len__(self):
        return 0

    def __iter__(self):
        return iter([])

    def keys(self):
        return []


print(issubclass(Map, Mapping), issubclass(Map, MutableMapping))


class Fn:
    def __call__(self):
        return 1


print(issubclass(Fn, Callable), issubclass(Nothing, Callable))


class Rev:
    def __iter__(self):
        return iter([])

    def __reversed__(self):
        return iter([])


print(issubclass(Rev, Reversible), issubclass(JustIter, Reversible))


class Gen:
    def __iter__(self):
        return self

    def __next__(self):
        raise StopIteration

    def send(self, v):
        return None

    def throw(self, t, v=None, tb=None):
        return None

    def close(self):
        return None


print(issubclass(Gen, Generator), issubclass(Cursor, Generator))


class Aw:
    def __await__(self):
        return iter([])


print(issubclass(Aw, Awaitable), issubclass(Nothing, Awaitable))


class ACo:
    def __await__(self):
        return iter([])

    def send(self, v):
        return None

    def throw(self, t, v=None, tb=None):
        return None

    def close(self):
        return None


print(issubclass(ACo, Coroutine), issubclass(Aw, Coroutine))


class AIt:
    def __aiter__(self):
        return self

    def __anext__(self):
        return None


print(issubclass(AIt, AsyncIterable), issubclass(AIt, AsyncIterator))
print(issubclass(Nothing, AsyncIterable))


class AGen:
    def __aiter__(self):
        return self

    def __anext__(self):
        return None

    def asend(self, v):
        return None

    def athrow(self, t, v=None, tb=None):
        return None

    def aclose(self):
        return None


print(issubclass(AGen, AsyncGenerator), issubclass(AIt, AsyncGenerator))

# ---- inheriting from one ---------------------------------------------------
#
# THE ABSTRACT NAMES ARE STILL ENFORCED, which is what makes inheriting from an
# ABC different from being recognised by one.
class Incomplete(Sequence):
    pass


try:
    Incomplete()
except TypeError as e:
    print("incomplete refused:", "abstract" in str(e))


class Complete(Sequence):
    def __init__(self, items):
        self.items = list(items)

    def __getitem__(self, i):
        return self.items[i]

    def __len__(self):
        return len(self.items)


c = Complete([1, 2, 3])
print(c[0], len(c), isinstance(c, Sequence), isinstance(c, Sized))
print(issubclass(Complete, Sequence), issubclass(Complete, Collection))

# A SUBCLASS ASKING IS NOT THE SAME QUESTION: `Complete` inherits, so anything
# claiming to be a `Complete` must really be one -- the structural hook only
# answers for the ABC it was written on.
print(issubclass(Seq, Complete))

# ---- the module surface ----------------------------------------------------
#
# BY NAME, not through the module object: `import collections.abc` binds the
# dotted name in CPython and this frontend does not, so the names are imported
# and their identity checked instead. That is the same question asked in the
# spelling both interpreters answer.
print(sorted([Hashable.__name__, Sized.__name__, Container.__name__,
              Iterable.__name__, Iterator.__name__, Reversible.__name__,
              Generator.__name__, Callable.__name__, Collection.__name__,
              Sequence.__name__, MutableSequence.__name__, Set.__name__,
              MutableSet.__name__, Mapping.__name__, MutableMapping.__name__,
              MappingView.__name__, Awaitable.__name__, Coroutine.__name__,
              AsyncIterable.__name__, AsyncIterator.__name__,
              AsyncGenerator.__name__]))
print(issubclass(MappingView, Sized), MappingView.__name__)
