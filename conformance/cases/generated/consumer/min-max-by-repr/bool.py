# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# False
# True
xs = [True, False, True]
print(min(xs, key=repr))
print(max(xs, key=repr))
