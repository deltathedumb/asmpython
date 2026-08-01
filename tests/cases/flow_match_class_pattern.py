# probes: a class pattern matches by type and attribute
# expect:
# origin
# 1/2
# not a point
class Point:
    __match_args__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


def describe(value):
    match value:
        case Point(0, 0):
            return "origin"
        case Point(x, y):
            return str(x) + "/" + str(y)
        case _:
            return "not a point"


print(describe(Point(0, 0)))
print(describe(Point(1, 2)))
print(describe("x"))
