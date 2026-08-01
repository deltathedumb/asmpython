# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
# True
xs = [[1, 2], [3], [4, 5, 6]]
print(xs[0] in xs)
print(xs[-1] in xs)
