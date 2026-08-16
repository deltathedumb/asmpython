# probes: __slots__ refuses an undeclared attribute
# expect:
# refused
class Point:
    __slots__ = ("x",)

    def __init__(self):
        self.x = 1


p = Point()
try:
    p.z = 3
    print("accepted")
except AttributeError:
    print("refused")
