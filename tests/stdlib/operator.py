# COVERAGE: the arithmetic/comparison/bitwise/sequence/identity functions
# (add sub mul truediv floordiv mod pow neg pos abs invert lshift rshift
# and_ or_ xor not_ lt le eq ne gt ge is_ is_not is_none is_not_none contains
# concat countOf indexOf index matmul getitem setitem delitem length_hint
# truth call), attrgetter/itemgetter/methodcaller including their
# multi-argument and dotted-path forms, and every in-place variant (iadd
# isub imul itruediv ifloordiv imod ipow ilshift irshift iand ior ixor
# iconcat) against both an int and a list. NOT refused BY NAME, but not
# fully exercised here: `methodcaller` against a BUILTIN type's own method
# (`methodcaller('upper')('hi')`) -- this compiler has no bound-method VALUE
# for a builtin instance yet (`frontends/python/methods.py` says so of
# itself), so it is exercised here against a user-defined class only. See
# the bundled module's docstring for that, for the one CPython source line
# that could not be copied as written (a builtin captured before its own
# name is shadowed), for a real compiler fix this module needed
# (`a.__index__()` on a builtin, added to both runtimes), and for why
# `imatmul` below is exercised against a class with no `__imatmul__` of its
# own (seven of the twelve in-place operators do not consult one yet).
#
# Run under CPython and under uasm; the outputs must be identical. So
# the assertions below are written against what the module IS SPECIFIED to
# do, not against what uasm currently does.
import operator


# Arithmetic ####################################################################
print(operator.add(2, 3))
print(operator.sub(7, 4))
print(operator.mul(3, 6))
print(operator.truediv(7, 2))
print(operator.floordiv(7, 2))
print(operator.mod(7, 2))
print(operator.pow(2, 10))
print(operator.neg(5))
print(operator.neg(-5))
print(operator.pos(-5))
print(operator.abs(-9))
print(operator.abs(9))

# `abs` DISPATCHES THROUGH `__abs__`, on a user class too -- exactly like the
# builtin `abs()` does, which is the whole point of writing it that way.
class Clock:
    def __abs__(self):
        return "tick"

print(operator.abs(Clock()))

print(operator.invert(5))
print(operator.inv(5))
print(operator.lshift(1, 4))
print(operator.rshift(256, 4))
print(operator.and_(0b1100, 0b1010))
print(operator.or_(0b1100, 0b1010))
print(operator.xor(0b1100, 0b1010))

# NOT `__imatmul__` too -- see the module docstring: this compiler's
# augmented assignment does not consult a user class's OWN `__imatmul__`
# (nor six of its eleven siblings), so `Grid` here defines `__matmul__`
# alone and `imatmul` is exercised through the fallback every in-place
# operator has for a type that does not define its own -- which is exactly
# what CPython does too when only `__matmul__` exists.
class Grid:
    def __init__(self, tag):
        self.tag = tag
    def __matmul__(self, other):
        return self.tag + "@" + other.tag

print(operator.matmul(Grid("a"), Grid("b")))

# Comparisons and logic ##########################################################
for a, b in [(1, 2), (2, 2), (3, 2)]:
    print(a, b, operator.lt(a, b), operator.le(a, b), operator.eq(a, b),
          operator.ne(a, b), operator.ge(a, b), operator.gt(a, b))

print(operator.not_(0), operator.not_(1), operator.not_([]), operator.not_([1]))
print(operator.truth(0), operator.truth(5), operator.truth(""), operator.truth("x"))

x = object()
y = object()
print(operator.is_(x, x), operator.is_(x, y))
print(operator.is_not(x, x), operator.is_not(x, y))
print(operator.is_none(None), operator.is_none(0), operator.is_none(False))
print(operator.is_not_none(None), operator.is_not_none(0))

# Sequence operations ############################################################
print(operator.concat([1, 2], [3, 4]))
print(operator.concat("ab", "cd"))
try:
    operator.concat(5, 3)
except TypeError as e:
    print("TypeError:", e)

print(operator.contains([1, 2, 3], 2))
print(operator.contains([1, 2, 3], 9))
print(operator.contains("hello", "ell"))

print(operator.countOf([1, 2, 1, 3, 1], 1))
print(operator.countOf([1, 2, 3], 9))
print(operator.indexOf([1, 2, 3, 2], 2))
try:
    operator.indexOf([1, 2, 3], 9)
except ValueError as e:
    print("ValueError:", e)

print(operator.index(7))
print(operator.getitem([10, 20, 30], 1))
d = {"a": 1}
operator.setitem(d, "b", 2)
print(sorted(d.items()))
operator.delitem(d, "a")
print(sorted(d.items()))

print(operator.length_hint([1, 2, 3]))
print(operator.length_hint([1, 2, 3], 99))

class HintOnly:
    def __length_hint__(self):
        return 42

print(operator.length_hint(HintOnly()))
print(operator.length_hint(object(), 7))

print(operator.call(str.upper, "abc"))
print(operator.call(operator.add, 4, 5))

# attrgetter, itemgetter, methodcaller ###########################################
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

class Line:
    def __init__(self, a, b):
        self.a = a
        self.b = b

p = Point(3, 4)
line = Line(Point(0, 0), Point(1, 1))

get_x = operator.attrgetter("x")
print(get_x(p))

get_both = operator.attrgetter("x", "y")
print(get_both(p))

