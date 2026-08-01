# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [3, 2]
# [2, 1, 3]
xs = [3, 1, 2]
print(xs[::2])
print(xs[::-1])
