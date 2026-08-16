# probes: a dataclass compares by field values
# expect:
# True
# False
import dataclasses


@dataclasses.dataclass
class Point:
    x: int
    y: int


print(Point(1, 2) == Point(1, 2))
print(Point(1, 2) == Point(2, 1))
