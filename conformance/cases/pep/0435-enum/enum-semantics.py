# tier: spec
# ref: library/enum.html
# expect:
# RED 1
# 2
# True
# [<Color.RED: 1>, <Color.GREEN: 2>]
# False
# True 2
from enum import Enum, IntEnum, auto

class Color(Enum):
    RED = 1
    GREEN = auto()

print(Color.RED.name, Color.RED.value)
print(Color.GREEN.value)
print(Color(1) is Color.RED)
print(list(Color))
print(Color.RED == 1)

class Num(IntEnum):
    ONE = 1

print(Num.ONE == 1, Num.ONE + 1)
