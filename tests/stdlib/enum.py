# COVERAGE: Enum members and their name/value/repr/str, lookup by value and by
# name, iteration in definition order, __members__ including aliases, aliases
# being the SAME object, auto() in an enum and in a flag, methods in an enum
# body staying methods, unique(), IntEnum comparison and arithmetic and its
# 3.11 __str__, StrEnum, Flag combination / iteration / containment /
# inversion, and IntFlag. NOT covered here: ReprEnum as a base, verify and
# EnumCheck, boundary=, global_enum, member/nonmember, _missing_, pickling and
# functional creation -- the module declares it has none of them.
#
# The one DECLARED divergence is not tested for sameness: CPython's IntEnum
# members are real ints and these are not, so `isinstance(Num.ONE, int)` is
# True there and False here. Everything a program does WITH one -- compare,
# add, sort, index, print -- is tested and must agree.
import enum

class Colour(enum.Enum):
    RED = 1
    GREEN = 2
    BLUE = 3

    def describe(self):
        return "%s is %d" % (self.name, self.value)


print(Colour.RED, repr(Colour.RED))
print(Colour.RED.name, Colour.RED.value)
print(Colour(2), Colour["BLUE"])
print(list(Colour))
print(len(Colour), Colour.RED in Colour)
print([c.name for c in Colour], [c.value for c in Colour])
print(Colour.RED is Colour(1), Colour.RED == Colour.RED, Colour.RED == Colour.GREEN)
print(type(Colour.RED) is Colour, isinstance(Colour.RED, Colour))
# A METHOD IN THE BODY STAYS A METHOD, which is the reason members are chosen
# by what they are rather than by everything the body binds.
print(Colour.BLUE.describe())
print(repr(Colour))

try:
    Colour(9)
except ValueError as exc:
    print("ValueError:", exc)
try:
    Colour["PUCE"]
except KeyError:
    print("KeyError for an unknown name")


# ---- aliases ---------------------------------------------------------------
class Shade(enum.Enum):
    RED = 1
    CRIMSON = 1
    BLUE = 2


# AN ALIAS IS THE SAME OBJECT and is not iterated, but IS in __members__.
print(Shade.CRIMSON is Shade.RED, Shade.CRIMSON.name)
print(list(Shade), len(Shade))
print(sorted(Shade.__members__), len(Shade.__members__))
print(Shade.__members__["CRIMSON"] is Shade.RED)


# ---- auto ------------------------------------------------------------------
class Step(enum.Enum):
    ONE = enum.auto()
    TWO = enum.auto()
    TEN = 10
    ELEVEN = enum.auto()


print([(s.name, s.value) for s in Step])


# ---- unique ----------------------------------------------------------------
@enum.unique
class Tidy(enum.Enum):
    A = 1
    B = 2


print("unique accepted", list(Tidy))

try:
    @enum.unique
    class Messy(enum.Enum):
        A = 1
        B = 1
except ValueError as exc:
    print("unique refused:", exc)


# ---- IntEnum ---------------------------------------------------------------
class Num(enum.IntEnum):
    ONE = 1
    TWO = 2
    THREE = 3


print(repr(Num.ONE), str(Num.ONE))
print(Num.ONE == 1, Num.ONE != 2, Num.TWO > Num.ONE, Num.ONE < 2)
print(Num.ONE + 1, 1 + Num.ONE, Num.THREE - 1, 10 - Num.THREE)
print(Num.TWO * 3, 3 * Num.TWO)
print(int(Num.THREE), sorted([Num.THREE, Num.ONE, Num.TWO]))
print([n.value for n in sorted([Num.THREE, Num.ONE, Num.TWO])])
print("%d" % (Num.TWO,), "%s" % (Num.TWO,))
print(bool(Num.ONE))
# INDEXING WITH ONE, which is what `__index__` is for.
print(["a", "b", "c", "d"][Num.TWO])
print(Num.ONE in Num, Num(3) is Num.THREE)


# ---- StrEnum ---------------------------------------------------------------
class Colours(enum.StrEnum):
    RED = "red"
    BLUE = "blue"


print(repr(Colours.RED), str(Colours.RED))
print(Colours.RED == "red", Colours.RED != "blue")
print(Colours.RED + "!", "the " + Colours.BLUE)
print(len(Colours.BLUE), sorted([Colours.RED, Colours.BLUE]))
print("%s" % (Colours.RED,))
print(Colours("blue") is Colours.BLUE)


# ---- Flag ------------------------------------------------------------------
class Perm(enum.Flag):
    R = enum.auto()
    W = enum.auto()
    X = enum.auto()


# `auto()` IN A FLAG IS THE NEXT BIT, so combinations cannot collide.
print([(p.name, p.value) for p in Perm])

both = Perm.R | Perm.W
print(repr(both), str(both), both.value)
print(list(both), [p.name for p in both])
print(Perm.R in both, Perm.X in both)
print((Perm.R | Perm.W) is (Perm.R | Perm.W))
print(both == Perm.R | Perm.W, both == Perm.R)
print((both & Perm.R).name, (both ^ Perm.R).name)
print(repr(~Perm.R), (~Perm.R).value)
print(bool(Perm.R), bool(Perm.R & Perm.W))
print(Perm(3) is both, Perm(1) is Perm.R)
# THAT it is refused is compared and not the WORDING: CPython's message for a
# flag out of range is three lines of binary showing which bits were allowed,
# and the module declares it does not reproduce that text.
try:
    Perm(8)
    print("ACCEPTED a bit nobody declared")
except ValueError:
    print("ValueError for a bit nobody declared")
# A PLAIN FLAG IS NOT ITS NUMBER, which is the whole difference from IntFlag.
print(Perm.R == 1)


# ---- IntFlag ---------------------------------------------------------------
class Mode(enum.IntFlag):
    READ = 1
    WRITE = 2
    EXEC = 4


print(repr(Mode.READ), str(Mode.READ))
print(Mode.READ == 1, Mode.READ | Mode.WRITE == 3)
print(int(Mode.READ | Mode.EXEC))
print(repr(Mode.READ | Mode.WRITE))
print([m.name for m in (Mode.READ | Mode.EXEC)])
print(Mode(5).value, [m.name for m in Mode(5)])
print("%d" % (Mode.WRITE,))

print("done")
