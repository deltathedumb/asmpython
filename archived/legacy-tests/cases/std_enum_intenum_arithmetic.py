# probes: IntEnum members behave as ints
# expect:
# 6
# True
import enum


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 5


print(Level.HIGH + 1)
print(Level.LOW < Level.HIGH)
