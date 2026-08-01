# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [3.5, 1.5, 2.5]
# True
xs = [3.5, 1.5, 2.5]
ys = list(xs)
print(ys)
print(ys == xs)
