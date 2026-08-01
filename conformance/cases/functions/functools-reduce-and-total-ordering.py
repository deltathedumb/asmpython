# tier: spec
# ref: library/functools.html#functools.reduce
# expect:
# 6
# 16
# 0
# True True True
import functools

print(functools.reduce(lambda a, b: a + b, [1, 2, 3]))
print(functools.reduce(lambda a, b: a + b, [1, 2, 3], 10))
print(functools.reduce(lambda a, b: a + b, [], 0))

@functools.total_ordering
class V:
    def __init__(self, n):
        self.n = n
    def __eq__(self, o):
        return self.n == o.n
    def __lt__(self, o):
        return self.n < o.n

print(V(1) < V(2), V(2) >= V(1), V(1) <= V(1))
