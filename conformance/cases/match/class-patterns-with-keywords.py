# tier: spec
# ref: reference/compound_stmts.html#class-patterns
# expect:
# origin
# on-y:5
# diagonal
# point
# other
class Point:
    __match_args__ = ("x", "y")
    def __init__(self, x, y):
        self.x, self.y = x, y

def f(p):
    match p:
        case Point(x=0, y=0):
            return "origin"
        case Point(0, y):
            return f"on-y:{y}"
        case Point(x, y) if x == y:
            return "diagonal"
        case Point():
            return "point"
        case _:
            return "other"

print(f(Point(0, 0)))
print(f(Point(0, 5)))
print(f(Point(2, 2)))
print(f(Point(1, 2)))
print(f(42))
