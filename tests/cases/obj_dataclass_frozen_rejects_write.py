# probes: a frozen dataclass refuses assignment
# expect:
# 1
# assignment refused
import dataclasses


@dataclasses.dataclass(frozen=True)
class Point:
    x: int


p = Point(1)
print(p.x)
try:
    p.x = 2
    print("assignment allowed")
except dataclasses.FrozenInstanceError:
    print("assignment refused")
