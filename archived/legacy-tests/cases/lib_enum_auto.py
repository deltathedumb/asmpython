# expect:
# 1 2
from enum import Enum, auto
class C(Enum):
    X = auto()
    Y = auto()
print(C.X.value, C.Y.value)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
