# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# ['c', 'a', 'b']
# True
xs = ['c', 'a', 'b']
ys = list(xs)
print(ys)
print(ys == xs)
