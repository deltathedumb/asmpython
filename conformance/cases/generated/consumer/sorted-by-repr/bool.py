# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [False, True, True]
xs = [True, False, True]
print(sorted(xs, key=repr))
