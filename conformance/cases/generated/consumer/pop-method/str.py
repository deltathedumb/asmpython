# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b
# 2
xs = ['c', 'a', 'b']
ys = list(xs)
print(ys.pop())
print(len(ys))
