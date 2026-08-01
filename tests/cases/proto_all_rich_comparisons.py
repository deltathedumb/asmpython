# probes: each rich comparison dunder is reachable
# expect:
# True
# True
# True
# False
class Num:
    def __init__(self, n):
        self.n = n

    def __lt__(self, other):
        return self.n < other.n

    def __le__(self, other):
        return self.n <= other.n

    def __gt__(self, other):
        return self.n > other.n

    def __ge__(self, other):
        return self.n >= other.n


print(Num(1) < Num(2))
print(Num(2) <= Num(2))
print(Num(3) > Num(2))
print(Num(2) >= Num(3))
