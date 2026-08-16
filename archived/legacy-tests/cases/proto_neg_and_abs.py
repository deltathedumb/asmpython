# probes: __neg__ and __abs__ serve unary - and abs()
# expect:
# -3
# 4
class Signed:
    def __init__(self, n):
        self.n = n

    def __neg__(self):
        return Signed(-self.n)

    def __abs__(self):
        return Signed(abs(self.n))


print((-Signed(3)).n)
print(abs(Signed(-4)).n)
