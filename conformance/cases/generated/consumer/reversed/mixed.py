# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [None, True, 3.5, 'two', 1]
xs = [1, 'two', 3.5, True, None]
print(list(reversed(xs)))
