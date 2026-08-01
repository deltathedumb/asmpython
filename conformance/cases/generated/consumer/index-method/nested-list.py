# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# 2
xs = [[1, 2], [3], [4, 5, 6]]
print(xs.index(xs[0]))
print(xs.index(xs[-1]))
