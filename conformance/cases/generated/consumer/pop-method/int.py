# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 2
# 2
xs = [3, 1, 2]
ys = list(xs)
print(ys.pop())
print(len(ys))
