# tier: spec
# ref: reference/datamodel.html#object.__lt__
# expect:
# True True False False False True
# 1
class Cmp:
    def __init__(self, v):
        self.v = v
    def __lt__(self, o): return self.v < o.v
    def __le__(self, o): return self.v <= o.v
    def __gt__(self, o): return self.v > o.v
    def __ge__(self, o): return self.v >= o.v
    def __eq__(self, o): return self.v == o.v
    def __ne__(self, o): return self.v != o.v

a, b = Cmp(1), Cmp(2)
print(a < b, a <= b, a > b, a >= b, a == b, a != b)
print(sorted([Cmp(2), Cmp(1)], key=lambda c: c.v)[0].v)
