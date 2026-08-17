"""`functools`, as ordinary Python this compiler compiles.

COVERAGE: reduce, wraps, total_ordering, partial, cached_property, lru_cache, cache, singledispatch.

NOT COVERED: partialmethod, singledispatchmethod, cmp_to_key, update_wrapper as a public name, and
`reduce`'s C fast path.

Restored from `archived/stdlib-prerefactor/` and measured against CPython by
`tests/stdlib/functools.py`. The coverage line above is the module's contract; see
`docs/STDLIB.md`.

A module written HERE rather than as runtime C is one that any Python
programmer can read and fix, and one that cannot drift from the semantics it
is copying -- because it IS the semantics. The frontend splices these
definitions into the program that imports them; see `bundled.py`.

What may be written here is the subset the compiler accepts. That is a real
constraint and a useful one: a construct this module cannot use is one the
compiler should probably support.
"""


def reduce(fn, xs, *rest):
    """`reduce(f, xs)` and `reduce(f, xs, initial)`."""
    items = list(xs)
    if rest:
        acc = rest[0]
        start = 0
    else:
        if not items:
            raise TypeError(
                "reduce() of empty iterable with no initial value")
        acc = items[0]
        start = 1
    for i in range(start, len(items)):
        acc = fn(acc, items[i])
    return acc


def wraps(fn):
    """Copy the identifying attributes of `fn` onto the wrapper.

    `__wrapped__` is part of it: a decorator that hides the function it wraps
    still has to hand it back to anyone who asks.
    """
    def deco(inner):
        inner.__name__ = fn.__name__
        inner.__doc__ = fn.__doc__
        inner.__wrapped__ = fn
        return inner
    return deco


def total_ordering(cls):
    """Fill in the ordering operators a class did not write.

    `__lt__` and `__eq__` are the two it must have; the other four follow from
    them, and writing them out by hand is what this exists to avoid.
    """
    def __gt__(self, other):
        return not (self < other or self == other)

    def __le__(self, other):
        return self < other or self == other

    def __ge__(self, other):
        return not self < other

    def __ne__(self, other):
        return not self == other

    cls.__gt__ = __gt__
    cls.__le__ = __le__
    cls.__ge__ = __ge__
    cls.__ne__ = __ne__
    return cls


def partial(fn, *bound, **held):
    """`partial(f, 1)` -- a callable with some arguments already supplied.

    A CLOSURE rather than a class, which is the whole implementation: the
    attributes CPython puts on the object (`func`, `args`, `keywords`) are set
    on the returned function, so a program that reads them still can.
    """
    def applied(*rest, **more):
        merged = dict(held)
        merged.update(more)
        return fn(*(bound + rest), **merged)

    applied.func = fn
    applied.args = bound
    applied.keywords = held
    return applied


class cached_property:
    """A property computed ONCE and then stored on the instance.

    A descriptor with `__get__` and no `__set__`: that is what makes the
    instance's own attribute win every later read, so the second access never
    reaches this at all and the value costs nothing to fetch.
    """

    def __init__(self, func):
        self.func = func
        self.attrname = "_cached_" + func.__name__
        self.__doc__ = func.__doc__

    def __set_name__(self, owner, name):
        # PEP 487: the descriptor is told the name it was bound to, which is
        # the only way it can know where to cache -- the expression that built
        # it had no idea what it was about to be assigned to.
        self.attrname = "_cached_" + name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        if not hasattr(instance, self.attrname):
            setattr(instance, self.attrname, self.func(instance))
        return getattr(instance, self.attrname)


class _CacheInfo:
    def __init__(self, hits, misses, maxsize, currsize):
        self.hits = hits
        self.misses = misses
        self.maxsize = maxsize
        self.currsize = currsize

    def __repr__(self):
        return "CacheInfo(hits=" + str(self.hits) + ", misses=" \
               + str(self.misses) + ", maxsize=" + repr(self.maxsize) \
               + ", currsize=" + str(self.currsize) + ")"

    def __eq__(self, other):
        return (self.hits, self.misses, self.maxsize, self.currsize) == other


def lru_cache(maxsize=128, typed=False):
    """Remember what a function answered, and answer again without calling it.

    LEAST RECENTLY USED IS WHAT GOES when the cache is full -- so a key that
    keeps being asked for stays, and the eviction order is a property of the
    reads rather than of the writes. `maxsize=None` never evicts.

    Written to accept BOTH spellings CPython does: `@lru_cache` bare, where
    the decorated function arrives as `maxsize`, and `@lru_cache(maxsize=2)`.
    """
    if callable(maxsize):
        return _lru_wrap(maxsize, 128)
    return lambda fn: _lru_wrap(fn, maxsize)


def _lru_wrap(fn, maxsize):
    state = {"hits": 0, "misses": 0}
    cache = {}
    order = []

    def wrapper(*args, **kwargs):
        key = (args, tuple(sorted(kwargs.items()))) if kwargs else args
        if key in cache:
            state["hits"] = state["hits"] + 1
            order.remove(key)
            order.append(key)
            return cache[key]
        state["misses"] = state["misses"] + 1
        made = fn(*args, **kwargs)
        cache[key] = made
        order.append(key)
        if maxsize is not None and len(order) > maxsize:
            del cache[order[0]]
            del order[0]
        return made

    def cache_info():
        return _CacheInfo(state["hits"], state["misses"], maxsize, len(cache))

    def cache_clear():
        cache.clear()
        del order[:]
        state["hits"] = 0
        state["misses"] = 0

    wrapper.cache_info = cache_info
    wrapper.cache_clear = cache_clear
    wrapper.__wrapped__ = fn
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def cache(fn):
    """`lru_cache(maxsize=None)` -- unbounded, so nothing is ever evicted."""
    return _lru_wrap(fn, None)


def singledispatch(fn):
    """A function whose body is chosen by the TYPE of its first argument.

    The default is the function this decorates; every `register` adds a more
    specific one. Dispatch tries an EXACT type match first and falls back to
    `isinstance` in registration order, which is what makes `describe(True)`
    reach the `int` implementation -- a bool is an int, and no one registered
    a bool.
    """
    exact = {}
    order = []

    def dispatch(kind):
        if kind in exact:
            return exact[kind]
        return fn

    def register(first, second=None):
        # BOTH SPELLINGS. `@f.register` reads the type off the annotation of
        # the function it decorates; `@f.register(int)` is given it.
        if second is None and not isinstance(first, type):
            hints = getattr(first, "__annotations__", {})
            kind = None
            for key in hints:
                if key != "return":
                    kind = hints[key]
                    break
            if kind is None:
                raise TypeError("Invalid first argument to register(): "
                                "no type annotation found")
            exact[kind] = first
            order.append((kind, first))
            return first
        if second is not None:
            exact[first] = second
            order.append((first, second))
            return second

        def keep(impl):
            exact[first] = impl
            order.append((first, impl))
            return impl
        return keep

    def wrapper(*args, **kwargs):
        if args:
            found = exact.get(type(args[0]))
            if found is None:
                for pair in order:
                    if isinstance(args[0], pair[0]):
                        found = pair[1]
                        break
            if found is not None:
                return found(*args, **kwargs)
        return fn(*args, **kwargs)

    wrapper.register = register
    wrapper.dispatch = dispatch
    wrapper.registry = exact
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper
