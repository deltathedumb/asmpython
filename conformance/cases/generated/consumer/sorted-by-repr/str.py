# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# ['a', 'b', 'c']
xs = ['c', 'a', 'b']
print(sorted(xs, key=repr))
