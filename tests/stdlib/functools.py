# COVERAGE: reduce, wraps, total_ordering, partial, cached_property, lru_cache,
# cache, singledispatch. NOT covered: partialmethod, singledispatchmethod,
# cmp_to_key, update_wrapper as a public name.
import functools

print(functools.reduce(lambda a, b: a + b, [1, 2, 3, 4]))
print(functools.reduce(lambda a, b: a + b, [1, 2, 3], 10))
print(functools.reduce(lambda a, b: a + b, [], 0))

add = functools.partial(lambda a, b, c: a + b + c, 1, 2)
print(add(3))
kw = functools.partial(lambda a, b=0: a * 10 + b, b=5)
print(kw(1))


def documented(x):
    """The docstring."""
    return x


@functools.wraps(documented)
def wrapper(x):
    return documented(x) + 1


print(wrapper.__name__, wrapper.__doc__, wrapper(1))


@functools.total_ordering
class Version:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n

    def __lt__(self, other):
        return self.n < other.n


a, b = Version(1), Version(2)
print(a < b, a <= b, a > b, a >= b, a == b, a != b)

calls = []


@functools.lru_cache(maxsize=None)
def slow(n):
    calls.append(n)
    return n * n


print(slow(4), slow(4), slow(5))
print(calls)
print(slow.cache_info().hits, slow.cache_info().misses)


@functools.cache
def cached(n):
    return n + 1


print(cached(1), cached(1))


@functools.singledispatch
def describe(value):
    return "something"


@describe.register(int)
def _(value):
    return "int %d" % value


@describe.register(str)
def _(value):
    return "str %s" % value


print(describe(3), describe("s"), describe(1.5))


class Lazy:
    def __init__(self):
        self.built = 0

    @functools.cached_property
    def value(self):
        self.built += 1
        return 42


lazy = Lazy()
print(lazy.value, lazy.value, lazy.built)
