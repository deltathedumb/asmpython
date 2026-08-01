# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [3, 1, 2]
# True
xs = [3, 1, 2]
ys = list(xs)
print(ys)
print(ys == xs)
