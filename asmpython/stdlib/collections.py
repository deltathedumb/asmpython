"""collections module: high-level container data types.

Implements the most commonly used CPython collections:
  - deque       — double-ended queue with O(1) append/appendleft/pop/popleft
  - Counter     — dict subclass for counting hashable items
  - defaultdict — dict that auto-initialises missing keys via a factory
  - OrderedDict — dict that remembers insertion order (all dicts do in asmpython)
  - namedtuple  — factory that returns a simple class with positional fields

Limitations vs CPython:
  - deque: no maxlen support
  - namedtuple: field names must be plain identifiers; no defaults= support
"""
from __future__ import annotations


class deque:
    """Double-ended queue. append/appendleft are O(1); random access is O(n)."""

    def __init__(self, iterable: list = [], maxlen: int = -1) -> None:
        self._data: list = []
        # -1 stands for CPython's `maxlen=None` (unbounded); asmpython has no
        # None-vs-int union for a field, and a real deque bound is always >= 0.
        self._maxlen: int = maxlen
        for item in iterable:
            self._data.append(item)
        self._trim_right()

    def _trim_right(self) -> None:
        """Drop from the LEFT until the bound holds -- what `append` past a
        bounded deque's capacity does in CPython."""
        if self._maxlen < 0:
            return
        while len(self._data) > self._maxlen:
            del self._data[0]

    def _trim_left(self) -> None:
        """The mirror of `_trim_right`, for growth at the front."""
        if self._maxlen < 0:
            return
        while len(self._data) > self._maxlen:
            del self._data[len(self._data) - 1]

    def append(self, x: object) -> None:
        self._data.append(x)
        self._trim_right()

    def appendleft(self, x: object) -> None:
        self._data.insert(0, x)
        self._trim_left()

    def pop(self) -> object:
        n = len(self._data)
        if n == 0:
            raise IndexError("pop from an empty deque")
        v = self._data[n - 1]
        del self._data[n - 1]
        return v

    def popleft(self) -> object:
        if len(self._data) == 0:
            raise IndexError("pop from an empty deque")
        v = self._data[0]
        del self._data[0]
        return v

    def extend(self, iterable: list) -> None:
        for item in iterable:
            self._data.append(item)
        self._trim_right()

    def extendleft(self, iterable: list) -> None:
        for item in iterable:
            self.appendleft(item)

    def rotate(self, n: int = 1) -> None:
        d = len(self._data)
        if d == 0:
            return
        n = n % d
        if n == 0:
            return
        # rotate right by n: last n elements move to front
        tail = self._data[d - n:]
        head = self._data[0:d - n]
        self._data = tail + head

    def clear(self) -> None:
        self._data = []

    def copy(self) -> deque:
        return deque(self._data, self._maxlen)

    def count(self, x: object) -> int:
        c = 0
        for item in self._data:
            if item == x:
                c = c + 1
        return c

    def remove(self, x: object) -> None:
        i = 0
        n = len(self._data)
        while i < n:
            if self._data[i] == x:
                del self._data[i]
                return
            i = i + 1
        raise ValueError("deque.remove(x): x not in deque")

    def reverse(self) -> None:
        n = len(self._data)
        i = 0
        j = n - 1
        while i < j:
            tmp = self._data[i]
            self._data[i] = self._data[j]
            self._data[j] = tmp
            i = i + 1
            j = j - 1

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, i: int) -> object:
        return self._data[i]

    def __setitem__(self, i: int, v: object) -> None:
        self._data[i] = v

    def __contains__(self, x: object) -> int:
        for item in self._data:
            if item == x:
                return 1
        return 0

    def __repr__(self) -> str:
        return "deque(" + repr(self._data) + ")"


