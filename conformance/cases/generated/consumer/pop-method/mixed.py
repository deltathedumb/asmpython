# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# None
# 4
xs = [1, 'two', 3.5, True, None]
ys = list(xs)
print(ys.pop())
print(len(ys))
