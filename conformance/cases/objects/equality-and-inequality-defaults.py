# tier: spec
# ref: reference/datamodel.html#object.__eq__
# expect:
# True False True
# True False False
class Plain:
    pass

a, b = Plain(), Plain()
print(a == a, a == b, a != b)

class Eq:
    def __init__(self, v):
        self.v = v
    def __eq__(self, other):
        return isinstance(other, Eq) and self.v == other.v

print(Eq(1) == Eq(1), Eq(1) != Eq(1), Eq(1) == Eq(2))
