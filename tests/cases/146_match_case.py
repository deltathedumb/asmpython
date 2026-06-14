# expect:
# one
# got two or three
# got two or three
# big: 100
# unknown: 99
# second: b
# rest: [3, 4, 5]
# first: 10 last: 40
# Dog named Rex
# Point(1, 2)
# guarded even: 4
# no match
# as-bound: 42

# Literal patterns
x = 1
match x:
    case 1:
        print("one")
    case 2:
        print("two")

# Or-patterns
y = 2
match y:
    case 2 | 3:
        print("got two or three")

y = 3
match y:
    case 2 | 3:
        print("got two or three")

# Capture pattern
z = 100
match z:
    case 99:
        print("exactly 99")
    case n:
        print("big:", n)

# Wildcard
w = 99
match w:
    case 100:
        print("hundred")
    case _:
        print("unknown:", w)

# Sequence patterns
lst = [1, 2, 3, 4, 5]
match lst:
    case [1, second, *rest]:
        print("second:", second)
        print("rest:", rest)

lst2 = [10, 20, 30, 40]
match lst2:
    case [first, *_, last]:
        print("first:", first, "last:", last)

# Class patterns with __match_args__
class Point:
    __match_args__ = ("x", "y")
    def __init__(self, px: int, py: int):
        self.x = px
        self.y = py
    def __str__(self) -> str:
        return "Point(" + str(self.x) + ", " + str(self.y) + ")"

class Dog:
    __match_args__ = ("name",)
    def __init__(self, n: str):
        self.name = n

d = Dog("Rex")
match d:
    case Dog(name=n):
        print("Dog named", n)

p = Point(1, 2)
match p:
    case Point(x=px, y=py):
        print("Point(" + str(px) + ", " + str(py) + ")")

# Guard (if clause)
val = 4
match val:
    case n if n % 2 == 0:
        print("guarded even:", n)
    case _:
        print("odd")

# No match (falls through all cases)
val2 = 7
match val2:
    case 1:
        print("one")
    case 2:
        print("two")
    case 3:
        print("three")

print("no match")

# `case pattern as name`
val3 = 42
match val3:
    case n as bound:
        print("as-bound:", bound)
