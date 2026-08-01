# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# ['two', 1, 3.5, None, True]
xs = [1, 'two', 3.5, True, None]
print(sorted(xs, key=repr))
