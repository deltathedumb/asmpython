# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# ['c', 'b']
# ['b', 'a', 'c']
xs = ['c', 'a', 'b']
print(xs[::2])
print(xs[::-1])
