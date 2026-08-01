# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [[1, 2], [3], [4, 5, 6]]
# True
xs = [[1, 2], [3], [4, 5, 6]]
ys = list(xs)
print(ys)
print(ys == xs)
