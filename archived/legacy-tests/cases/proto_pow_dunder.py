# probes: __pow__ serves the ** operator
# expect:
# 1024
class Num:
    def __init__(self, n):
        self.n = n

    def __pow__(self, other):
        return Num(self.n ** other)


print((Num(2) ** 10).n)
