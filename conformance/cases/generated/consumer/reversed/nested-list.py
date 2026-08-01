# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [[4, 5, 6], [3], [1, 2]]
xs = [[1, 2], [3], [4, 5, 6]]
print(list(reversed(xs)))
