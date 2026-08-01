# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [True, False, True, True]
xs = [True, False, True]
print(xs + xs[:1])