class Counter:
    """Dict-like object that counts hashable items (keys must be strings)."""

    def __init__(self, iterable: list[str] = []) -> None:
        self._counts: dict[str, int] = {}
        for item in iterable:
            if item in self._counts:
                self._counts[item] = self._counts[item] + 1
            else:
                self._counts[item] = 1

    def update(self, iterable: list[str]) -> None:
        for item in iterable:
            if item in self._counts:
                self._counts[item] = self._counts[item] + 1
            else:
                self._counts[item] = 1

    def __getitem__(self, key: str) -> int:
        return self._counts.get(key, 0)

    def __setitem__(self, key: str, val: int) -> None:
        self._counts[key] = val

    def __contains__(self, key: str) -> int:
        return int(key in self._counts)

    def get(self, key: str, default: int = 0) -> int:
        return self._counts.get(key, default)

    def keys(self) -> list[str]:
        return self._counts.keys()

    def values(self) -> list[int]:
        return self._counts.values()

    def items(self) -> list[tuple]:
        # `list[tuple]` (not a bare `list`): the element kind has to survive the
        # return annotation, or a caller's `sorted(c.items())` reprs the pairs
        # as raw pointers.
        return self._counts.items()

    def elements(self) -> list[str]:
        result: list[str] = []
        for k in self._counts:
            c = self._counts[k]
            i = 0
            while i < c:
                result.append(k)
                i = i + 1
        return result

    def most_common(self, n: int = -1) -> list[tuple[str, int]]:
        keys: list[str] = []
        vals: list[int] = []
        for k in self._counts:
            keys.append(k)
            vals.append(self._counts[k])
        # Insertion sort by count descending (stable: equal counts keep
        # insertion order, matching CPython's most_common()).
        i = 1
        ln = len(keys)
        while i < ln:
            kk = keys[i]
            vv = vals[i]
            j = i - 1
            while j >= 0 and vals[j] < vv:
                keys[j + 1] = keys[j]
                vals[j + 1] = vals[j]
                j = j - 1
            keys[j + 1] = kk
            vals[j + 1] = vv
            i = i + 1
        result: list[tuple[str, int]] = []
        limit = ln if n < 0 else (n if n < ln else ln)
        i = 0
        while i < limit:
            result.append((keys[i], vals[i]))
            i = i + 1
        return result

    def total(self) -> int:
        t = 0
        for k in self._counts:
            t = t + self._counts[k]
        return t

    def subtract(self, iterable: list[str]) -> None:
        for item in iterable:
            if item in self._counts:
                self._counts[item] = self._counts[item] - 1
            else:
                self._counts[item] = -1

    def _drop_nonpositive(self) -> None:
        to_remove: list[str] = []
        for k in self._counts:
            if self._counts[k] <= 0:
                to_remove.append(k)
        for k in to_remove:
            del self._counts[k]

    def __add__(self, other: Counter) -> Counter:
        result = Counter([])
        for k in self._counts:
            result._counts[k] = self._counts[k]
        for k in other._counts:
            if k in result._counts:
                result._counts[k] = result._counts[k] + other._counts[k]
            else:
                result._counts[k] = other._counts[k]
        result._drop_nonpositive()
        return result

    def __sub__(self, other: Counter) -> Counter:
        result = Counter([])
        for k in self._counts:
            result._counts[k] = self._counts[k]
        for k in other._counts:
            if k in result._counts:
                result._counts[k] = result._counts[k] - other._counts[k]
            else:
                result._counts[k] = -other._counts[k]
        result._drop_nonpositive()
        return result

    def __and__(self, other: Counter) -> Counter:
        result = Counter([])
        for k in self._counts:
            if k in other._counts:
                a = self._counts[k]
                b = other._counts[k]
                result._counts[k] = a if a < b else b
        result._drop_nonpositive()
        return result

    def __or__(self, other: Counter) -> Counter:
        result = Counter([])
        for k in self._counts:
            result._counts[k] = self._counts[k]
        for k in other._counts:
            if k in result._counts:
                a = result._counts[k]
                b = other._counts[k]
                result._counts[k] = b if b > a else a
            else:
                result._counts[k] = other._counts[k]
        result._drop_nonpositive()
        return result

    def __repr__(self) -> str:
        return "Counter(" + repr(self._counts) + ")"


class defaultdict:
    """dict that auto-initialises missing keys.

    Pass a type-name string as the default_factory:
      "list"  -> default value is []
      "int"   -> default value is 0
      "str"   -> default value is ""
      "set"   -> default value is set()
      "dict"  -> default value is {}

    This replaces CPython's callable default_factory since asmpython does not
    support storing arbitrary callables in instance fields.
    """

    def __init__(self, default_factory: str = "") -> None:
        self._data: dict = {}
        self.default_factory = default_factory

    def _make_default(self) -> object:
        f = self.default_factory
        if f == "list":
            return []
        if f == "int":
            return 0
        if f == "str":
            return ""
        if f == "dict":
            return {}
        return 0

    def __getitem__(self, key: str) -> object:
        if key not in self._data:
            if self.default_factory == "":
                raise KeyError(key)
            self._data[key] = self._make_default()
        return self._data[key]

    def __setitem__(self, key: str, val: object) -> None:
        self._data[key] = val

    def __contains__(self, key: str) -> int:
        return int(key in self._data)

    def get(self, key: str, default: object = 0) -> object:
        return self._data.get(key, default)

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def values(self) -> list:
        return list(self._data.values())

    def items(self) -> list:
        return list(self._data.items())

    def pop(self, key: str, default: object = 0) -> object:
        if key in self._data:
            v = self._data[key]
            del self._data[key]
            return v
        return default

    def update(self, other: dict) -> None:
        self._data.update(other)

    def __repr__(self) -> str:
        return "defaultdict(" + repr(self._data) + ")"


