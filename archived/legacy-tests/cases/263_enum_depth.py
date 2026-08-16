# expect:
# 1
# 2
# 3
# 1
# 3
# 0
# 1
# 2
# 3

import enum

class Color(enum.Enum):
    RED: int = 1
    GREEN: int = 2
    BLUE: int = 3

print(Color.RED)
print(Color.GREEN)
print(Color.BLUE)

class Status(enum.IntEnum):
    OK: int = 1
    WARN: int = 2
    ERROR: int = 3

print(Status.OK)
print(Status.ERROR)

class Perm(enum.Flag):
    NONE: int = 0
    READ: int = 1
    WRITE: int = 2
    EXEC: int = 3

print(Perm.NONE)
print(Perm.READ)
print(Perm.WRITE)
print(Perm.EXEC)
