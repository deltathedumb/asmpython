# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [3.5, 2.5]
# [2.5, 1.5, 3.5]
xs = [3.5, 1.5, 2.5]
print(xs[::2])
print(xs[::-1])
