# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [True, False, True]
# True
xs = [True, False, True]
ys = list(xs)
print(ys)
print(ys == xs)
