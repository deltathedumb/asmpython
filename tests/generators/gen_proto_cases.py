"""Generate operator- and iteration-protocol conformance probes.

In CPython, `a + b`, `x in xs`, `for v in xs`, `xs[i]` and `len(xs)` are not
operations on builtin types -- they are dispatches through `__add__`,
`__contains__`, `__iter__`, `__getitem__` and `__len__`, with documented
fallbacks when the preferred slot is missing. A compiler that recognises the
builtin container types and emits direct code for them gets every builtin case
right and every user-defined case wrong, and the two are indistinguishable from
a test that only ever indexes a list.

The fallbacks are where this bites hardest, because they are invisible until a
type declines to implement the fast path:

* `in` falls back to iteration when `__contains__` is absent
* `for` falls back to `__getitem__(0), __getitem__(1), ...` when `__iter__` is
  absent, stopping at IndexError
* `a + b` tries `b.__radd__(a)` when `a.__add__(b)` returns NotImplemented
* `a += b` falls back to `a = a + b` when `__iadd__` is absent -- which changes
  whether the operation mutates or rebinds
* rich comparison reflects `<` to the other operand's `>`

FAILURE_AUDIT.md rank 8 is exactly this ("operator / indexing / iteration
protocol gap", 9 cases, verified), and ranks 21-22 are two more single-case
symptoms of it.

Usage: python gen_proto_cases.py <tests/cases dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _emit import CaseSet, main  # noqa: E402

CASES = CaseSet("probes")
case = CASES.case


# ---------------------------------------------------------------------------
# subscription: __getitem__ / __setitem__ / __delitem__
# ---------------------------------------------------------------------------

case("proto_getitem_int_index", "__getitem__ serves integer subscription", r'''
class Squares:
    def __getitem__(self, index):
        return index * index


s = Squares()
print(s[3])
print(s[5])
''')

case("proto_getitem_receives_slice", "__getitem__ receives a slice object", r'''
class Probe:
    def __getitem__(self, key):
        return (key.start, key.stop, key.step)


print(Probe()[1:5:2])
''')

case("proto_getitem_string_key", "__getitem__ serves non-integer keys", r'''
class Lookup:
    def __getitem__(self, key):
        return "value-for-" + key


print(Lookup()["name"])
''')

case("proto_setitem", "__setitem__ serves subscript assignment", r'''
class Store:
    def __init__(self):
        self.data = {}

    def __setitem__(self, key, value):
        self.data[key] = value

    def __getitem__(self, key):
        return self.data[key]


s = Store()
s["k"] = 7
print(s["k"])
''')

case("proto_delitem", "__delitem__ serves del on a subscript", r'''
class Store:
    def __init__(self):
        self.data = {"a": 1, "b": 2}

    def __delitem__(self, key):
        del self.data[key]

    def __len__(self):
        return len(self.data)


s = Store()
del s["a"]
print(len(s))
''')

case("proto_negative_index_is_passed_through", "a negative index reaches __getitem__ unchanged", r'''
class Echo:
    def __getitem__(self, index):
        return index


print(Echo()[-1])
''')

case("proto_getitem_iteration_fallback", "for falls back to __getitem__ when __iter__ is absent", r'''
class Countdown:
    def __getitem__(self, index):
        if index > 2:
            raise IndexError(index)
        return 10 - index


print([v for v in Countdown()])
''')


# ---------------------------------------------------------------------------
# iteration: __iter__ / __next__
# ---------------------------------------------------------------------------

case("proto_iter_custom_object", "__iter__/__next__ drive a for loop", r'''
class UpTo:
    def __init__(self, limit):
        self.limit = limit
        self.current = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.current >= self.limit:
            raise StopIteration
        self.current = self.current + 1
        return self.current


for v in UpTo(3):
    print(v)
''')

case("proto_iter_returns_separate_iterator", "__iter__ may hand back a fresh iterator", r'''
class Bag:
    def __init__(self, items):
        self.items = items

    def __iter__(self):
        return iter(self.items)


bag = Bag([1, 2])
print(list(bag))
print(list(bag))
''')

case("proto_iter_via_generator_method", "__iter__ implemented as a generator", r'''
class Fib:
    def __iter__(self):
        a, b = 0, 1
        for _ in range(5):
            yield a
            a, b = b, a + b


print(list(Fib()))
''')

case("proto_next_builtin_on_custom", "next() drives a custom __next__", r'''
class Once:
    def __init__(self):
        self.done = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.done:
            raise StopIteration
        self.done = True
        return "only"


it = iter(Once())
print(next(it))
print(next(it, "default"))
''')

case("proto_custom_iter_feeds_builtins", "a custom iterable feeds sum/list/max", r'''
class Three:
    def __iter__(self):
        return iter([3, 1, 2])


print(sum(Three()))
print(sorted(Three()))
print(max(Three()))
''')

case("proto_custom_iter_feeds_zip", "a custom iterable works with zip", r'''
class Letters:
    def __iter__(self):
        return iter(["a", "b"])


print(list(zip([1, 2], Letters())))
''')

case("proto_custom_iter_unpacks", "a custom iterable unpacks into names", r'''
class Pair:
    def __iter__(self):
        return iter([10, 20])


left, right = Pair()
print(left)
print(right)
''')

case("proto_reversed_dunder", "reversed() dispatches to __reversed__", r'''
class Backwards:
    def __reversed__(self):
        return iter(["last", "first"])


print(list(reversed(Backwards())))
''')

case("proto_reversed_len_getitem", "reversed() falls back to __len__ plus __getitem__", r'''
class Seq:
    def __len__(self):
        return 3

    def __getitem__(self, index):
        return index * 10


print(list(reversed(Seq())))
''')


# ---------------------------------------------------------------------------
# membership
# ---------------------------------------------------------------------------

case("proto_contains_dunder", "in dispatches to __contains__", r'''
class Evens:
    def __contains__(self, value):
        return value % 2 == 0


e = Evens()
print(4 in e)
print(5 in e)
print(5 not in e)
''')

case("proto_contains_iteration_fallback", "in falls back to iteration when __contains__ is absent", r'''
class Bag:
    def __iter__(self):
        return iter(["a", "b"])


bag = Bag()
print("a" in bag)
print("z" in bag)
''')

case("proto_contains_uses_equality", "membership compares with __eq__", r'''
class Tag:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, Tag) and self.name == other.name

    def __hash__(self):
        return hash(self.name)


print(Tag("a") in [Tag("a"), Tag("b")])
print(Tag("z") in [Tag("a"), Tag("b")])
''')


# ---------------------------------------------------------------------------
# arithmetic and reflected operators
# ---------------------------------------------------------------------------

case("proto_add_dunder", "__add__ serves the + operator", r'''
class Money:
    def __init__(self, amount):
        self.amount = amount

    def __add__(self, other):
        return Money(self.amount + other.amount)


print((Money(2) + Money(3)).amount)
''')

case("proto_radd_reflected", "__radd__ handles a left operand that declines", r'''
class Money:
    def __init__(self, amount):
        self.amount = amount

    def __radd__(self, other):
        return Money(other + self.amount)


print((1 + Money(2)).amount)
''')

case("proto_radd_enables_sum", "sum() reaches __radd__ starting from 0", r'''
class Money:
    def __init__(self, amount):
        self.amount = amount

    def __add__(self, other):
        if isinstance(other, Money):
            return Money(self.amount + other.amount)
        return Money(self.amount + other)

    def __radd__(self, other):
        return Money(self.amount + other)


print(sum([Money(1), Money(2)]).amount)
''')

case("proto_sub_mul_dunders", "__sub__ and __mul__ serve - and *", r'''
class Vec:
    def __init__(self, n):
        self.n = n

    def __sub__(self, other):
        return Vec(self.n - other.n)

    def __mul__(self, factor):
        return Vec(self.n * factor)


print((Vec(5) - Vec(2)).n)
print((Vec(5) * 3).n)
''')

case("proto_iadd_mutates_in_place", "__iadd__ makes += mutate rather than rebind", r'''
class Accum:
    def __init__(self):
        self.items = []

    def __iadd__(self, value):
        self.items.append(value)
        return self


a = Accum()
same = a
a += "x"
a += "y"
print(a.items)
print(same is a)
''')

case("proto_iadd_falls_back_to_add", "+= falls back to __add__ when __iadd__ is absent", r'''
class Count:
    def __init__(self, n):
        self.n = n

    def __add__(self, other):
        return Count(self.n + other)


c = Count(1)
original = c
c += 2
print(c.n)
print(c is original)
''')

case("proto_list_iadd_extends", "list += iterable extends in place", r'''
xs = [1]
alias = xs
xs += [2, 3]
print(xs)
print(alias is xs)
''')

case("proto_neg_and_abs", "__neg__ and __abs__ serve unary - and abs()", r'''
class Signed:
    def __init__(self, n):
        self.n = n

    def __neg__(self):
        return Signed(-self.n)

    def __abs__(self):
        return Signed(abs(self.n))


print((-Signed(3)).n)
print(abs(Signed(-4)).n)
''')

case("proto_bitwise_dunders", "__and__/__or__/__xor__ serve the bit operators", r'''
class Flags:
    def __init__(self, bits):
        self.bits = bits

    def __and__(self, other):
        return Flags(self.bits & other.bits)

    def __or__(self, other):
        return Flags(self.bits | other.bits)

    def __xor__(self, other):
        return Flags(self.bits ^ other.bits)


print((Flags(6) & Flags(3)).bits)
print((Flags(6) | Flags(3)).bits)
print((Flags(6) ^ Flags(3)).bits)
''')

case("proto_matmul_dunder", "__matmul__ serves the @ operator", r'''
class Mat:
    def __init__(self, tag):
        self.tag = tag

    def __matmul__(self, other):
        return Mat(self.tag + "@" + other.tag)


print((Mat("a") @ Mat("b")).tag)
''')

case("proto_truediv_floordiv_mod", "__truediv__/__floordiv__/__mod__ serve / // %", r'''
class Num:
    def __init__(self, n):
        self.n = n

    def __truediv__(self, other):
        return Num(self.n / other)

    def __floordiv__(self, other):
        return Num(self.n // other)

    def __mod__(self, other):
        return Num(self.n % other)


print((Num(7) / 2).n)
print((Num(7) // 2).n)
print((Num(7) % 2).n)
''')


# ---------------------------------------------------------------------------
# rich comparison
# ---------------------------------------------------------------------------

case("proto_lt_gt_reflection", "a < b reflects to b.__gt__(a)", r'''
class OnlyGt:
    def __init__(self, n):
        self.n = n

    def __gt__(self, other):
        return self.n > other


print(1 < OnlyGt(5))
print(9 < OnlyGt(5))
''')

case("proto_all_rich_comparisons", "each rich comparison dunder is reachable", r'''
class Num:
    def __init__(self, n):
        self.n = n

    def __lt__(self, other):
        return self.n < other.n

    def __le__(self, other):
        return self.n <= other.n

    def __gt__(self, other):
        return self.n > other.n

    def __ge__(self, other):
        return self.n >= other.n


print(Num(1) < Num(2))
print(Num(2) <= Num(2))
print(Num(3) > Num(2))
print(Num(2) >= Num(3))
''')

case("proto_total_ordering_fills_in", "functools.total_ordering derives the rest", r'''
import functools


@functools.total_ordering
class Version:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n

    def __lt__(self, other):
        return self.n < other.n


print(Version(1) < Version(2))
print(Version(3) > Version(2))
print(Version(2) >= Version(2))
''')

case("proto_sort_key_beats_lt", "sorted(key=) does not consult __lt__", r'''
class Item:
    def __init__(self, n):
        self.n = n

    def __lt__(self, other):
        raise AssertionError("__lt__ must not be used when key= is given")


print([i.n for i in sorted([Item(3), Item(1)], key=lambda i: i.n)])
''')

case("proto_sort_reverse_flag", "sorted(reverse=True) inverts the order", r'''
print(sorted([2, 3, 1], reverse=True))
''')

case("proto_min_max_key", "min/max accept a key function", r'''
words = ["bbb", "a", "cc"]
print(min(words, key=len))
print(max(words, key=len))
''')

case("proto_comparison_chaining", "a < b < c evaluates as a chain", r'''
def note(v):
    print("evaluated " + str(v))
    return v


print(1 < note(2) < 3)
print(5 < note(2) < 3)
''')

case("proto_eq_returns_notimplemented", "NotImplemented falls through to identity", r'''
class Picky:
    def __eq__(self, other):
        if not isinstance(other, Picky):
            return NotImplemented
        return True


p = Picky()
print(p == Picky())
print(p == 1)
print(p == p)
''')


# ---------------------------------------------------------------------------
# hashing and identity in containers
# ---------------------------------------------------------------------------

case("proto_hash_groups_equal_keys", "equal objects collapse to one dict key", r'''
class Key:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n

    def __hash__(self):
        return hash(self.n)


table = {Key(1): "a", Key(1): "b", Key(2): "c"}
print(len(table))
print(table[Key(1)])
''')

case("proto_hash_in_set_dedupes", "a set drops equal-and-equally-hashed members", r'''
class Key:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n

    def __hash__(self):
        return hash(self.n)


print(len({Key(1), Key(1), Key(2)}))
''')

case("proto_tuple_is_hashable_key", "a tuple works as a dict key", r'''
grid = {}
grid[(0, 1)] = "a"
grid[(1, 0)] = "b"
print(grid[(0, 1)])
print(len(grid))
''')

case("proto_list_is_unhashable", "a list cannot be a dict key", r'''
try:
    {}[[1, 2]] = "x"
    print("accepted")
except TypeError:
    print("refused")
''')


# ---------------------------------------------------------------------------
# unpacking and starred targets
# ---------------------------------------------------------------------------

case("proto_star_unpack_middle", "a starred target absorbs the middle", r'''
first, *rest = [1, 2, 3, 4]
print(first)
print(rest)
head, *middle, tail = [1, 2, 3, 4]
print(middle)
print(tail)
''')

case("proto_star_unpack_into_call", "*seq spreads into a call", r'''
def add3(a, b, c):
    return a + b + c


print(add3(*[1, 2, 3]))
''')

case("proto_doublestar_unpack_into_call", "**mapping spreads into keywords", r'''
def describe(name, count):
    return name + "=" + str(count)


print(describe(**{"name": "x", "count": 2}))
''')

case("proto_nested_tuple_unpack_in_for", "a for target can destructure nested pairs", r'''
pairs = [(1, ("a", "b")), (2, ("c", "d"))]
for number, (left, right) in pairs:
    print(number, left, right)
''')

case("proto_enumerate_start", "enumerate accepts a start index", r'''
for index, value in enumerate(["a", "b"], start=1):
    print(index, value)
''')

case("proto_zip_strict", "zip(strict=True) rejects ragged inputs (3.10+)", r'''
try:
    print(list(zip([1, 2], ["a"], strict=True)))
except ValueError:
    print("ragged refused")
print(list(zip([1, 2], ["a", "b"], strict=True)))
''')


# ---------------------------------------------------------------------------
# sequence protocol on builtins that the audit flagged
# ---------------------------------------------------------------------------

case("proto_tuple_concat", "tuple + tuple concatenates", r'''
print((1, 2) + (3,))
''')

case("proto_tuple_repeat", "tuple * int repeats", r'''
print((1, 2) * 2)
''')

case("proto_list_slice_assignment", "assigning to a slice splices the list", r'''
xs = [1, 2, 3, 4]
xs[1:3] = ["a"]
print(xs)
''')

case("proto_slice_step_and_negative", "extended slices honour step and negatives", r'''
xs = [0, 1, 2, 3, 4, 5]
print(xs[::2])
print(xs[::-1])
print(xs[-2:])
print(xs[1:-1])
''')

case("proto_str_slice_step", "a str slice honours a negative step", r'''
s = "abcdef"
print(s[::-1])
print(s[1:4])
print(s[::2])
''')


if __name__ == "__main__":
    raise SystemExit(main(CASES, "gen_proto_cases.py", sys.argv))
