# probes: a property coexists with __slots__
# expect:
# 4
class Point:
    __slots__ = ("_x",)

    def __init__(self, x):
        self._x = x

    @property
    def x(self):
        return self._x


print(Point(4).x)
