# probes: a < b reflects to b.__gt__(a)
# expect:
# True
# False
class OnlyGt:
    def __init__(self, n):
        self.n = n

    def __gt__(self, other):
        return self.n > other


print(1 < OnlyGt(5))
print(9 < OnlyGt(5))
