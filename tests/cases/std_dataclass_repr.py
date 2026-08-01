# probes: a dataclass gets a generated repr
# expect:
# Point(x=1, y=2)
import dataclasses


@dataclasses.dataclass
class Point:
    x: int
    y: int


print(repr(Point(1, 2)))
