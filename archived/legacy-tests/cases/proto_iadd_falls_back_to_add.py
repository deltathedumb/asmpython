# probes: += falls back to __add__ when __iadd__ is absent
# expect:
# 3
# False
class Count:
    def __init__(self, n):
        self.n = n

    def __add__(self, other):
        return Count(self.n + other)


c = Count(1)
original = c
c += 2
print(c.n)
print(c is original)
