# probes: __truediv__/__floordiv__/__mod__ serve / // %
# expect:
# 3.5
# 3
# 1
class Num:
    def __init__(self, n):
        self.n = n

    def __truediv__(self, other):
        return Num(self.n / other)

    def __floordiv__(self, other):
        return Num(self.n // other)

    def __mod__(self, other):
        return Num(self.n % other)


print((Num(7) / 2).n)
print((Num(7) // 2).n)
print((Num(7) % 2).n)
