# tier: spec
# ref: reference/datamodel.html#object.__index__
# expect:
# 2
# [2, 3]
# 0b10
# [0, 1]
class Idx:
    def __index__(self):
        return 2

xs = [0, 1, 2, 3]
print(xs[Idx()])
print(xs[Idx():])
print(bin(Idx()))
print(list(range(Idx())))
