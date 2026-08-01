# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 2
# ['a', 'b']
xs = ['c', 'a', 'b']
ys = list(xs)
ys.remove(xs[0])
print(len(ys))
print(ys)
