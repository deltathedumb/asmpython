# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# 2
xs = ['c', 'a', 'b']
print(xs.index(xs[0]))
print(xs.index(xs[-1]))
