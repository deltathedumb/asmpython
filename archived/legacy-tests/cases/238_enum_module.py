# expect:
# 0
# 2
# 1
# 3

import enum

class Direction(enum.IntEnum):
    NORTH: int = 0
    SOUTH: int = 1
    EAST: int = 2
    WEST: int = 3

print(Direction.NORTH)
print(Direction.EAST)

class Priority(enum.Enum):
    LOW: int = 1
    MEDIUM: int = 2
    HIGH: int = 3

print(Priority.LOW)
print(Priority.HIGH)
