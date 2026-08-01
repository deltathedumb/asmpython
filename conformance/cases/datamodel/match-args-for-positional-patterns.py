# tier: spec
# ref: reference/datamodel.html#object.__match_args__
# expect:
# origin
# on-x:3
# at:1,2
class Point:
    __match_args__ = ("x", "y")
    def __init__(self, x, y):
        self.x = x
        self.y = y

def describe(p):
    match p:
        case Point(0, 0):
            return "origin"
        case Point(x, 0):
            return f"on-x:{x}"
        case Point(x, y):
            return f"at:{x},{y}"

print(describe(Point(0, 0)))
print(describe(Point(3, 0)))
print(describe(Point(1, 2)))
