# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 2, 3]
xs = [3, 1, 2]
print(sorted(xs, key=repr))