class OrderedDict:
    """Dict that remembers insertion order.

    In asmpython all dicts preserve insertion order already; this class adds
    the CPython-compatible API surface.
    """

    def __init__(self, items: list = []) -> None:
        self._data: dict = {}
        # `OrderedDict([('a', 1), ('b', 2)])` -- CPython's constructor takes
        # any iterable of key/value pairs (and a mapping, which asmpython's
        # dict() already handles for the caller).
        for pair in items:
            self._data[pair[0]] = pair[1]

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __setitem__(self, key: str, val: object) -> None:
        self._data[key] = val

    def __contains__(self, key: str) -> int:
        return int(key in self._data)

    def get(self, key: str, default: object = 0) -> object:
        return self._data.get(key, default)

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def values(self) -> list:
        return list(self._data.values())

    def items(self) -> list:
        return list(self._data.items())

    def pop(self, key: str, default: object = 0) -> object:
        if key in self._data:
            v = self._data[key]
            del self._data[key]
            return v
        return default

    def update(self, other: dict) -> None:
        self._data.update(other)

    def setdefault(self, key: str, default: object = 0) -> object:
        if key not in self._data:
            self._data[key] = default
        return self._data[key]

    def __len__(self) -> int:
        return len(self._data)

    def move_to_end(self, key: str, last: int = 1) -> None:
        """Move an existing key to either end of the ordered dict.

        Re-inserting a key naturally moves it to the end (asmpython dicts
        append new keys to their insertion-order buffer); moving to the
        front requires rebuilding the dict with that key first.
        """
        v = self._data[key]
        del self._data[key]
        if last:
            self._data[key] = v
        else:
            new_data: dict = {key: v}
            for k in self._data:
                new_data[k] = self._data[k]
            self._data = new_data

    def popitem(self, last: int = 1) -> tuple:
        """Remove and return a (key, value) pair, LIFO order by default."""
        if len(self._data) == 0:
            raise KeyError("popitem(): dictionary is empty")
        keys: list[str] = list(self._data.keys())
        if last:
            k = keys[len(keys) - 1]
        else:
            k = keys[0]
        v = self._data[k]
        del self._data[k]
        return (k, v)

    def __repr__(self) -> str:
        return "OrderedDict(" + repr(self._data) + ")"


class ChainMap:
    """A group of dicts (or mappings) searched in order.

    ChainMap(m1, m2, ...) looks up keys in m1 first, then m2, etc.
    Writes always go to the first map.
    """

    def __init__(self, first: dict = {}, second: dict = {}, third: dict = {}) -> None:
        self._maps: list = [first, second, third]

    def __getitem__(self, key: str) -> object:
        for m in self._maps:
            if key in m:
                return m[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: object) -> None:
        self._maps[0][key] = value

    def __contains__(self, key: str) -> int:
        for m in self._maps:
            if key in m:
                return 1
        return 0

    def get(self, key: str, default: object = 0) -> object:
        for m in self._maps:
            if key in m:
                return m[key]
        return default

    def keys(self) -> list:
        seen: dict = {}
        result: list[str] = []
        for m in self._maps:
            for k in m.keys():
                if k not in seen:
                    seen[k] = 1
                    result.append(k)
        return result

    def new_child(self, m: dict = {}) -> ChainMap:
        return ChainMap(m, self._maps[0], self._maps[1])

    @property
    def parents(self) -> ChainMap:
        return ChainMap(self._maps[1], self._maps[2], {})


class UserDict:
    """A dict wrapper that can be subclassed."""

    def __init__(self, initial: dict = {}) -> None:
        self.data: dict = {}
        for k in initial.keys():
            self.data[k] = initial[k]

    def __getitem__(self, key: str) -> object:
        return self.data[key]

    def __setitem__(self, key: str, value: object) -> None:
        self.data[key] = value

    def __delitem__(self, key: str) -> None:
        del self.data[key]

    def __contains__(self, key: str) -> int:
        return 1 if key in self.data else 0

    def __len__(self) -> int:
        return len(self.data)

    def get(self, key: str, default: object = 0) -> object:
        if key in self.data:
            return self.data[key]
        return default

    def keys(self) -> list:
        return self.data.keys()

    def values(self) -> list:
        return self.data.values()

    def items(self) -> list:
        return self.data.items()

    def update(self, other: dict = {}) -> None:
        for k in other.keys():
            self.data[k] = other[k]

    def pop(self, key: str, default: object = 0) -> object:
        if key in self.data:
            v = self.data[key]
            del self.data[key]
            return v
        return default

    def setdefault(self, key: str, default: object = 0) -> object:
        if key not in self.data:
            self.data[key] = default
        return self.data[key]

    def __repr__(self) -> str:
        return "UserDict(" + repr(self.data) + ")"


