# probes: __sub__ and __mul__ serve - and *
# expect:
# 3
# 15
class Vec:
    def __init__(self, n):
        self.n = n

    def __sub__(self, other):
        return Vec(self.n - other.n)

    def __mul__(self, factor):
        return Vec(self.n * factor)


print((Vec(5) - Vec(2)).n)
print((Vec(5) * 3).n)
