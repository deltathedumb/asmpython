# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# two
# True
xs = [1, 'two', 3.5, True, None]
print(min(xs, key=repr))
print(max(xs, key=repr))
