# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [[1, 2], [4, 5, 6]]
# [[4, 5, 6], [3], [1, 2]]
xs = [[1, 2], [3], [4, 5, 6]]
print(xs[::2])
print(xs[::-1])
