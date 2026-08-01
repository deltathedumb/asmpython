# tier: spec
# ref: reference/datamodel.html#object.__hash__
# expect:
# True True
# 1
# v
class Point:
    def __init__(self, x):
        self.x = x
    def __eq__(self, o):
        return isinstance(o, Point) and self.x == o.x
    def __hash__(self):
        return hash(self.x)

a, b = Point(1), Point(1)
print(a == b, hash(a) == hash(b))
s = {a, b}
print(len(s))
d = {a: "v"}
print(d[b])
