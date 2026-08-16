"""Container datatypes.

Every one of these is a class in CPython that INHERITS FROM A BUILTIN --
`Counter` from `dict`, `namedtuple`'s result from `tuple`. This compiler
refuses to subclass a builtin, so each is written as a class that HOLDS one
and forwards the mapping or sequence protocol to it.

WHAT THAT COSTS, stated rather than hidden: `isinstance(Counter(), dict)` is
False here and True in CPython. Everything a program does WITH one of these --
index it, iterate it, compare it, ask its length -- goes through the dunders
below and behaves. The gap is the type test alone.

`namedtuple` IS the exception, and deliberately: its result extends `tuple`,
because being one is most of what a named tuple is for. The mappings could
follow now that a class may extend a builtin; they have not, because each
would then have two places to keep its entries and only one of them is the
one its methods read.
"""


class _Mapping:
    """The half of `dict` every mapping below shares.

    Written once here rather than five times: these classes differ in how a
    lookup RESOLVES and agree on everything after it.
    """

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return list(self._data)

    def values(self):
        return [self._data[k] for k in self._data]

    def items(self):
        return [(k, self._data[k]) for k in self._data]

    def get(self, key, default=None):
        return self._data[key] if key in self._data else default

    def pop(self, key, *default):
        if key in self._data:
            out = self._data[key]
            del self._data[key]
            return out
        if default:
            return default[0]
        raise KeyError(key)

    def setdefault(self, key, default=None):
        if key not in self._data:
            self._data[key] = default
        return self._data[key]

    def update(self, other=None, **kw):
        if other is not None:
            pairs = other.items() if hasattr(other, "items") else other
            for pair in pairs:
                self._data[pair[0]] = pair[1]
        for key in kw:
            self._data[key] = kw[key]

    def clear(self):
        self._data.clear()

    def copy(self):
        return type(self)(dict(self._data))

    def __setitem__(self, key, value):
        self._data[key] = value

    def __delitem__(self, key):
        del self._data[key]

    def __eq__(self, other):
        return self._data == (other._data if isinstance(other, _Mapping)
                              else other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return type(self).__name__ + "(" + repr(self._data) + ")"


class defaultdict(_Mapping):
    """A mapping that MAKES a value for a missing key rather than refusing.

    The made value is STORED, which is the whole point: `d[k].append(v)` has
    to mutate something the mapping keeps, so a fresh list handed back and
    forgotten would silently drop every append.
    """

    def __init__(self, default_factory=None, *args):
        self.default_factory = default_factory
        self._data = dict(args[0]) if args else {}

    def __getitem__(self, key):
        if key not in self._data:
            if self.default_factory is None:
                raise KeyError(key)
            self._data[key] = self.default_factory()
        return self._data[key]

    def __missing__(self, key):
        return self.__getitem__(key)

    def copy(self):
        return defaultdict(self.default_factory, dict(self._data))


class Counter(_Mapping):
    """A mapping from a value to how many times it was seen.

    A MISSING KEY IS ZERO AND IS NOT STORED -- `c["z"]` on an unseen value
    answers 0 and leaves the mapping the size it was, which is what makes a
    counter safe to probe.
    """

    def __init__(self, source=None):
        self._data = {}
        if source is not None:
            self.update(source)

    def update(self, source=None, **kw):
        if source is not None:
            if hasattr(source, "items"):
                for pair in source.items():
                    self._data[pair[0]] = self.get(pair[0], 0) + pair[1]
            else:
                for item in source:
                    self._data[item] = self.get(item, 0) + 1
        for key in kw:
            self._data[key] = self.get(key, 0) + kw[key]

    def __getitem__(self, key):
        return self._data[key] if key in self._data else 0

    def most_common(self, n=None):
        pairs = self.items()
        # SORTED BY COUNT, HIGHEST FIRST, and ties keep the order they were
        # first seen in -- which a stable sort on the count alone gives.
        ordered = sorted(pairs, key=lambda pair: -pair[1])
        return ordered if n is None else ordered[:n]

    def elements(self):
        out = []
        for pair in self.items():
            for _ in range(pair[1]):
                out.append(pair[0])
        return out

    def total(self):
        return sum(self.values())

    def __add__(self, other):
        out = Counter()
        out._data = dict(self._data)
        for pair in other.items():
            out._data[pair[0]] = out[pair[0]] + pair[1]
        return out

    def __sub__(self, other):
        out = Counter()
        for pair in self.items():
            left = pair[1] - other[pair[0]]
            if left > 0:
                out._data[pair[0]] = left
        return out


class OrderedDict(_Mapping):
    """A mapping that COMPARES ORDER-SENSITIVELY.

    Insertion order is what a plain dict already keeps, so the only thing this
    adds over one is `move_to_end` and an `__eq__` that says two mappings with
    the same pairs in different orders are different.
    """

    def __init__(self, source=None):
        self._data = {}
        if source is not None:
            self.update(source)

    def __getitem__(self, key):
        return self._data[key]

    def move_to_end(self, key, last=True):
        value = self._data[key]
        del self._data[key]
        if last:
            self._data[key] = value
            return
        # A dict has no prepend, so the front is reached by rebuilding.
        rest = dict(self._data)
        self._data.clear()
        self._data[key] = value
        for other in rest:
            self._data[other] = rest[other]

    def popitem(self, last=True):
        keys = list(self._data)
        if not keys:
            raise KeyError("dictionary is empty")
        key = keys[-1] if last else keys[0]
        value = self._data[key]
        del self._data[key]
        return (key, value)

    def __eq__(self, other):
        if isinstance(other, OrderedDict):
            return list(self.items()) == list(other.items())
        return self._data == (other._data if isinstance(other, _Mapping)
                              else other)


class ChainMap(_Mapping):
    """Several mappings searched IN ORDER and written to only in the first.

    Nothing is copied: the maps stay the objects the caller handed over, so a
    later write to one of them shows through, and a write here lands in
    `maps[0]` and is invisible to the rest.
    """

    def __init__(self, *maps):
        self.maps = list(maps) if maps else [{}]

    def __getitem__(self, key):
        for m in self.maps:
            if key in m:
                return m[key]
        raise KeyError(key)

    def __setitem__(self, key, value):
        self.maps[0][key] = value

    def __delitem__(self, key):
        del self.maps[0][key]

    def __contains__(self, key):
        for m in self.maps:
            if key in m:
                return True
        return False

    def __len__(self):
        return len(self.keys())

    def __iter__(self):
        return iter(self.keys())

    def keys(self):
        # FIRST MAP WINS, and a key in two maps is listed once.
        out = []
        for m in self.maps:
            for key in m:
                if key not in out:
                    out.append(key)
        return out

    def values(self):
        return [self[key] for key in self.keys()]

    def items(self):
        return [(key, self[key]) for key in self.keys()]

    def get(self, key, default=None):
        return self[key] if key in self else default

    def new_child(self, m=None):
        return ChainMap(m if m is not None else {}, *self.maps)

    @property
    def parents(self):
        return ChainMap(*self.maps[1:])

    def __repr__(self):
        return "ChainMap(" + ", ".join([repr(m) for m in self.maps]) + ")"


class deque:
    """A sequence cheap to grow and shrink at BOTH ends.

    Backed by a list, so `popleft` is linear here where CPython's is constant.
    That is a performance difference and not a behavioural one; every answer
    below is the answer CPython gives.
    """

    def __init__(self, source=None, maxlen=None):
        self.maxlen = maxlen
        self._items = []
        if source is not None:
            self.extend(source)

    def _trim(self, from_left):
        if self.maxlen is None:
            return
        while len(self._items) > self.maxlen:
            # THE OLD END GOES. Appending on the right drops from the left,
            # which is what makes a bounded deque a sliding window.
            if from_left:
                del self._items[-1]
            else:
                del self._items[0]

    def append(self, item):
        self._items.append(item)
        self._trim(False)

    def appendleft(self, item):
        self._items.insert(0, item)
        self._trim(True)

    def extend(self, source):
        for item in source:
            self.append(item)

    def extendleft(self, source):
        for item in source:
            self.appendleft(item)

    def pop(self):
        if not self._items:
            raise IndexError("pop from an empty deque")
        out = self._items[-1]
        del self._items[-1]
        return out

    def popleft(self):
        if not self._items:
            raise IndexError("pop from an empty deque")
        out = self._items[0]
        del self._items[0]
        return out

    def rotate(self, n=1):
        if not self._items:
            return
        n = n % len(self._items)
        if n:
            self._items = self._items[-n:] + self._items[:-n]

    def clear(self):
        self._items = []

    def count(self, value):
        return self._items.count(value)

    def remove(self, value):
        self._items.remove(value)

    def reverse(self):
        self._items.reverse()

    def index(self, value):
        return self._items.index(value)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, i):
        return self._items[i]

    def __setitem__(self, i, value):
        self._items[i] = value

    def __contains__(self, value):
        return value in self._items

    def __eq__(self, other):
        if isinstance(other, deque):
            return self._items == other._items
        return NotImplemented

    def __repr__(self):
        if self.maxlen is None:
            return "deque(" + repr(self._items) + ")"
        return "deque(" + repr(self._items) + ", maxlen=" \
               + repr(self.maxlen) + ")"


