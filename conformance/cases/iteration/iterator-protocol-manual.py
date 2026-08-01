# tier: spec
# ref: reference/datamodel.html#object.__iter__
# expect:
# [1, 2, 3]
# 1
# StopIteration
class Count:
    def __init__(self, n):
        self.n = n
        self.i = 0
    def __iter__(self):
        return self
    def __next__(self):
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i

print(list(Count(3)))
it = Count(1)
print(next(it))
try:
    next(it)
except StopIteration:
    print("StopIteration")
