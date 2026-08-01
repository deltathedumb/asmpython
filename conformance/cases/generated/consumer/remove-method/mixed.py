# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 4
# ['two', 3.5, True, None]
xs = [1, 'two', 3.5, True, None]
ys = list(xs)
ys.remove(xs[0])
print(len(ys))
print(ys)
