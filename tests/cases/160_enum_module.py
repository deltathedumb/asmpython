# expect:
# 1
# 2
# 3
# True
# False

from enum import IntEnum, auto

class Color(IntEnum):
    RED = 1
    GREEN = 2
    BLUE = 3

print(Color.RED)
print(Color.GREEN)
print(Color.BLUE)
print(Color.RED == 1)
print(Color.RED == 2)
