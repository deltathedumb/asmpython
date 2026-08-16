# guards: iter_next_compat_fixes
# expect:
# 10
# 20
# a
# 1
# 2
# 6
class Counter:
    def __init__(self, limit):
        self.limit = limit
        self.n = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.n >= self.limit:
            raise StopIteration
        self.n = self.n + 1
        return self.n


it = iter([10, 20, 30])
print(next(it))
print(next(it))

si = iter("ab")
print(next(si))

ci = iter(Counter(2))
print(next(ci))
print(next(ci))

total = 0
for v in Counter(3):
    total = total + v
print(total)
