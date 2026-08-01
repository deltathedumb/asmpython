# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 'two', 3.5, True, None, 1]
xs = [1, 'two', 3.5, True, None]
print(xs + xs[:1])
