# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
# 2
xs = [True, False, True]
ys = list(xs)
print(ys.pop())
print(len(ys))
