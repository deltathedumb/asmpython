# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 2
# [False, True]
xs = [True, False, True]
ys = list(xs)
ys.remove(xs[0])
print(len(ys))
print(ys)
