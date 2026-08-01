# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 3.5, None]
# [None, True, 3.5, 'two', 1]
xs = [1, 'two', 3.5, True, None]
print(xs[::2])
print(xs[::-1])
