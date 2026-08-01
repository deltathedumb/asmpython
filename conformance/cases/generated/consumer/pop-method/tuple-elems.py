# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (2, 'b')
# 1
xs = [(1, 'a'), (2, 'b')]
ys = list(xs)
print(ys.pop())
print(len(ys))
