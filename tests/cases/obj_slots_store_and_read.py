# probes: a __slots__ attribute stores and reads back
# expect:
# 1
# 2
class Point:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


p = Point(1, 2)
print(p.x)
print(p.y)
