# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# []
# True
xs = []
ys = list(xs)
print(ys)
print(ys == xs)
