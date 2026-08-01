# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 2
# [1.5, 2.5]
xs = [3.5, 1.5, 2.5]
ys = list(xs)
ys.remove(xs[0])
print(len(ys))
print(ys)
