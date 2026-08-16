# probes: typing.NamedTuple's class form works
# expect:
# 1
# 0
# (1, 0)
import typing


class Point(typing.NamedTuple):
    x: int
    y: int = 0


p = Point(1)
print(p.x)
print(p.y)
print(tuple(p))
