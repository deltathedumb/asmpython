# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# 1
xs = [(1, 'a'), (2, 'b')]
print(xs.index(xs[0]))
print(xs.index(xs[-1]))
