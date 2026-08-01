# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 2
# [1, 2]
xs = [3, 1, 2]
ys = list(xs)
ys.remove(xs[0])
print(len(ys))
print(ys)
