# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# a
# c
xs = ['c', 'a', 'b']
print(min(xs, key=repr))
print(max(xs, key=repr))
