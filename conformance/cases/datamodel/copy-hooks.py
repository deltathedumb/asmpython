# tier: spec
# ref: library/copy.html
# expect:
# [1, 2, 3]
# [1, 2]
# True False
import copy

class C:
    def __init__(self, xs):
        self.xs = xs

c = C([1, 2])
shallow = copy.copy(c)
deep = copy.deepcopy(c)
c.xs.append(3)
print(shallow.xs)
print(deep.xs)
print(shallow.xs is c.xs, deep.xs is c.xs)
