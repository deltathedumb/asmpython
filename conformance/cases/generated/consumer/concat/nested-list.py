# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [[1, 2], [3], [4, 5, 6], [1, 2]]
xs = [[1, 2], [3], [4, 5, 6]]
print(xs + xs[:1])