def namedtuple(typename, field_names):
    """A tuple whose positions ALSO HAVE NAMES.

    The result is a real class built at run time, which is what `namedtuple`
    is: a class factory. Its instances are NOT tuples here -- see the module
    docstring -- but they index, unpack, compare and print as one.
    """
    if isinstance(field_names, str):
        fields = field_names.replace(",", " ").split()
    else:
        fields = list(field_names)
    fields = tuple(fields)

    class _Tuple(tuple):
        # A NAMED TUPLE IS A TUPLE, which is what
        # `isinstance(p, tuple)` asks and what every function taking a
        # sequence relies on. The body writes the whole sequence protocol
        # itself; the base is what makes the claim true.
        _fields = fields

        def __init__(self, *args, **kwargs):
            values = list(args)
            for name in fields[len(args):]:
                if name not in kwargs:
                    raise TypeError(typename + "() missing argument "
                                    + repr(name))
                values.append(kwargs[name])
            if len(values) != len(fields):
                raise TypeError(typename + "() takes " + str(len(fields))
                                + " arguments")
            self._values = tuple(values)
            for i in range(len(fields)):
                setattr(self, fields[i], values[i])

        def __getitem__(self, i):
            return self._values[i]

        def __len__(self):
            return len(self._values)

        def __iter__(self):
            return iter(self._values)

        def __eq__(self, other):
            if isinstance(other, _Tuple):
                return self._values == other._values
            return self._values == other

        def __ne__(self, other):
            return not self.__eq__(other)

        def __hash__(self):
            return hash(self._values)

        def _asdict(self):
            out = {}
            for i in range(len(fields)):
                out[fields[i]] = self._values[i]
            return out

        def _replace(self, **kw):
            values = []
            for i in range(len(fields)):
                name = fields[i]
                values.append(kw[name] if name in kw else self._values[i])
            return _Tuple(*values)

        def __repr__(self):
            parts = []
            for i in range(len(fields)):
                parts.append(fields[i] + "=" + repr(self._values[i]))
            return typename + "(" + ", ".join(parts) + ")"

    _Tuple.__name__ = typename
    _Tuple.__qualname__ = typename
    return _Tuple