get_dotted = operator.attrgetter("a.x", "b.y")
print(get_dotted(line))

get_mixed = operator.attrgetter("a.x", "b")
ax, b = get_mixed(line)
print(ax, b.x, b.y)

try:
    operator.attrgetter(5)
except TypeError as e:
    print("TypeError:", e)

items = [10, 20, 30, 40, 50]
get_1 = operator.itemgetter(1)
print(get_1(items))

get_1_3 = operator.itemgetter(1, 3)
print(get_1_3(items))

by_key = operator.itemgetter("name")
print(by_key({"name": "ok", "age": 3}))

words = ["banana", "kiwi", "fig", "apple"]
print(sorted(words, key=operator.itemgetter(0)))
print([p.x for p in sorted([Point(3, 1), Point(1, 2)], key=operator.attrgetter("x"))])

# `methodcaller` AGAINST A USER-DEFINED CLASS ONLY -- see the module
# docstring: `getattr(obj, name)` cannot hand back a BUILTIN type's method as
# a bound value on this compiler yet, so `methodcaller('upper')('hi')`,
# which the spec covers for any object, is not exercised against `str`.
class Greeter:
    def __init__(self, name):
        self.name = name

    def hello(self, punct="!"):
        return "hi " + self.name + punct

greet = operator.methodcaller("hello")
print(greet(Greeter("sam")))

greet_q = operator.methodcaller("hello", "?")
print(greet_q(Greeter("sam")))

kw = operator.methodcaller("hello", punct="...")
print(kw(Greeter("sam")))

try:
    operator.methodcaller(5)
except TypeError as e:
    print("TypeError:", e)

# In-place operations, against an int (rebinds) and a list (mutates) ############
n = 5
print(operator.iadd(n, 3), n)
print(operator.isub(n, 1), n)
print(operator.imul(n, 2), n)
print(operator.itruediv(n, 4), n)
print(operator.ifloordiv(n, 2), n)
print(operator.imod(n, 3), n)
print(operator.ipow(n, 2), n)
print(operator.ilshift(n, 2), n)
print(operator.irshift(n, 1), n)
print(operator.iand(n, 6), n)
print(operator.ior(n, 1), n)
print(operator.ixor(n, 3), n)

lst = [1, 2]
before = lst
result = operator.iadd(lst, [3, 4])
print(result, lst, result is before)

lst2 = [1, 2, 3]
before2 = lst2
result2 = operator.iconcat(lst2, [4])
print(result2, lst2, result2 is before2)

print(operator.imatmul(Grid("x"), Grid("y")))

try:
    operator.iconcat(5, [1])
except TypeError as e:
    print("TypeError:", e)

# Dunder aliases match their plain-named counterparts ############################
print(operator.__add__(2, 3) == operator.add(2, 3))
print(operator.__sub__(9, 4) == operator.sub(9, 4))
print(operator.__mul__(3, 3) == operator.mul(3, 3))
print(operator.__eq__(2, 2) == operator.eq(2, 2))
print(operator.__lt__(1, 2) == operator.lt(1, 2))
print(operator.__and__(6, 3) == operator.and_(6, 3))
print(operator.__or__(6, 3) == operator.or_(6, 3))
print(operator.__xor__(6, 3) == operator.xor(6, 3))
print(operator.__neg__(5) == operator.neg(5))
print(operator.__abs__(-5) == operator.abs(-5))
print(operator.__invert__(5) == operator.invert(5))
print(operator.__not__(0) == operator.not_(0))
print(operator.__getitem__([1, 2, 3], 1) == operator.getitem([1, 2, 3], 1))
print(operator.__contains__([1, 2, 3], 2) == operator.contains([1, 2, 3], 2))
print(operator.__iadd__(2, 3) == operator.iadd(2, 3))
print(operator.__concat__([1], [2]) == operator.concat([1], [2]))
print(operator.__call__(str.upper, "z") == operator.call(str.upper, "z"))

# A BUILTIN REACHED AS A VALUE CARRIES ITS KEYWORDS. The value form is a
# synthesised thunk whose body is the call the frontend would have emitted,
# and a thunk with no keyword slot dropped them SILENTLY -- `forward(dict,
# a=1)` answered `{}` and `forward(sorted, xs, reverse=True)` ignored the
# reversal. Both are wrong answers with nothing to mark them.
def forward(fn, *args, **kw):
    return fn(*args, **kw)


rows = [("b", 2), ("a", 3), ("c", 1)]
print(forward(dict, a=1, b=2))
print(forward(dict, rows))
print(forward(dict, rows, z=9))
print(forward(dict))
print(forward(sorted, [3, 1, 2]))
print(forward(sorted, [3, 1, 2], reverse=True))
print(forward(sorted, rows, key=lambda r: r[1]))
print(forward(sorted, rows, key=lambda r: r[1], reverse=True))
print(forward(min, rows, key=lambda r: r[1]),
      forward(max, rows, key=lambda r: r[1]))

# AND A `**` SPLAT WRITTEN OUT names its keywords at run time, which the
# analysis pass used to refuse by naming `None` as the offending keyword.
opts = {"reverse": True}
print(sorted([3, 1, 2], **opts))
print(sorted([3, 1, 2], **opts, key=lambda v: -v))
print(dict(rows, **{"q": 5}))
maker = dict
print(maker(a=1), maker(rows), maker(rows, a=1))
