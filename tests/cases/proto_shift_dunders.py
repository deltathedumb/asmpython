# probes: __lshift__/__rshift__ serve << and >>
# expect:
# 16
# 4
class Bits:
    def __init__(self, n):
        self.n = n

    def __lshift__(self, by):
        return Bits(self.n << by)

    def __rshift__(self, by):
        return Bits(self.n >> by)


print((Bits(1) << 4).n)
print((Bits(16) >> 2).n)
