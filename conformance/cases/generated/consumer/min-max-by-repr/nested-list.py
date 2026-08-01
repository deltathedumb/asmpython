# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 2]
# [4, 5, 6]
xs = [[1, 2], [3], [4, 5, 6]]
print(min(xs, key=repr))
print(max(xs, key=repr))
