# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [4, 5, 6]
# 2
xs = [[1, 2], [3], [4, 5, 6]]
ys = list(xs)
print(ys.pop())
print(len(ys))
