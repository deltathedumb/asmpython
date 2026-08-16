# probes: a __slots__ instance has no __dict__
# expect:
# False
class Point:
    __slots__ = ("x",)


print(hasattr(Point(), "__dict__"))