class UserList:
    """A list wrapper that can be subclassed."""

    def __init__(self, initial: list = []) -> None:
        self.data: list = []
        for x in initial:
            self.data.append(x)

    def __getitem__(self, idx: int) -> object:
        return self.data[idx]

    def __setitem__(self, idx: int, value: object) -> None:
        self.data[idx] = value

    def __len__(self) -> int:
        return len(self.data)

    def __contains__(self, item: object) -> int:
        for x in self.data:
            if x == item:
                return 1
        return 0

    def append(self, item: object) -> None:
        self.data.append(item)

    def extend(self, other: list) -> None:
        for x in other:
            self.data.append(x)

    def insert(self, i: int, item: object) -> None:
        self.data.insert(i, item)

    def remove(self, item: object) -> None:
        i: int = 0
        for x in self.data:
            if x == item:
                del self.data[i]
                return
            i = i + 1

    def pop(self, i: int = -1) -> object:
        if i < 0:
            i = len(self.data) - 1
        v = self.data[i]
        del self.data[i]
        return v

    def index(self, item: object) -> int:
        i: int = 0
        for x in self.data:
            if x == item:
                return i
            i = i + 1
        return -1

    def count(self, item: object) -> int:
        n: int = 0
        for x in self.data:
            if x == item:
                n = n + 1
        return n

    def sort(self) -> None:
        n: int = len(self.data)
        i: int = 0
        while i < n - 1:
            j: int = i + 1
            while j < n:
                if self.data[j] < self.data[i]:
                    tmp = self.data[i]
                    self.data[i] = self.data[j]
                    self.data[j] = tmp
                j = j + 1
            i = i + 1

    def reverse(self) -> None:
        n: int = len(self.data)
        i: int = 0
        while i < n // 2:
            tmp = self.data[i]
            self.data[i] = self.data[n - 1 - i]
            self.data[n - 1 - i] = tmp
            i = i + 1

    def copy(self) -> UserList:
        result: UserList = UserList()
        for x in self.data:
            result.data.append(x)
        return result

    def __repr__(self) -> str:
        return "UserList(" + repr(self.data) + ")"


class UserString:
    """A str wrapper that can be subclassed."""

    def __init__(self, s: str = "") -> None:
        self.data: str = s

    def __str__(self) -> str:
        return self.data

    def __repr__(self) -> str:
        return "UserString(" + repr(self.data) + ")"

    def __len__(self) -> int:
        return len(self.data)

    def __contains__(self, sub: str) -> int:
        return 1 if sub in self.data else 0

    def __add__(self, other: str) -> UserString:
        result: UserString = UserString(self.data + other)
        return result

    def upper(self) -> str:
        return self.data.upper()

    def lower(self) -> str:
        return self.data.lower()

    def strip(self) -> str:
        return self.data.strip()

    def split(self, sep: str = " ") -> list:
        return self.data.split(sep)

    def replace(self, old: str, new: str) -> UserString:
        result: UserString = UserString(self.data.replace(old, new))
        return result

    def startswith(self, prefix: str) -> int:
        return 1 if self.data.startswith(prefix) else 0

    def endswith(self, suffix: str) -> int:
        return 1 if self.data.endswith(suffix) else 0


class _NamedTupleBase:
    """Base class returned by namedtuple(). Fields accessed by index."""

    def __init__(self, *values: object, **kwargs: object) -> None:
        # The generated namedtuple subclass owns the field-name metadata;
        # instances only retain their positional values.  Accepting arbitrary
        # keyword arguments mirrors object.__init__ for subclasses that use a
        # custom __new__ (for example doctest.TestResults).
        self._values: list[object] = list(values)

    def __getitem__(self, i: int) -> object:
        return self._values[i]

    def __len__(self) -> int:
        return len(self._values)

    def _asdict(self) -> dict:
        return dict(zip(self._fields, self._values))

    def _replace(self, **changes: object) -> _NamedTupleBase:
        values = list(self._values)
        for name, value in changes.items():
            values[self._fields.index(name)] = value
        return self.__class__(*values)


def namedtuple(typename: str, field_names: str) -> _NamedTupleBase:
    """Create a lightweight dynamic tuple class with named field descriptors."""
    if isinstance(field_names, str):
        names = field_names.replace(",", " ").split()
    else:
        names = list(field_names)
    namespace: dict[str, object] = {"_fields": tuple(names)}
    for index, name in enumerate(names):
        namespace[name] = property(lambda value, i=index: value[i])
    return type(typename, (_NamedTupleBase,), namespace)
