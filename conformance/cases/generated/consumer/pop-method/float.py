# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 2.5
# 2
xs = [3.5, 1.5, 2.5]
ys = list(xs)
print(ys.pop())
print(len(ys))
