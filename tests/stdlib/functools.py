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


# THE ROOT IS WHICHEVER ORDERING OPERATOR THE CLASS WROTE, and only the
# MISSING ones are filled. This assumed `__lt__` and wrote all four
# regardless, so a class rooted on `__gt__` got a `__gt__` back that recursed
# through a `__lt__` it never had, and an operator the class DID write was
# replaced by a derived one.
#
# AND A DERIVED OPERATOR HANDS BACK NotImplemented rather than raising. Each
# one reaches the root as a METHOD -- `type(self).__lt__(self, other)` --
# because spelling it as the OPERATOR raises the moment the root declines,
# from inside the synthesised method and with that call's operands. `5 < v`
# then reported a refusal between 'Version' and 'int', in that order, about a
# comparison the program never wrote.
def rooted(name):
    if name == "lt":
        @functools.total_ordering
        class R:
            def __init__(self, n):
                self.n = n

            def __eq__(self, other):
                return isinstance(other, R) and self.n == other.n

            def __lt__(self, other):
                if not isinstance(other, R):
                    return NotImplemented
                return self.n < other.n

            def __hash__(self):
                return hash(self.n)
        return R
    if name == "le":
        @functools.total_ordering
        class R:
            def __init__(self, n):
                self.n = n

            def __eq__(self, other):
                return isinstance(other, R) and self.n == other.n

            def __le__(self, other):
                if not isinstance(other, R):
                    return NotImplemented
                return self.n <= other.n

            def __hash__(self):
                return hash(self.n)
        return R
    if name == "gt":
        @functools.total_ordering
        class R:
            def __init__(self, n):
                self.n = n

            def __eq__(self, other):
                return isinstance(other, R) and self.n == other.n

            def __gt__(self, other):
                if not isinstance(other, R):
                    return NotImplemented
                return self.n > other.n

            def __hash__(self):
                return hash(self.n)
        return R

    @functools.total_ordering
    class R:
        def __init__(self, n):
            self.n = n

        def __eq__(self, other):
            return isinstance(other, R) and self.n == other.n

        def __ge__(self, other):
            if not isinstance(other, R):
                return NotImplemented
            return self.n >= other.n

        def __hash__(self):
            return hash(self.n)
    return R


for root in ("lt", "le", "gt", "ge"):
    K = rooted(root)
    one, two = K(1), K(2)
    print(root, one < two, one > two, one <= two, one >= two)
    print(root, two < one, two > one, two <= one, two >= one)
    print(root, K(1) <= K(1), K(1) >= K(1), K(1) == K(1), K(1) != K(1))
    print(root, "sorted", [v.n for v in sorted([K(3), K(1), K(2)])])
    # THE ROOT DECLINING IS HANDED BACK, not raised, so the refusal is the
    # one the program's own comparison words -- operands in the order it
    # wrote them, and the operator it used.
    for label, call in (("v < int", lambda: one < 5),
                        ("v > int", lambda: one > 5),
                        ("v <= int", lambda: one <= 5),
                        ("int < v", lambda: 5 < one),
                        ("int > v", lambda: 5 > one)):
        try:
            print(root, label, repr(call()))
        except TypeError as e:
            print(root, label, "TypeError:", e)


# AN OPERATOR THE CLASS WROTE IS LEFT ALONE.
@functools.total_ordering
class Keeps:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return isinstance(other, Keeps) and self.n == other.n

    def __lt__(self, other):
        return self.n < other.n

    def __ge__(self, other):
        return "OWN GE"

    def __hash__(self):
        return hash(self.n)


print("keeps", Keeps(1) >= Keeps(2), Keeps(1) <= Keeps(2), Keeps(1) > Keeps(2))


# AND A CLASS WITH NO ORDERING OPERATOR HAS NOTHING TO DERIVE FROM.
try:
    @functools.total_ordering
    class NoOrder:
        def __eq__(self, other):
            return True

        def __hash__(self):
            return 1

    print("no error")
except ValueError as e:
    print("ValueError:", e)

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


# --- A NAME DEFINED TWICE IS REBINDING, and each definition is its own -----
#
# `@f.register` over two `def _`s is the idiom that makes this worth
# supporting. A call between the two means the FIRST, which is why a rebound
# name goes through the VALUE rather than a symbol picked at compile time --
# and why both definitions take the value path even when both are annotated.
def twice(x):
    return "first-" + str(x)


print(twice(1), twice(x=1))


def twice(y):
    return "second-" + str(y)


print(twice(2), twice(y=2))
try:
    twice(x=2)
except TypeError as exc:
    print(exc)


def typed(a: int) -> str:
    return "typed-first"


print(typed(1), sorted(typed.__annotations__))


def typed(b: bool) -> float:
    return 2.5


print(typed(True), sorted(typed.__annotations__), typed(b=False))
