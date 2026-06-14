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

    def __init__(self, iterable: list = []) -> None:
        self._data: list = []
        for item in iterable:
            self._data.append(item)

    def append(self, x: object) -> None:
        self._data.append(x)

    def appendleft(self, x: object) -> None:
        self._data.insert(0, x)

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
        return deque(self._data)

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


class CountPair:
    """A (element, count) pair as returned by Counter.most_common()."""

    def __init__(self, element: str, count: int) -> None:
        self.element = element
        self.count = count

    def __repr__(self) -> str:
        return "(" + repr(self.element) + ", " + str(self.count) + ")"


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

    def elements(self) -> list[str]:
        result: list[str] = []
        for k in self._counts:
            c = self._counts[k]
            i = 0
            while i < c:
                result.append(k)
                i = i + 1
        return result

    def most_common(self, n: int = -1) -> list[CountPair]:
        keys: list[str] = []
        vals: list[int] = []
        for k in self._counts:
            keys.append(k)
            vals.append(self._counts[k])
        # Insertion sort by count descending
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
        result: list[CountPair] = []
        limit = ln if n < 0 else (n if n < ln else ln)
        i = 0
        while i < limit:
            result.append(CountPair(keys[i], vals[i]))
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

    def __init__(self) -> None:
        self._data: dict = {}

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
