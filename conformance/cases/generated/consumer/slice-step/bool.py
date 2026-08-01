# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [True, True]
# [True, False, True]
xs = [True, False, True]
print(xs[::2])
print(xs[::-1])
