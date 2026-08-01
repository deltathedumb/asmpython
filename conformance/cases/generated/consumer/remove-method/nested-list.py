# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 2
# [[3], [4, 5, 6]]
xs = [[1, 2], [3], [4, 5, 6]]
ys = list(xs)
ys.remove(xs[0])
print(len(ys))
print(ys)
