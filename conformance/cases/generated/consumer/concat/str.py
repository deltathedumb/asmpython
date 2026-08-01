# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# ['c', 'a', 'b', 'c']
xs = ['c', 'a', 'b']
print(xs + xs[:1])
